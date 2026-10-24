import glob
import json
import os

import sim_car
import dtcsweep as scan

# Services a read-only scanner may send. Anything else is a bug.
ALLOWED_SERVICES = {0x01, 0x02, 0x03, 0x07, 0x09, 0x0A, 0x19, 0x22, 0x3E}
ALLOWED_0x19_SUBFUNCTIONS = {0x02, 0x04, 0x06}


def run(tmp_path, capsys, *args):
    scan.main(["--out-dir", str(tmp_path), "--no-color", *args])
    text = capsys.readouterr().out
    logs = glob.glob(os.path.join(tmp_path, "dtcsweep-*.log"))
    jsons = glob.glob(os.path.join(tmp_path, "dtcsweep-*.json"))
    assert len(logs) == 1 and len(jsons) == 1
    with open(jsons[0], encoding="utf-8") as f:
        data = json.load(f)
    with open(logs[0], encoding="utf-8") as f:
        log = f.read()
    return text, log, data


def test_read_only_guarantee(tmp_path, capsys):
    run(tmp_path, capsys, "--details", "--target", "U3001")
    assert sim_car.SENT, "scanner sent nothing"
    for ext, arb, payload in sim_car.SENT:
        assert payload[0] in ALLOWED_SERVICES, f"non-read service 0x{payload[0]:02X} to 0x{arb:X}"
        if payload[0] == 0x19:
            assert payload[1] in ALLOWED_0x19_SUBFUNCTIONS
        if payload[0] == 0x3E:
            assert payload[1:2] == b"\x00"


def test_discovers_all_schemes(tmp_path, capsys):
    text, _, data = run(tmp_path, capsys)
    assert "11-bit VAG   3 module(s)" in text
    assert "29-bit VAG   1 module(s)" in text
    assert "29-bit OBD   2 module(s)" in text
    names = {scan.module_title(m) for m in data["modules"]}
    assert {"ESC", "MEM-FS", "EBKV", "R4 2.0l TFS", "EBBC-ElecBrkBstCtrl"} <= names


def test_hides_untested_dtcs_by_default(tmp_path, capsys):
    text, _, _ = run(tmp_path, capsys)
    assert "P0001-0A" not in text          # status 0x50 = never failed
    assert "B1505-00" in text              # status 0x09 = failing now


def test_all_shows_untested_dtcs(tmp_path, capsys):
    text, _, _ = run(tmp_path, capsys, "--all")
    assert "P0001-0A" in text


def test_response_too_long_narrows_mask(tmp_path, capsys):
    _, _, data = run(tmp_path, capsys)
    mem = next(m for m in data["modules"] if scan.module_title(m) == "MEM-FS")
    assert mem["mask_used"] == 0x08
    assert [d["code"] for d in mem["dtcs"]] == ["U0100-00"]


def test_target_found_via_obd_with_freeze_frame(tmp_path, capsys):
    text, _, data = run(tmp_path, capsys, "--target", "U3001")
    assert "U3001 FOUND in 1 module(s)" in text
    assert "EBBC-ElecBrkBstCtrl" in text and "OBD stored, permanent" in text
    ebbc = next(m for m in data["modules"] if m["tx"] == 0x18DA03F1)
    assert ebbc["freeze_frame"]["module voltage"] == "12.10 V"


def test_target_in_uds_pulls_extended_data(tmp_path, capsys):
    _, _, data = run(tmp_path, capsys, "--target", "P05A2-50")
    ebkv = next(m for m in data["modules"] if scan.module_title(m) == "EBKV")
    dtc = ebkv["dtcs"][0]
    assert dtc["target"] is True
    assert dtc["extended"].startswith("590605a250a8")
    assert dtc["snapshot"].startswith("error")


def test_no_target_lists_notable_faults(tmp_path, capsys):
    text, _, _ = run(tmp_path, capsys)
    assert "Faults worth attention" in text
    assert "U3001  EBBC-ElecBrkBstCtrl [0x18DA03F1]  OBD stored" in text
    assert "(read failed)" not in text and "OBD-only ECU" in text


def test_redact_masks_vin_everywhere(tmp_path, capsys):
    text, log, data = run(tmp_path, capsys, "--redact")
    vin = sim_car.VIN.decode()
    assert vin not in text and vin not in log and vin not in json.dumps(data)
    assert "WVW**************" in text


def test_vin_present_without_redact(tmp_path, capsys):
    text, _, _ = run(tmp_path, capsys)
    assert sim_car.VIN.decode() in text


def test_scheme_filter(tmp_path, capsys):
    _, _, data = run(tmp_path, capsys, "--schemes", "11vag")
    assert data["modules"] and all(m["scheme"] == "11vag" for m in data["modules"])
    assert not any(ext for ext, _, _ in sim_car.SENT)


def test_log_has_no_ansi_codes(tmp_path, capsys):
    _, log, _ = run(tmp_path, capsys)
    assert "\x1b[" not in log


def test_cel_owner_identified(tmp_path, capsys):
    text, _, data = run(tmp_path, capsys)
    assert "Check engine light: ON — requested by EBBC-ElecBrkBstCtrl [0x18DA03F1]" in text
    assert "Readiness: not ready — evaporative system (ECM -EngineControl)" in text
    ecm = next(m for m in data["modules"] if m["tx"] == 0x18DA01F1)
    assert ecm["obd_status"]["mil_on"] is False


def test_cel_off_when_no_ecu_requests_it(tmp_path, capsys):
    sim_car.MODS[(True, 0x18DA03F1)]["pid01"] = bytes([0x00, 0, 0, 0])
    text, _, _ = run(tmp_path, capsys)
    assert "Check engine light: off" in text
