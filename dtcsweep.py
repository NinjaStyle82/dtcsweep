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
  --target U3001   hunt one code: flag it and pull its detail records
  --schemes 11vag,29obd   limit which address schemes are swept
  --slow           also probe silent addresses with a real 19 02 request
  --fd             CAN FD frames (needs an FD-capable interface/firmware)
  --redact         mask the VIN in output (use before sharing logs)
  --out-dir DIR    where to write the .log and .json (default: current dir)
  --no-color       plain output

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
    code = f"{'PCBU'[b[0] >> 6]}{b[0] & 0x3F:02X}{b[1]:02X}"
    return f"{code}-{b[2]:02X}" if len(b) > 2 else code


def parse_dtc(s):
    s = s.strip().upper()
    m = re.fullmatch(r"([PCBU])([0-3][0-9A-F])([0-9A-F]{2})(?:-?([0-9A-F]{2}))?", s)
    if not m:
        sys.exit(f"Bad --target '{s}'. Use e.g. U3001 or U3001-00.")
    b0 = ("PCBU".index(m.group(1)) << 6) | int(m.group(2), 16)
    b1 = int(m.group(3), 16)
    ftb = int(m.group(4), 16) if m.group(4) else None
    return b0, b1, ftb


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
            dtcs.append({"code": dtc_str(b), "raw": b.hex(), "status": s,
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


def read_obd(stack, mod):
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
    if "dtc_error" in mod:
        out("  " + yellow(f"DTC read failed: {mod['dtc_error']}"))
    dtcs = mod.get("dtcs", [])
    if not dtcs and "dtc_error" not in mod:
        out("  " + green("✓ no failed DTCs"))
    order = {"active": 0, "confirmed": 1, "pending": 2, "history": 3, "untested": 4}
    for d in sorted(dtcs, key=lambda d: order[d["severity"]]):
        if d["severity"] == "untested" and not show_all:
            continue
        marker = sev_paint(d["severity"], "●")
        code = sev_paint(d["severity"], f"{d['code']:<9}")
        flag = red("  ◀ TARGET") if d.get("target") else ""
        status = dim("0x%02X" % d["status"])
        out(f"  {marker} {code} {status}  {', '.join(d['labels'])}{flag}")
        for key in ("snapshot", "extended"):
            if key in d:
                out(f"      {dim(key + ':')} {d[key]}")
    info = mod.get("obd_info")
    if info:
        for key, label in (("ecu_name", "OBD name"), ("cal_id", "Cal ID"), ("vin", "VIN")):
            val = info.get(key, "")
            if val and not val.startswith("error"):
                out(f"  {dim(label + ':')} {val}")
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
        err = yellow(" (read failed)") if "dtc_error" in mod else ""
        out(f"  {addr:<12} {name:<30} {' '.join(cells)}{err}")
    out()
    if target_txt is None:
        notable = [(m, d) for m in mods for d in m.get("dtcs", [])
                   if d["severity"] in ("active", "confirmed", "pending")]
        if notable:
            out(bold("  Faults worth attention:"))
            for mod, d in notable:
                out(f"    {sev_paint(d['severity'], d['code']):<9}  "
                    f"{module_title(mod)} [{fmt_id(mod['tx'], mod['ext'])}]  "
                    f"{', '.join(d['labels'])}")
        else:
            out(green("  No active, confirmed or pending faults."))
        obd = [(m, k, v) for m in mods for k, v in (m.get("obd_dtcs") or {}).items()
               if isinstance(v, list) and v]
        for mod, k, v in obd:
            out(f"    OBD {k}: {', '.join(v)}  ({module_title(mod)} "
                f"[{fmt_id(mod['tx'], mod['ext'])}])")
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


# ---------------------------------------------------------------------- main

def redact_vin(vin):
    return vin[:3] + "*" * (len(vin) - 3) if vin and not vin.startswith("error") else vin


def main(argv=None):
    global _log, _color
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
    ap.add_argument("--out-dir", default=".", help="directory for .log/.json output")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    if args.no_color:
        _color = False
    keys = [k.strip() for k in args.schemes.split(",") if k.strip()]
    bad = [k for k in keys if k not in SCHEMES]
    if bad:
        sys.exit(f"Unknown scheme(s): {', '.join(bad)}. Choose from {', '.join(SCHEMES)}.")
    if args.target:
        t0, t1, tftb = parse_dtc(args.target)
        target_txt = args.target.upper()
    else:
        t0 = t1 = tftb = None
        target_txt = None
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
                    if target_txt and raw[0] == t0 and raw[1] == t1 and (tftb is None or raw[2] == tftb):
                        d["target"] = True
                        hits.append((mod, d))
                        read_details(stack, d)
                    elif args.details and d["severity"] in ("active", "confirmed"):
                        read_details(stack, d)
                if mod["obd"]:
                    code = dtc_str(bytes([t0, t1])) if target_txt else None
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

        print_summary(mods, target_txt, hits)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"version": __version__, "started": stamp, "fd": args.fd,
                       "mask": mask, "target": target_txt, "modules": mods}, f, indent=2)
        out(dim(f"  Finished in {time.time() - started:.0f}s. "
                f"Files: {log_path}, {json_path}"))
    finally:
        bus.shutdown()
        _log.close()
        _log = None


if __name__ == "__main__":
    main()
