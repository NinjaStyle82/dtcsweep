"""
A small simulated VW-style car for tests. No hardware needed.

Each module is keyed by (is_extended_id, request_id). respond() returns the
UDS/OBD response payload a real module would send, or None for silence.
Every request is recorded in SENT so tests can check the read-only guarantee.
"""

SENT = []  # list of (is_extended_id, arbitration_id, payload bytes)


def _mods():
    return {
        # 11-bit VAG: brakes-style module with history + confirmed + untested DTCs
        (False, 0x713): dict(rx=0x77D, name=b"ESC", part=b"5WA614517XX",
                             dtcs=[(b"\x00\x22\x1A", 0x20), (b"\x95\x05\x00", 0x09),
                                   (b"\x00\x01\x0A", 0x50)]),
        # 11-bit VAG: module whose full list is too long unless the mask is narrow
        (False, 0x74C): dict(rx=0x7B6, name=b"MEM-FS", part=b"3QH959760XX",
                             toolong=True, dtcs=[(b"\xC1\x00\x00", 0x08)]),
        # 11-bit VAG: brake booster holding the target code in UDS form
        (False, 0x73B): dict(rx=0x7A5, name=b"EBKV", part=b"5WA909059XX",
                             dtcs=[(b"\x05\xA2\x50", 0xA8)]),
        # 29-bit VAG: engine
        (True, 0x17FC0076): dict(rx=0x17FE0076, name=b"R4 2.0l TFS", part=b"5WA906259XX",
                                 dtcs=[(b"\x00\x72\x3C", 0x2F)]),
        # 29-bit OBD: ECM, OBD-only, nothing stored
        (True, 0x18DA01F1): dict(rx=0x18DAF101, obdonly=True, obd_name=b"ECM\x00-EngineControl",
                                 obd_stored=[], obd_permanent=[],
                                 # MIL off; misfire/fuel/components complete;
                                 # catalyst, evap, O2, O2 heater supported; evap not ready
                                 pid01=bytes([0x00, 0x07, 0x65, 0x04])),
        # 29-bit OBD: brake booster's OBD face, U3001 stored + permanent
        (True, 0x18DA03F1): dict(rx=0x18DAF103, obdonly=True,
                                 obd_name=b"EBBC-ElecBrkBstCtrl",
                                 obd_stored=[b"\xF0\x01"], obd_permanent=[b"\xF0\x01"],
                                 freeze=True,
                                 # MIL on, 1 DTC, no monitors (not an engine ECU)
                                 pid01=bytes([0x81, 0x00, 0x00, 0x00])),
    }


MODS = _mods()
VIN = b"WVWZZZCDZTW000001"


def reset():
    global MODS
    MODS = _mods()
    SENT.clear()


def _obd(m, p):
    sid = p[0]
    if sid == 0x3E:
        return bytes([0x7E, 0x00])
    if sid == 0x01 and len(p) > 1 and p[1] == 0x00:
        return bytes([0x41, 0x00, 0xBE, 0x3F, 0xA8, 0x13])
    if sid == 0x01 and len(p) > 1 and p[1] == 0x01:
        return bytes([0x41, 0x01]) + m["pid01"]
    if sid in (0x03, 0x07, 0x0A):
        codes = {0x03: m["obd_stored"], 0x07: [], 0x0A: m["obd_permanent"]}[sid]
        return bytes([sid + 0x40, len(codes)]) + b"".join(codes)
    if sid == 0x09:
        if p[1] == 0x0A:
            return bytes([0x49, 0x0A, 1]) + m["obd_name"].ljust(20, b"\x00")
        if p[1] == 0x04:
            return bytes([0x49, 0x04, 1]) + b"CALID-TEST".ljust(16, b"\x00")
        if p[1] == 0x02:
            return bytes([0x49, 0x02, 1]) + VIN
    if sid == 0x02 and m.get("freeze"):
        pid = p[1]
        if pid == 0x02:
            return bytes([0x42, 0x02, 0x00, 0xF0, 0x01])
        if pid == 0x42:
            return bytes([0x42, 0x42, 0x00, 0x2F, 0x44])   # 12.10 V
        if pid == 0x0D:
            return bytes([0x42, 0x0D, 0x00, 0xFF])
        return bytes([0x7F, 0x02, 0x12])
    return bytes([0x7F, sid, 0x11])


def respond(ext, tx, p):
    SENT.append((ext, tx, bytes(p)))
    m = MODS.get((ext, tx))
    if not m:
        return None
    if m.get("obdonly"):
        return _obd(m, p)
    sid = p[0]
    if sid == 0x3E:
        return bytes([0x7E, 0x00])
    if sid == 0x22:
        did = (p[1] << 8) | p[2]
        if did == 0xF197:
            return bytes([0x62, p[1], p[2]]) + m["name"]
        if did == 0xF187:
            return bytes([0x62, p[1], p[2]]) + m["part"]
        return bytes([0x7F, 0x22, 0x31])
    if sid == 0x19:
        sub = p[1]
        if sub == 0x02:
            mask = p[2]
            if m.get("toolong") and mask not in (0x08, 0x01):
                return bytes([0x7F, 0x19, 0x14])
            body = b"".join(d + bytes([s]) for d, s in m["dtcs"] if s & mask)
            return bytes([0x59, 0x02, 0xFF]) + body
        if sub == 0x04:
            return bytes([0x7F, 0x19, 0x12])
        if sub == 0x06:
            return bytes([0x59, 0x06]) + bytes(p[2:5]) + bytes([0xA8, 0x01, 0x02, 0x06, 0x02, 0x28])
    return bytes([0x7F, sid, 0x11])


def functional(ext, p):
    """Functional OBD request: every OBD-capable module answers."""
    SENT.append((ext, 0x18DB33F1 if ext else 0x7DF, bytes(p)))
    replies = []
    for (mext, _tx), m in MODS.items():
        if mext == ext and m.get("obdonly"):
            replies.append((m["rx"], _obd(m, p)))
    return replies
