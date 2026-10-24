import pytest

import dtcsweep as scan


@pytest.mark.parametrize("raw,expected", [
    (b"\xF0\x01\x00", "U3001-00"),
    (b"\x00\x72\x3C", "P0072-3C"),
    (b"\x95\x05\x00", "B1505-00"),
    (b"\x41\x23", "C0123"),
])
def test_dtc_str(raw, expected):
    assert scan.dtc_str(raw) == expected


def test_parse_dtc_roundtrip():
    b0, b1, ftb = scan.parse_dtc("u3001-00")
    assert scan.dtc_str(bytes([b0, b1, ftb])) == "U3001-00"
    assert scan.parse_dtc("P0300")[2] is None


@pytest.mark.parametrize("bad", ["U9999", "X1234", "P03", "P0300-ZZ"])
def test_parse_dtc_rejects_invalid(bad):
    with pytest.raises(SystemExit):
        scan.parse_dtc(bad)


@pytest.mark.parametrize("status,sev", [
    (0x01, "active"), (0xA8, "active"), (0x08, "confirmed"),
    (0x04, "pending"), (0x20, "history"), (0x50, "untested"),
])
def test_severity(status, sev):
    assert scan.severity(status) == sev


def test_status_labels():
    assert scan.status_labels(0x2E) == [
        "failed this cycle", "pending", "confirmed", "failed since clear"]


def test_printable_strips_control_bytes():
    assert scan.printable(b"ECM\x00-EngineControl\x00\x00") == "ECM -EngineControl"


def test_monitor_status_spark():
    st = scan.decode_monitor_status(bytes([0x83, 0x07, 0x65, 0x04]))
    assert st["mil_on"] is True and st["dtc_count"] == 3 and st["ignition"] == "spark"
    names = {m["name"]: m["complete"] for m in st["monitors"]}
    assert names == {"misfire": True, "fuel system": True, "components": True,
                     "catalyst": True, "evaporative system": False,
                     "O2 sensor": True, "O2 sensor heater": True}


def test_monitor_status_incomplete_continuous_and_diesel():
    st = scan.decode_monitor_status(bytes([0x00, 0x09 | 0x10, 0x40, 0x40]))
    names = {m["name"]: m["complete"] for m in st["monitors"]}
    assert st["ignition"] == "compression" and st["mil_on"] is False
    assert names == {"misfire": False, "particulate filter": False}


@pytest.mark.parametrize("volts,level", [
    (14.1, "charging"), (12.6, "ok"), (12.3, "fair"), (12.0, "low"), (11.5, "critical"),
])
def test_assess_voltage(volts, level):
    assert scan.assess_voltage(volts)[0] == level


@pytest.mark.parametrize("ftb,text", [
    (0x00, None),
    (0x11, "short to ground"),
    (0x87, "message missing"),
    (0x50, "programming/configuration (category only)"),
    (0x3C, "frequency/PWM signal (category only)"),
    (0xF3, "manufacturer-specific (category only)"),
    (0xB0, None),
])
def test_failure_type(ftb, text):
    assert scan.failure_type(ftb) == text
