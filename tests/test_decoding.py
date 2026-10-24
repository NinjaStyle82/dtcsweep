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
