#!/usr/bin/env python3
"""
dtcsweep — read-only, multi-module DTC scanner for VW Group cars over
SocketCAN. Built for MQB / MQB-Evo platforms; tested on a 2026 VW Golf R (Mk8.5).

READ-ONLY: uses only TesterPresent (0x3E, discovery), ReadDataByIdentifier
(0x22), ReadDTCInformation (0x19) and the OBD read services 0x01/0x03/0x07/0x0A.
Nothing is cleared, coded, adapted or written.

WHAT IT DOES
  1. Discovery: pings every candidate diagnostic address on four schemes
       11-bit VAG      0x700-0x774  -> response +0x6A
       11-bit OBD      0x7E0-0x7E7  -> response +0x08
       29-bit OBD      0x18DAxxF1   -> 0x18DAF1xx   (ISO 15765-4, CAN 2.0B)
       29-bit VAG      0x17FC00xx   -> 0x17FE00xx   (CAN 2.0B)
     plus functional OBD "01 00" on 0x7DF and 0x18DB33F1.
     Pings are fire-and-collect, so the whole sweep takes a few seconds.
  2. For each module that answered: identity DIDs (name, part, SW, HW) and
     its DTC list, filtered to codes that have actually failed.
  3. For emissions ECUs: OBD Mode 03 (stored), 07 (pending), 0A (permanent).
  4. For a --target DTC, and for every active/confirmed DTC
     with --details: freeze-frame (19 04) and extended data (19 06).
  5. Summary table, plus a .log (plain text) and a .json (everything raw).

See README.md and docs/setup.md for hardware setup. Quick start (Linux):
  sudo slcand -o -c -s6 /dev/ttyACM0 can0 && sudo ip link set can0 up
  python3 dtcsweep.py

USEFUL OPTIONS
  --all            show every DTC a module lists, including never-failed ones
  --details        pull freeze-frame/extended data for all active/confirmed DTCs
  --target 369232  hunt one code (VW fault number, 0x05A250, or OBD U3001):
                   flag it and pull its detail records
  --schemes 11vag,29obd   limit which address schemes are swept
  --slow           also probe silent addresses with a real 19 02 request
  --fd             CAN FD frames (needs an FD-capable interface/firmware)
  --redact         mask the VIN in output (use before sharing logs)
  --out-dir DIR    where to write the .log and .json (default: current dir)
  --no-color       plain output

COMPARE TWO SCANS
  python3 dtcsweep.py diff OLD.json NEW.json

Ignition ON, engine off, battery maintainer recommended.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import can
    import isotp
except ImportError:
    sys.exit('Missing deps: pip install python-can "can-isotp>=2.0"')

if not hasattr(isotp.CanStack, "start"):
    sys.exit('This script needs can-isotp 2.x:  pip install -U "can-isotp>=2.0"')

__version__ = "0.1.0"

# ----------------------------------------------------------------- constants

PAD = 0x55
DEFAULT_MASK = 0x2D  # testFailed | pending | confirmed | testFailedSinceLastClear

SCHEMES = {
    "11vag": ("11-bit VAG", False, [(t, t + 0x6A) for t in range(0x700, 0x775)]),
    "11obd": ("11-bit OBD", False, [(t, t + 0x08) for t in range(0x7E0, 0x7E8)]),
    "29obd": ("29-bit OBD", True,
              [(0x18DA00F1 | (x << 8), 0x18DAF100 | x) for x in range(256) if x != 0xF1]),
    "29vag": ("29-bit VAG", True,
              [(0x17FC0000 | x, 0x17FE0000 | x) for x in range(256)]),
}

# Hints only (usual MQB convention). DID F197 read from the module is the truth.
KNOWN = {
    0x7E0: "01 Engine?", 0x7E1: "02 Transmission?", 0x713: "03 Brakes?",
    0x70E: "09 Central electrics?", 0x715: "15 Airbag?", 0x714: "17 Instruments?",
    0x710: "19 Gateway?", 0x712: "44 Steering?",
}

IDENT_DIDS = [
    (0xF197, "name"), (0xF187, "part"), (0xF189, "sw"),
    (0xF191, "hw"), (0xF19E, "odx"),
]

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported", 0x13: "incorrectMessageLength",
    0x14: "responseTooLong", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x31: "requestOutOfRange",
    0x33: "securityAccessDenied", 0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

STATUS_BITS = [
    (0x01, "failing now"), (0x02, "failed this cycle"), (0x04, "pending"),
    (0x08, "confirmed"), (0x10, "not tested since clear"),
    (0x20, "failed since clear"), (0x40, "not tested this cycle"),
    (0x80, "warning lamp"),
]


# ------------------------------------------------------------------- output

ANSI = re.compile(r"\x1b\[[0-9;]*m")
_color = sys.stdout.isatty()
_log = None


def paint(code, s):
    return f"\x1b[{code}m{s}\x1b[0m" if _color else s


def red(s): return paint("1;31", s)
def yellow(s): return paint("33", s)
def green(s): return paint("32", s)
def cyan(s): return paint("36", s)
def bold(s): return paint("1", s)
def dim(s): return paint("2", s)


def out(msg=""):
    print(msg)
    if _log:
        _log.write(ANSI.sub("", msg) + "\n")
        _log.flush()


def status_line(msg):
    """Transient progress line (terminal only, not logged)."""
    if sys.stdout.isatty():
        sys.stdout.write("\r\x1b[K" + msg)
        sys.stdout.flush()


def clear_status():
    if sys.stdout.isatty():
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()


def rule(title=""):
    width = 78
    if title:
        text = f"── {title} "
        out(bold(text + "─" * max(0, width - len(text))))
    else:
        out(dim("─" * width))


# ------------------------------------------------------------------ decoding

def dtc_str(b):
    """SAE-format code (e.g. U3001) for a 2-byte OBD DTC."""
    return f"{'PCBU'[b[0] >> 6]}{b[0] & 0x3F:02X}{b[1]:02X}"


def vw_number(b):
    """VW fault number for a 3-byte UDS DTC: the bytes read as one big-endian
    number, as VW tools show it (05 A2 50 -> 369232). VW modules don't use the
    SAE letter + failure-type layout, so this is the only meaning we can
    derive from the bytes themselves."""
    return str(int.from_bytes(bytes(b), "big"))


def parse_target(s):
    """--target: a VW fault number (369232), its hex (0x05A250), or an SAE
    code (U3001) to find in OBD lists. Returns {"raw": bytes} or {"sae": str}."""
    s = s.strip().upper()
    if re.fullmatch(r"\d{1,8}", s) and int(s) <= 0xFFFFFF:
        return {"raw": int(s).to_bytes(3, "big")}
    if re.fullmatch(r"0X[0-9A-F]{6}", s):
        return {"raw": bytes.fromhex(s[2:])}
    m = re.fullmatch(r"([PCBU][0-3][0-9A-F]{3})(?:[- ]?[0-9A-F]{2})?", s)
    if m:
        return {"sae": m.group(1)}
    sys.exit(f"Bad --target '{s}'. Use a VW fault number (369232), its hex "
             "(0x05A250), or an SAE code for OBD lists (U3001).")


def status_labels(s):
    return [label for bit, label in STATUS_BITS if s & bit]


def severity(s):
    if s & 0x01 or s & 0x80:
        return "active"
    if s & 0x0C:
        return "confirmed" if s & 0x08 else "pending"
    if s & 0x20:
        return "history"
    return "untested"


def sev_paint(sev, text):
    return {"active": red, "confirmed": yellow, "pending": yellow,
            "history": cyan, "untested": dim}[sev](text)


def printable(raw):
    text = raw.decode("latin-1", errors="replace")
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    return " ".join(text.split())


def fmt_id(arb, ext):
    return f"0x{arb:08X}" if ext else f"0x{arb:03X}"


# ---------------------------------------------------------------------- UDS

class UdsError(Exception):
    pass


class NoResponse(UdsError):
    pass


def make_stack(bus, mod, fd):
    mode = isotp.AddressingMode.Normal_29bits if mod["ext"] else isotp.AddressingMode.Normal_11bits
    addr = isotp.Address(mode, txid=mod["tx"], rxid=mod["rx"])
    params = {
        "can_fd": fd,
        "tx_data_length": 64 if fd else 8,
        "tx_padding": PAD,
        "rx_flowcontrol_timeout": 1000,
        "rx_consecutive_frame_timeout": 1000,
    }
    stack = isotp.CanStack(bus, address=addr, params=params, error_handler=lambda e: None)
    stack.start()
    return stack


def uds(stack, payload, timeout=1.5):
    sid = payload[0]
    stack.send(bytes(payload))
    deadline = time.time() + timeout
    pending_seen = False
    busy = 0
    while True:
        left = deadline - time.time()
        if left <= 0:
            raise UdsError("timed out after responsePending") if pending_seen else NoResponse("no response")
        data = stack.recv(block=True, timeout=left)
        if data is None:
            continue
        data = bytes(data)
        if len(data) >= 3 and data[0] == 0x7F and data[1] == sid:
            nrc = data[2]
            if nrc == 0x78:
                pending_seen = True
                deadline = time.time() + 5.0
                continue
            if nrc == 0x21 and busy < 3:
                busy += 1
                time.sleep(0.3)
                stack.send(bytes(payload))
                deadline = time.time() + timeout
                continue
            raise UdsError(f"NRC 0x{nrc:02X} {NRC.get(nrc, 'unknown')}")
        if data and data[0] == sid + 0x40:
            return data


# ---------------------------------------------------------------- discovery

def raw_frame(arb, ext, payload, fd):
    data = bytes([len(payload), *payload]).ljust(8, bytes([PAD]))
    return can.Message(arbitration_id=arb, data=data, is_extended_id=ext,
                       is_fd=fd, check=False)


def safe_send(bus, msg):
    for _ in range(20):
        try:
            bus.send(msg, timeout=0.2)
            return
        except can.CanError:
            time.sleep(0.02)  # tx queue full — back off


def discover(bus, scheme_keys, fd, listen=1.0):
    """Fire TesterPresent at every candidate; return {(ext, tx): module}."""
    found = {}
    expected = {}
    queue = []
    for key in scheme_keys:
        label, ext, pairs = SCHEMES[key]
        for tx, rx in pairs:
            expected[(ext, rx)] = (key, label, tx)
            queue.append((tx, ext))

    def absorb(msg):
        k = (msg.is_extended_id, msg.arbitration_id)
        if k not in expected or len(msg.data) < 3:
            return
        sid = msg.data[1]
        if sid == 0x7E or (sid == 0x7F and msg.data[2] == 0x3E) or (msg.data[0] & 0xF0) == 0x10:
            key, label, tx = expected[k]
            found.setdefault((k[0], tx), {
                "scheme": key, "scheme_label": label, "ext": k[0],
                "tx": tx, "rx": k[1], "obd": False,
            })

    def drain():
        while True:
            msg = bus.recv(timeout=0)
            if msg is None:
                return
            absorb(msg)

    total = len(queue)
    for i, (tx, ext) in enumerate(queue, 1):
        safe_send(bus, raw_frame(tx, ext, [0x3E, 0x00], fd))
        drain()
        time.sleep(0.003)
        if i % 16 == 0 or i == total:
            status_line(f"  Pinging addresses {i}/{total}  ·  {len(found)} answered")
    end = time.time() + listen
    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg is not None:
            absorb(msg)
    clear_status()
    return found


def discover_obd(bus, scheme_keys, fd, listen=1.0):
    """Functional 'Mode 01 PID 00' on 11- and 29-bit; returns {(ext, tx): module}."""
    found = {}
    if "11obd" in scheme_keys:
        safe_send(bus, raw_frame(0x7DF, False, [0x01, 0x00], fd))
    if "29obd" in scheme_keys:
        safe_send(bus, raw_frame(0x18DB33F1, True, [0x01, 0x00], fd))
    end = time.time() + listen
    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg is None or len(msg.data) < 3 or msg.data[1] != 0x41:
            continue
        arb, ext = msg.arbitration_id, msg.is_extended_id
        if not ext and 0x7E8 <= arb <= 0x7EF:
            tx, key = arb - 8, "11obd"
        elif ext and (arb & 0xFFFFFF00) == 0x18DAF100:
            tx, key = 0x18DA00F1 | ((arb & 0xFF) << 8), "29obd"
        else:
            continue
        found[(ext, tx)] = {"scheme": key, "scheme_label": SCHEMES[key][0], "ext": ext,
                            "tx": tx, "rx": arb, "obd": True}
    return found


def functional_pid(bus, scheme_keys, fd, pid, listen=0.6, nbytes=2):
    """Functional OBD Mode 01 request. Returns the raw big-endian value of the
    first `nbytes` data bytes from every ECU that answered."""
    sent = False
    if "11obd" in scheme_keys:
        safe_send(bus, raw_frame(0x7DF, False, [0x01, pid], fd))
        sent = True
    if "29obd" in scheme_keys:
        safe_send(bus, raw_frame(0x18DB33F1, True, [0x01, pid], fd))
        sent = True
    values = []
    end = time.time() + listen if sent else 0
    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg is None or len(msg.data) < 3 + nbytes:
            continue
        if msg.data[1] == 0x41 and msg.data[2] == pid:
            values.append(int.from_bytes(bytes(msg.data[3:3 + nbytes]), "big"))
    return values


def read_battery(bus, scheme_keys, fd):
    """Control module voltage (PID 42), median across ECUs. Volts or None."""
    readings = sorted(v / 1000 for v in functional_pid(bus, scheme_keys, fd, 0x42))
    return readings[len(readings) // 2] if readings else None


def read_rpm(bus, scheme_keys, fd):
    """Engine speed (PID 0C), highest reply. rpm or None if no ECU reports it."""
    readings = functional_pid(bus, scheme_keys, fd, 0x0C)
    return max(readings) / 4 if readings else None


def engine_running(rpm):
    return rpm is not None and rpm >= 300


def read_distances(bus, scheme_keys, fd):
    """Odometer (PID A6) and the check engine light / codes-cleared counters
    (PIDs 21, 4D, 31, 4E). Highest reply for each; None if no ECU reports it.
    Also whether any ECU requests the light right now (PID 01, bit A7), since
    the "light on" counters keep their last value after the light goes off."""
    def top(pid, nbytes=2):
        vals = functional_pid(bus, scheme_keys, fd, pid, nbytes=nbytes)
        return max(vals) if vals else None
    status = functional_pid(bus, scheme_keys, fd, 0x01, nbytes=1)
    odo = top(0xA6, 4)
    return {"odometer_km": odo / 10 if odo is not None else None,
            "mil_on": any(a & 0x80 for a in status) if status else None,
            "mil_km": top(0x21), "mil_min": top(0x4D),
            "cleared_km": top(0x31), "cleared_min": top(0x4E)}


def km_text(km, decimals=0):
    return f"{km:,.{decimals}f} km ({km * 0.621371:,.{decimals}f} mi)"


def counter_text(km, minutes):
    parts = []
    if km is not None:
        parts.append(km_text(km))
    if minutes is not None:
        parts.append(f"{minutes:,} min")
    return ", ".join(parts)


def print_distances(d):
    if d["odometer_km"] is None:
        out(dim("  Odometer: unavailable (no ECU reports OBD PID A6)"))
    else:
        out(f"  Odometer: {km_text(d['odometer_km'], 1)}")
    if d["mil_km"] is not None or d["mil_min"] is not None:
        text = counter_text(d["mil_km"], d["mil_min"])
        mil_on = d.get("mil_on")
        if mil_on:
            out("  Driven with check engine light on: " + yellow(text))
        elif mil_on is False and (d["mil_km"] or d["mil_min"]):
            out(f"  Check engine light off; last time it was on, it stayed on for: {text}")
        elif mil_on is None:
            out(f"  Driven with check engine light on (now or last time): {text}")
    if d["cleared_km"] is not None or d["cleared_min"] is not None:
        out(f"  Since codes were last cleared: {counter_text(d['cleared_km'], d['cleared_min'])}")


def assess_voltage(v):
    """(level, advice) for a module supply voltage with the engine off."""
    if v >= 13.2:
        return "charging", "engine running or a charger is connected"
    if v >= 12.4:
        return "ok", "healthy"
    if v >= 12.2:
        return "fair", "partly discharged; fine for a short scan"
    if v >= 11.8:
        return "low", ("low; connect a battery maintainer. Low voltage can make "
                       "modules log faults of their own")
    return "critical", "very low; charge the battery before scanning"


def print_battery(v, label="Battery", rpm=None):
    engine = ""
    if rpm is not None:
        engine = dim(f"  ·  engine running ({rpm:.0f} rpm)" if engine_running(rpm)
                     else "  ·  engine off")
    if v is None:
        out(dim(f"  {label}: unavailable (no OBD ECU reported module voltage)") + engine)
        return
    level, advice = assess_voltage(v)
    if engine_running(rpm):
        level, advice = "charging", "alternator charging; battery health can't be judged"
    paint_fn = {"charging": cyan, "ok": green, "fair": yellow,
                "low": red, "critical": red}[level]
    out(f"  {label}: " + paint_fn(f"{v:.2f} V") +
        f"  {dim(advice) if level in ('ok', 'charging') else advice}" + engine)


def slow_probe(bus, scheme_keys, known, fd, timeout):
    """Real 19 02 request to every address that stayed silent."""
    found = {}
    todo = [(key, tx, rx) for key in scheme_keys for tx, rx in SCHEMES[key][2]
            if (SCHEMES[key][1], tx) not in known]
    for i, (key, tx, rx) in enumerate(todo, 1):
        status_line(f"  Slow probe {i}/{len(todo)}  ·  {len(found)} extra")
        mod = {"scheme": key, "scheme_label": SCHEMES[key][0], "ext": SCHEMES[key][1],
               "tx": tx, "rx": rx, "obd": False}
        stack = make_stack(bus, mod, fd)
        try:
            uds(stack, [0x19, 0x02, 0x08], timeout=timeout)
            found[(mod["ext"], tx)] = mod
        except NoResponse:
            pass
        except UdsError:
            found[(mod["ext"], tx)] = mod
        finally:
            stack.stop()
    clear_status()
    return found


# ---------------------------------------------------------------- per module

def read_identity(stack, mod):
    ident = {}
    for did, key in IDENT_DIDS:
        try:
            raw = uds(stack, [0x22, did >> 8, did & 0xFF], timeout=1.0)[3:]
            ident[key] = {"text": printable(raw), "hex": raw.hex()}
        except UdsError as e:
            ident[key] = {"error": str(e)}
    mod["ident"] = ident


def read_dtcs(stack, mod, mask):
    masks = [mask] + ([0x08, 0x01] if mask != 0x08 else [0x01])
    for m in masks:
        try:
            resp = uds(stack, [0x19, 0x02, m], timeout=3.0)
        except UdsError as e:
            if "0x14" in str(e):          # responseTooLong: narrow the mask
                mod.setdefault("notes", []).append(f"mask 0x{m:02X} too long, narrowing")
                continue
            mod["dtc_error"] = str(e)
            mod["dtcs"] = []
            return
        body = resp[3:]
        dtcs = []
        for i in range(0, len(body) - 3, 4):
            b, s = body[i:i + 3], body[i + 3]
            if m != 0xFF and not (s & m):
                continue
            dtcs.append({"code": vw_number(b), "raw": b.hex(), "status": s,
                         "labels": status_labels(s), "severity": severity(s)})
        mod["mask_used"] = m
        mod["availability_mask"] = resp[2]
        mod["dtcs"] = dtcs
        return
    mod["dtc_error"] = "responseTooLong even with narrow masks"
    mod["dtcs"] = []


def read_details(stack, dtc):
    b = bytes.fromhex(dtc["raw"])
    for sub, key in ((0x04, "snapshot"), (0x06, "extended")):
        try:
            dtc[key] = uds(stack, [0x19, sub, *b, 0xFF], timeout=3.0).hex()
        except UdsError as e:
            dtc[key] = f"error: {e}"


CONTINUOUS_MONITORS = ["misfire", "fuel system", "components"]
SPARK_MONITORS = ["catalyst", "heated catalyst", "evaporative system", "secondary air",
                  None, "O2 sensor", "O2 sensor heater", "EGR/VVT"]
DIESEL_MONITORS = ["NMHC catalyst", "NOx/SCR", None, "boost pressure",
                   None, "exhaust gas sensor", "particulate filter", "EGR/VVT"]


def decode_monitor_status(data):
    """Decode OBD Mode 01 PID 01 (4 bytes: A B C D)."""
    a, b, c, d = data[:4]
    diesel = bool(b & 0x08)
    monitors = []
    for i, name in enumerate(CONTINUOUS_MONITORS):
        if b & (1 << i):
            monitors.append({"name": name, "complete": not (b & (0x10 << i))})
    for i, name in enumerate(DIESEL_MONITORS if diesel else SPARK_MONITORS):
        if name and c & (1 << i):
            monitors.append({"name": name, "complete": not (d & (1 << i))})
    return {
        "mil_on": bool(a & 0x80),
        "dtc_count": a & 0x7F,
        "ignition": "compression" if diesel else "spark",
        "monitors": monitors,
    }


def read_obd(stack, mod):
    # Mode 01 PID 01: is this ECU commanding the check engine light? Readiness?
    try:
        resp = uds(stack, [0x01, 0x01], timeout=2.0)
        if len(resp) >= 6:
            mod["obd_status"] = decode_monitor_status(resp[2:6])
    except UdsError as e:
        mod["obd_status"] = {"error": str(e)}

    res = {}
    for sid, key in ((0x03, "stored"), (0x07, "pending"), (0x0A, "permanent")):
        try:
            resp = uds(stack, [sid], timeout=2.0)
            n = resp[1]
            codes = [dtc_str(resp[2 + 2 * i: 4 + 2 * i]) for i in range(n)
                     if 4 + 2 * i <= len(resp)]
            res[key] = codes
        except UdsError as e:
            res[key] = f"error: {e}"
    mod["obd_dtcs"] = res

    # Mode 09: who is this ECU? (name, calibration ID, VIN)
    info = {}
    for pid, key in ((0x0A, "ecu_name"), (0x04, "cal_id"), (0x02, "vin")):
        try:
            resp = uds(stack, [0x09, pid], timeout=2.0)
            info[key] = printable(resp[3:])
        except UdsError as e:
            info[key] = f"error: {e}"
    mod["obd_info"] = info

    # Mode 02: freeze frame 0, only worth asking if something is stored
    stored = res.get("stored")
    if isinstance(stored, list) and stored:
        mod["freeze_frame"] = read_freeze_frame(stack)


FF_PIDS = [
    (0x02, "trigger DTC", lambda a: dtc_str(a[:2])),
    (0x42, "module voltage", lambda a: f"{(a[0] * 256 + a[1]) / 1000:.2f} V"),
    (0x0C, "engine rpm", lambda a: f"{(a[0] * 256 + a[1]) / 4:.0f} rpm"),
    (0x0D, "vehicle speed", lambda a: f"{a[0]} km/h"),
    (0x05, "coolant temp", lambda a: f"{a[0] - 40} °C"),
    (0x46, "ambient temp", lambda a: f"{a[0] - 40} °C"),
    (0x1F, "run time since start", lambda a: f"{a[0] * 256 + a[1]} s"),
    (0x31, "distance since codes cleared", lambda a: f"{a[0] * 256 + a[1]} km"),
    (0x4E, "time since codes cleared", lambda a: f"{a[0] * 256 + a[1]} min"),
]


def read_freeze_frame(stack):
    ff = {}
    for pid, label, fn in FF_PIDS:
        try:
            resp = uds(stack, [0x02, pid, 0x00], timeout=1.5)
            data = resp[3:]
            ff[label] = fn(data) if data else "empty"
        except (UdsError, IndexError) as e:
            if isinstance(e, UdsError) and "0x11" in str(e):
                return {"freeze frame": "n/a (Mode 02 not supported by this ECU)"}
            ff[label] = f"n/a ({e})" if isinstance(e, UdsError) else "n/a"
    return ff


# ------------------------------------------------------------------ printing

def module_title(mod):
    hint = KNOWN.get(mod["tx"], "") if not mod["ext"] else ""
    obd_name = mod.get("obd_info", {}).get("ecu_name", "")
    if obd_name.startswith("error"):
        obd_name = ""
    name = (mod.get("ident", {}).get("name", {}).get("text") or obd_name
            or hint or "unnamed module")
    return name


def obd_only(mod):
    return mod.get("obd") and "0x11" in mod.get("dtc_error", "")


def readiness_text(st):
    mons = st["monitors"]
    if not mons:
        return "no monitors supported"
    not_ready = [m["name"] for m in mons if not m["complete"]]
    ready = len(mons) - len(not_ready)
    if not not_ready:
        if ready == 1:
            return green("the 1 supported monitor is complete")
        return green(f"all {ready} supported monitors complete")
    return f"{ready} complete, " + yellow(f"{len(not_ready)} not ready: {', '.join(not_ready)}")


def print_cel(mods):
    """Who is turning on the check engine light, and emissions readiness."""
    reporting = [m for m in mods if m.get("obd_status") and "error" not in m["obd_status"]]
    if not reporting:
        out(dim("  Check engine light: unknown (no OBD ECU reported its status)"))
        return
    owners = [m for m in reporting if m["obd_status"]["mil_on"]]
    if owners:
        names = ", ".join(f"{module_title(m)} [{fmt_id(m['tx'], m['ext'])}]" for m in owners)
        out(red(bold("  Check engine light: ON")) + f" — requested by {names}")
    else:
        out(green("  Check engine light: off") + dim(f" ({len(reporting)} OBD ECU(s) reporting)"))
    not_ready = [(m, mon["name"]) for m in reporting
                 for mon in m["obd_status"]["monitors"] if not mon["complete"]]
    if not_ready:
        items = ", ".join(f"{n} ({module_title(m)})" for m, n in not_ready)
        out(yellow(f"  Readiness: not ready — {items}"))
    else:
        out(green("  Readiness: all supported monitors complete"))
    out()


def print_module(mod, show_all):
    tx, rx = fmt_id(mod["tx"], mod["ext"]), fmt_id(mod["rx"], mod["ext"])
    tag = " · OBD" if mod["obd"] else ""
    rule(f"{module_title(mod)}  [{tx} → {rx}, {mod['scheme_label']}{tag}]")
    ident = mod.get("ident", {})
    parts = []
    for key, label in (("part", "Part"), ("sw", "SW"), ("hw", "HW")):
        text = ident.get(key, {}).get("text")
        if text:
            parts.append(f"{label} {text}")
    if parts:
        out("  " + dim("  ·  ".join(parts)))
    if obd_only(mod):
        out("  " + dim("OBD-only ECU (no UDS DTC service); codes are in the OBD lines below"))
    elif "dtc_error" in mod:
        out("  " + yellow(f"DTC read failed: {mod['dtc_error']}"))
    dtcs = mod.get("dtcs", [])
    if not dtcs and "dtc_error" not in mod:
        out("  " + green("✓ no failed DTCs"))
    order = {"active": 0, "confirmed": 1, "pending": 2, "history": 3, "untested": 4}
    for d in sorted(dtcs, key=lambda d: order[d["severity"]]):
        if d["severity"] == "untested" and not show_all:
            continue
        marker = sev_paint(d["severity"], "●")
        code = sev_paint(d["severity"], f"{d['code']:>8}") + dim(f" [{d['raw'].upper()}]")
        flag = red("  ◀ TARGET") if d.get("target") else ""
        status = dim("0x%02X" % d["status"])
        out(f"  {marker} {code}  {status}  {', '.join(d['labels'])}{flag}")
        for key in ("snapshot", "extended"):
            if key in d:
                out(f"      {dim(key + ':')} {d[key]}")
    info = mod.get("obd_info")
    if info:
        for key, label in (("ecu_name", "OBD name"), ("cal_id", "Cal ID"), ("vin", "VIN")):
            val = info.get(key, "")
            if val and not val.startswith("error"):
                out(f"  {dim(label + ':')} {val}")
    st = mod.get("obd_status")
    if st and "error" not in st:
        if st["mil_on"]:
            out("  " + red("CEL: ON") + " — this ECU is requesting the check engine light")
        else:
            out(f"  {dim('CEL:')} off")
        out(f"  {dim('Readiness:')} {readiness_text(st)}")
    obd = mod.get("obd_dtcs")
    if obd:
        for key, val in obd.items():
            if isinstance(val, list):
                text = ", ".join(red(c) if c == mod.get("_target_code") else c for c in val) or "none"
            else:
                text = dim(val)
            out(f"  {dim('OBD ' + key + ':')} {text}")
    ff = mod.get("freeze_frame")
    if ff:
        out(f"  {dim('Freeze frame 0:')}")
        for label, val in ff.items():
            out(f"      {label:<30} {dim(val) if val.startswith('n/a') else val}")
    for note in mod.get("notes", []):
        out("  " + dim(f"note: {note}"))
    out()


def print_summary(mods, target_txt, hits):
    """Per-module counts, then the target result or a list of notable faults."""
    rule("SUMMARY")
    hdr = f"  {'Address':<12} {'Module':<30} {'Active':>6} {'Conf':>5} {'Pend':>5} {'Hist':>5}"
    out(bold(hdr))
    for mod in mods:
        counts = {k: 0 for k in ("active", "confirmed", "pending", "history")}
        for d in mod.get("dtcs", []):
            if d["severity"] in counts:
                counts[d["severity"]] += 1
        name = module_title(mod)[:30]
        addr = fmt_id(mod["tx"], mod["ext"])
        cells = [
            red(f"{counts['active']:>6}") if counts["active"] else dim(f"{0:>6}"),
            yellow(f"{counts['confirmed']:>5}") if counts["confirmed"] else dim(f"{0:>5}"),
            yellow(f"{counts['pending']:>5}") if counts["pending"] else dim(f"{0:>5}"),
            cyan(f"{counts['history']:>5}") if counts["history"] else dim(f"{0:>5}"),
        ]
        if obd_only(mod):
            err = dim(" (OBD only)")
        else:
            err = yellow(" (read failed)") if "dtc_error" in mod else ""
        out(f"  {addr:<12} {name:<30} {' '.join(cells)}{err}")
    out()
    print_cel(mods)
    if target_txt is None:
        notable = [(m, d) for m in mods for d in m.get("dtcs", [])
                   if d["severity"] in ("active", "confirmed", "pending")]
        obd = []  # (module, code, ["stored", "permanent", ...]): one line per code
        for m in mods:
            kinds = {}
            for k, v in (m.get("obd_dtcs") or {}).items():
                if isinstance(v, list):
                    for code in v:
                        kinds.setdefault(code, []).append(k)
            obd += [(m, code, ks) for code, ks in kinds.items()]
        if notable or obd:
            out(bold("  Faults worth attention:"))
            for mod, d in notable:
                out(f"    {sev_paint(d['severity'], d['code']):<9}  "
                    f"{module_title(mod)} [{fmt_id(mod['tx'], mod['ext'])}]  "
                    f"{', '.join(d['labels'])}")
            for mod, code, ks in obd:
                out(f"    {yellow(code)}  {module_title(mod)} "
                    f"[{fmt_id(mod['tx'], mod['ext'])}]  OBD {', '.join(ks)}")
        else:
            out(green("  No active, confirmed, pending or OBD-reported faults."))
        out()
        return
    if hits:
        out(red(bold(f"  {target_txt} FOUND in {len(hits)} module(s):")))
        for mod, d in hits:
            st = f"status 0x{d['status']:02X} " if d["status"] is not None else ""
            out(f"    {module_title(mod)} [{fmt_id(mod['tx'], mod['ext'])}]  "
                f"{d['code']}  {st}({', '.join(d['labels'])})")
    else:
        out(green(f"  {target_txt} not stored in any module that answered."))
    out()


# ---------------------------------------------------------------------- diff

def byte_changes(old_hex, new_hex):
    """List of (index, old_byte, new_byte) where two hex strings differ."""
    a, b = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
    changes = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            changes.append((i, x, y))
    return changes


def _hx(v):
    return "--" if v is None else f"{v:02X}"


def _is_hex(v):
    return isinstance(v, str) and v and not v.startswith("error") and \
        all(c in "0123456789abcdefABCDEF" for c in v)


def diff_module(old, new):
    """Human-readable change lines for one module present in both scans."""
    lines = []

    # identity (a software change usually means the module was reflashed)
    for key, label in (("sw", "Software"), ("hw", "Hardware"), ("part", "Part number")):
        a = old.get("ident", {}).get(key, {}).get("text")
        b = new.get("ident", {}).get(key, {}).get("text")
        if a and b and a != b:
            lines.append(cyan(f"~ {label} {a} → {b}"))

    # UDS DTCs
    od = {d["raw"]: d for d in old.get("dtcs", [])}
    nd = {d["raw"]: d for d in new.get("dtcs", [])}
    for raw in sorted(nd.keys() - od.keys()):
        d = nd[raw]
        lines.append(sev_paint(d["severity"], f"+ {d['code']:<9}") +
                     f" new       0x{d['status']:02X}  {', '.join(d['labels'])}")
    for raw in sorted(od.keys() - nd.keys()):
        d = od[raw]
        lines.append(green(f"- {d['code']:<9}") +
                     f" gone      (was 0x{d['status']:02X}: {', '.join(d['labels'])})")
    for raw in sorted(od.keys() & nd.keys()):
        a, b = od[raw], nd[raw]
        if a["status"] != b["status"]:
            gained = [x for x in b["labels"] if x not in a["labels"]]
            lost = [x for x in a["labels"] if x not in b["labels"]]
            parts = [f"+{x}" for x in gained] + [f"-{x}" for x in lost]
            lines.append(yellow(f"~ {b['code']:<9}") +
                         f" status    0x{a['status']:02X} → 0x{b['status']:02X}  ({', '.join(parts)})")
        for key in ("extended", "snapshot"):
            x, y = a.get(key), b.get(key)
            if x is None or y is None or x == y:
                continue
            if _is_hex(x) and _is_hex(y):
                ch = ", ".join(f"[{i}] {_hx(p)}→{_hx(q)}" for i, p, q in byte_changes(x, y))
                lines.append(yellow(f"~ {b['code']:<9}") + f" {key:<9} bytes {ch}")
                lines.append(dim(f"              old {' '.join(x[i:i+2] for i in range(0, len(x), 2))}"))
                lines.append(dim(f"              new {' '.join(y[i:i+2] for i in range(0, len(y), 2))}"))
            else:
                lines.append(yellow(f"~ {b['code']:<9}") + f" {key:<9} {x} → {y}")

    # OBD lists
    oo, no = old.get("obd_dtcs") or {}, new.get("obd_dtcs") or {}
    for key in ("stored", "pending", "permanent"):
        a, b = oo.get(key), no.get(key)
        if not isinstance(a, list) or not isinstance(b, list):
            continue
        for c in sorted(set(b) - set(a)):
            lines.append(red(f"+ {c:<9}") + f" OBD {key}")
        for c in sorted(set(a) - set(b)):
            lines.append(green(f"- {c:<9}") + f" OBD {key} (cleared)")

    # check engine light and readiness
    os_, ns = old.get("obd_status") or {}, new.get("obd_status") or {}
    if "mil_on" in os_ and "mil_on" in ns:
        if os_["mil_on"] != ns["mil_on"]:
            state = red("ON") if ns["mil_on"] else green("off")
            lines.append(f"~ CEL request {'ON' if os_['mil_on'] else 'off'} → {state}")
        om = {m["name"]: m["complete"] for m in os_.get("monitors", [])}
        nm = {m["name"]: m["complete"] for m in ns.get("monitors", [])}
        for name in sorted(om.keys() & nm.keys()):
            if om[name] != nm[name]:
                now = green("complete") if nm[name] else yellow("not ready")
                lines.append(f"~ Readiness {name}: {now}")

    # freeze frame
    of, nf = old.get("freeze_frame") or {}, new.get("freeze_frame") or {}
    for label in sorted(of.keys() & nf.keys()):
        if of[label] != nf[label]:
            lines.append(f"~ Freeze frame {label}: {of[label]} → {nf[label]}")
    return lines


def diff_main(argv):
    global _color
    ap = argparse.ArgumentParser(
        prog="dtcsweep.py diff",
        description="Compare two dtcsweep JSON files and show what changed.")
    ap.add_argument("old", help="earlier scan (.json)")
    ap.add_argument("new", help="later scan (.json)")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)
    if args.no_color:
        _color = False
    try:
        with open(args.old, encoding="utf-8") as f:
            old = json.load(f)
        with open(args.new, encoding="utf-8") as f:
            new = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"Could not read scan file: {e}")

    out(bold("dtcsweep diff") + dim(f"  ·  {old.get('started', '?')} → {new.get('started', '?')}"))
    if old.get("mask") != new.get("mask"):
        out(yellow("  Note: the scans used different DTC masks (one used --all?), "
                   "so some codes may appear or vanish only because of that."))
    ob, nb = (old.get("battery") or {}).get("start"), (new.get("battery") or {}).get("start")
    if ob is not None and nb is not None:
        out(dim(f"  Battery at start: {ob:.2f} V → {nb:.2f} V"))
    oe = engine_running((old.get("engine_rpm") or {}).get("start"))
    ne = engine_running((new.get("engine_rpm") or {}).get("start"))
    if "engine_rpm" in old and "engine_rpm" in new and oe != ne:
        out(yellow(f"  Note: engine {'running' if oe else 'off'} → {'running' if ne else 'off'}. "
                   "Voltage and some fault states aren't directly comparable."))
    od, nd = old.get("distances") or {}, new.get("distances") or {}
    a, b = od.get("odometer_km"), nd.get("odometer_km")
    if a is not None and b is not None:
        out(f"  Odometer: {a:,.1f} → {b:,.1f} km "
            f"({b - a:+,.1f} km, {(b - a) * 0.621371:+,.1f} mi)")
    for key, label, unit in (("mil_km", "Driven with check engine light on", "km"),
                             ("mil_min", "Time with check engine light on", "min"),
                             ("cleared_km", "Distance since codes cleared", "km"),
                             ("cleared_min", "Time since codes cleared", "min")):
        a, b = od.get(key), nd.get(key)
        if a is not None and b is not None and a != b:
            line = f"  {label}: {a:,} → {b:,} {unit}"
            out(yellow(line + "  (codes were cleared in between)") if b < a else line)
    out()

    om = {(m["ext"], m["tx"]): m for m in old.get("modules", [])}
    nm = {(m["ext"], m["tx"]): m for m in new.get("modules", [])}
    changed = 0
    for key in sorted(nm.keys() - om.keys()):
        m = nm[key]
        out(cyan(f"+ Module now answering: {module_title(m)} [{fmt_id(m['tx'], m['ext'])}]"))
        changed += 1
    for key in sorted(om.keys() - nm.keys()):
        m = om[key]
        out(yellow(f"- Module not answering: {module_title(m)} [{fmt_id(m['tx'], m['ext'])}]"))
        changed += 1
    if changed:
        out()
    modules_changed = 0
    for key in sorted(om.keys() & nm.keys()):
        lines = diff_module(om[key], nm[key])
        if not lines:
            continue
        m = nm[key]
        rule(f"{module_title(m)}  [{fmt_id(m['tx'], m['ext'])}]")
        for line in lines:
            out("  " + line)
        out()
        changed += len(lines)
        modules_changed += 1
    if changed:
        total = modules_changed + len(nm.keys() ^ om.keys())
        out(bold(f"  {total} module(s) with changes."))
        out(dim("  Byte indexes count from the start of the response (byte 0 = 0x59)."))
    else:
        out(green("  No changes between these scans."))
    return 0


# ---------------------------------------------------------------------- main

def redact_vin(vin):
    return vin[:3] + "*" * (len(vin) - 3) if vin and not vin.startswith("error") else vin


def main(argv=None):
    global _log, _color
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "diff":
        return diff_main(argv[1:])
    ap = argparse.ArgumentParser(
        description="Read-only multi-module DTC scanner for VW Group cars (SocketCAN).")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--interface", choices=["socketcan", "gs_usb"], default="socketcan")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--fd", action="store_true", help="send CAN FD frames")
    ap.add_argument("--schemes", default="11vag,11obd,29obd,29vag",
                    help="comma list of: " + ", ".join(SCHEMES))
    ap.add_argument("--target", help="DTC to hunt for, e.g. U3001 or U3001-00")
    ap.add_argument("--all", action="store_true", help="list never-failed DTCs too")
    ap.add_argument("--details", action="store_true",
                    help="freeze-frame/extended data for all active+confirmed DTCs")
    ap.add_argument("--slow", action="store_true",
                    help="also probe silent addresses with a real 19 02")
    ap.add_argument("--probe", type=float, default=0.25, help="timeout per --slow probe")
    ap.add_argument("--redact", action="store_true", help="mask the VIN in all output")
    ap.add_argument("--min-voltage", type=float, default=None,
                    help="refuse to scan if module voltage is below this (e.g. 12.0)")
    ap.add_argument("--out-dir", default=".", help="directory for .log/.json output")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    if args.no_color:
        _color = False
    keys = [k.strip() for k in args.schemes.split(",") if k.strip()]
    bad = [k for k in keys if k not in SCHEMES]
    if bad:
        sys.exit(f"Unknown scheme(s): {', '.join(bad)}. Choose from {', '.join(SCHEMES)}.")
    target = parse_target(args.target) if args.target else None
    target_txt = args.target.strip().upper() if args.target else None
    mask = 0xFF if args.all else DEFAULT_MASK

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, f"dtcsweep-{stamp}.log")
    json_path = os.path.join(args.out_dir, f"dtcsweep-{stamp}.json")
    _log = open(log_path, "w", encoding="utf-8")

    out(bold("dtcsweep") + dim(f"  ·  {stamp}  ·  read-only  ·  "
        f"{'CAN FD' if args.fd else 'classic CAN'}"
        + (f"  ·  target {target_txt}" if target_txt else "")))
    out(dim(f"  Schemes: {', '.join(SCHEMES[k][0] for k in keys)}"))
    out()

    if args.interface == "gs_usb":
        if args.fd:
            sys.exit("gs_usb backend: classic CAN only (drop --fd)")
        bus = can.Bus(interface="gs_usb", channel=0, bitrate=500000)
    else:
        bus = can.Bus(interface="socketcan", channel=args.channel, fd=args.fd)

    started = time.time()
    try:
        battery = {"start": read_battery(bus, keys, args.fd)}
        engine = {"start": read_rpm(bus, keys, args.fd)}
        print_battery(battery["start"], rpm=engine["start"])
        if (args.min_voltage is not None and battery["start"] is not None
                and battery["start"] < args.min_voltage):
            out(red(f"  Below --min-voltage {args.min_voltage:.2f} V. Not scanning."))
            return
        distances = read_distances(bus, keys, args.fd)
        print_distances(distances)
        out()
        rule("DISCOVERY")
        found = discover(bus, keys, args.fd)
        for k, mod in discover_obd(bus, keys, args.fd).items():
            if k in found:
                found[k]["obd"] = True
            else:
                found[k] = mod
        if args.slow:
            found.update(slow_probe(bus, keys, found, args.fd, args.probe))
        by_scheme = {}
        for mod in found.values():
            by_scheme[mod["scheme_label"]] = by_scheme.get(mod["scheme_label"], 0) + 1
        for k in keys:
            label = SCHEMES[k][0]
            n = by_scheme.get(label, 0)
            out(f"  {label:<12} {green(str(n)) if n else dim('0')} module(s)")
        out()
        if not found:
            out(yellow("Nothing answered. Check: ignition on, can0 up at 500k, "
                       "wiring. Then try --slow, then --fd."))
            return

        mods = sorted(found.values(), key=lambda m: (m["ext"], m["tx"]))
        hits = []
        for i, mod in enumerate(mods, 1):
            status_line(f"  Reading module {i}/{len(mods)}  {fmt_id(mod['tx'], mod['ext'])}")
            stack = make_stack(bus, mod, args.fd)
            try:
                read_identity(stack, mod)
                read_dtcs(stack, mod, mask)
                for d in mod["dtcs"]:
                    raw = bytes.fromhex(d["raw"])
                    if target and target.get("raw") == raw:
                        d["target"] = True
                        hits.append((mod, d))
                        read_details(stack, d)
                    elif args.details and d["severity"] in ("active", "confirmed"):
                        read_details(stack, d)
                if mod["obd"]:
                    code = target.get("sae") if target else None
                    mod["_target_code"] = code
                    read_obd(stack, mod)
                    if args.redact and "vin" in mod.get("obd_info", {}):
                        mod["obd_info"]["vin"] = redact_vin(mod["obd_info"]["vin"])
                    lists = [k for k, v in mod["obd_dtcs"].items()
                             if code and isinstance(v, list) and code in v]
                    if lists and not any(h[0] is mod for h in hits):
                        hits.append((mod, {"code": code + " (OBD)", "status": None,
                                           "labels": [f"OBD {', '.join(lists)}"]}))
            finally:
                stack.stop()
            clear_status()
            print_module(mod, args.all)

        battery["end"] = read_battery(bus, keys, args.fd)
        engine["end"] = read_rpm(bus, keys, args.fd)
        print_summary(mods, target_txt, hits)
        if battery["end"] is not None:
            print_battery(battery["end"], "Battery at end", rpm=engine["end"])
            if battery["start"] is not None:
                out(dim(f"  Change during scan: {battery['end'] - battery['start']:+.2f} V"))
            out()

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"version": __version__, "started": stamp, "fd": args.fd,
                       "battery": battery, "engine_rpm": engine, "distances": distances,
                       "mask": mask, "target": target_txt, "modules": mods}, f, indent=2)
        out(dim(f"  Finished in {time.time() - started:.0f}s. "
                f"Files: {log_path}, {json_path}"))
    finally:
        bus.shutdown()
        _log.close()
        _log = None


if __name__ == "__main__":
    main()
