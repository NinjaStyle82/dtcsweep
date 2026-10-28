import pytest

import dtcsweep as scan


@pytest.mark.parametrize("raw,expected", [
    (b"\xF0\x01", "U3001"),
    (b"\x41\x23", "C0123"),
    (b"\x00\x72", "P0072"),
])
def test_dtc_str(raw, expected):
    assert scan.dtc_str(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # values cross-checked against a VCDS Auto-Scan of the test car
    (b"\x05\xA2\x50", "369232"),
    (b"\x00\x72\x3C", "29244"),
    (b"\xC0\x00\x47", "12582983"),
    (b"\x00\x01\x42", "322"),
])
def test_vw_number(raw, expected):
    assert scan.vw_number(raw) == expected


@pytest.mark.parametrize("text,expected", [
    ("369232", {"raw": b"\x05\xA2\x50"}),
    ("0x05a250", {"raw": b"\x05\xA2\x50"}),
    ("u3001", {"sae": "U3001"}),
    ("U3001-00", {"sae": "U3001"}),
    ("U3001 00", {"sae": "U3001"}),
])
def test_parse_target(text, expected):
    assert scan.parse_target(text) == expected


@pytest.mark.parametrize("bad", ["U9999", "X1234", "P03", "P0300-ZZ", "0x05A2", "99999999"])
def test_parse_target_rejects_invalid(bad):
    with pytest.raises(SystemExit):
        scan.parse_target(bad)


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

