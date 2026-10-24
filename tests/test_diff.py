import glob
import json
import os

import sim_car
import dtcsweep as scan


def scan_to(path, capsys, *args):
    scan.main(["--out-dir", str(path), "--no-color", *args])
    capsys.readouterr()
    return glob.glob(os.path.join(path, "dtcsweep-*.json"))[0]


def diff(capsys, old, new):
    scan.main(["diff", old, new, "--no-color"])
    return capsys.readouterr().out


def change_the_car():
    m = sim_car.MODS
    # brake booster: fault recurs (counter 02 -> 03), lamp request clears, module reflashed
    ebkv = m[(False, 0x73B)]
    ebkv["ext"] = bytes([0x01, 0x03, 0x06, 0x02, 0x27])
    ebkv["dtcs"] = [(b"\x05\xA2\x50", 0x28)]
    ebkv["sw"] = b"0571"
    # ESC: one fault cleared, a new one appears
    m[(False, 0x713)]["dtcs"] = [(b"\x00\x22\x1A", 0x20), (b"\x41\x23\x00", 0x08)]
    # OBD side: permanent U3001 cleared, CEL off, evap monitor completes
    m[(True, 0x18DA03F1)]["obd_permanent"] = []
    m[(True, 0x18DA03F1)]["pid01"] = bytes([0x00, 0, 0, 0])
    m[(True, 0x18DA01F1)]["pid01"] = bytes([0x00, 0x07, 0x65, 0x00])
    # a module stops answering
    del m[(False, 0x74C)]
    sim_car.VOLTAGE_MV = 12210


def test_diff_reports_every_kind_of_change(tmp_path, capsys):
    old = scan_to(tmp_path / "a", capsys, "--target", "P05A2-50")
    change_the_car()
    new = scan_to(tmp_path / "b", capsys, "--target", "P05A2-50")
    text = diff(capsys, old, new)

    assert "Battery at start: 12.45 V → 12.21 V" in text
    assert "- Module not answering: MEM-FS [0x74C]" in text
    assert "~ Software 0563 → 0571" in text
    assert "~ P05A2-50  status    0xA8 → 0x28  (-warning lamp)" in text
    # occurrence-style counter 02 -> 03 and healing-style counter 28 -> 27
    assert "extended  bytes [5] A8→28, [7] 02→03, [10] 28→27" in text
    assert "+ C0123-00  new" in text
    assert "- B1505-00  gone" in text
    assert "- U3001     OBD permanent (cleared)" in text
    assert "~ CEL request ON → off" in text
    assert "~ Readiness evaporative system: complete" in text
    assert "5 module(s) with changes." in text


def test_diff_notes_engine_state_change(tmp_path, capsys):
    old = scan_to(tmp_path / "a", capsys)
    sim_car.RPM = 750
    new = scan_to(tmp_path / "b", capsys)
    assert "engine off → running" in diff(capsys, old, new)


def test_diff_shows_distance_driven(tmp_path, capsys):
    old = scan_to(tmp_path / "a", capsys)
    sim_car.ODOMETER_X10 = 15600
    sim_car.MODS[(True, 0x18DA03F1)]["pids"][0x21] = bytes([0x00, 0xA8])  # 120 -> 168 km
    sim_car.MODS[(True, 0x18DA01F1)]["pids"][0x31] = bytes([0x00, 0x05])  # cleared
    new = scan_to(tmp_path / "b", capsys)
    text = diff(capsys, old, new)
    assert "Odometer: 1,512.3 → 1,560.0 km (+47.7 km, +29.6 mi)" in text
    assert "Driven with check engine light on: 120 → 168 km" in text
    assert "Distance since codes cleared: 1,512 → 5 km  (codes were cleared in between)" in text


def test_diff_identical_scans(tmp_path, capsys):
    old = scan_to(tmp_path / "a", capsys)
    new = scan_to(tmp_path / "b", capsys)
    assert "No changes between these scans." in diff(capsys, old, new)


def test_diff_warns_on_different_masks(tmp_path, capsys):
    old = scan_to(tmp_path / "a", capsys)
    new = scan_to(tmp_path / "b", capsys, "--all")
    assert "different DTC masks" in diff(capsys, old, new)


def test_diff_byte_changes_handles_length_change():
    assert scan.byte_changes("590601", "59060203") == [(2, 0x01, 0x02), (3, None, 0x03)]


def test_diff_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    try:
        scan.main(["diff", str(bad), str(bad)])
    except SystemExit as e:
        assert "Could not read scan file" in str(e)
    else:
        raise AssertionError("expected SystemExit")
