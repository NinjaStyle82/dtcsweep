import json
import os

import dtcsweep as scan

# Synthetic log in VCDS's Auto-Scan layout, with made-up descriptions
# (rule 2: no VCDS text in the repo), cp1252-encoded like real logs.
SAMPLE = os.path.join(os.path.dirname(__file__), "fixtures", "vcds-sample.txt")


def run(tmp_path, capsys, *args):
    scan.main(["--out-dir", str(tmp_path), "--no-color", *args])
    return capsys.readouterr().out


def test_parse_vcds_log():
    e = scan.parse_vcds_log(SAMPLE)
    assert set(e) == {"5WA906259XX", "5WA614517XX", "5WA909059XX"}
    booster = e["5WA909059XX"]["369232"]
    assert booster["sae"] == "U3001 00"
    assert booster["text"] == "Example: booster lost power before saving its state"
    assert booster["module"] == "23 Brake Booster" and booster["component"] == "EBKV"
    assert "symptom" not in booster
    assert e["5WA614517XX"]["8730"]["symptom"] == "Example Symptom"
    assert e["5WA906259XX"]["29244"]["tag"] == "example_internal_tag"
    assert e["5WA906259XX"]["29244"]["component"] == "R4 2.0l TFS"


def test_import_stores_locally_without_vin(capsys):
    assert scan.main(["import-vcds", SAMPLE]) == 0
    out = capsys.readouterr().out
    assert "3 fault description(s) for 3 module(s)" in out
    with open(scan.vcds_import_path(), encoding="utf-8") as f:
        raw = f.read()
    assert "WVWZZZCDZTW000001" not in raw and "1659" not in raw
    assert json.loads(raw)["faults"]["5WA909059XX"]["369232"]["sae"] == "U3001 00"


def test_import_twice_does_not_duplicate(capsys):
    scan.main(["import-vcds", SAMPLE])
    scan.main(["import-vcds", SAMPLE])
    assert len(scan.load_vcds_import()["5WA909059XX"]) == 1


def test_scan_shows_imported_descriptions(tmp_path, capsys):
    scan.main(["import-vcds", SAMPLE])
    capsys.readouterr()
    text = run(tmp_path, capsys)
    assert ("[your VCDS import] U3001 00 · Example: booster lost power before saving its state"
            in text)
    assert "U045A 00 · Example: implausible data from the parking module · Example Symptom" in text


def test_scan_without_import_has_no_vcds_lines(tmp_path, capsys):
    assert "[your VCDS import]" not in run(tmp_path, capsys)


def test_sae_target_finds_uds_code_through_import(tmp_path, capsys):
    scan.main(["import-vcds", SAMPLE])
    capsys.readouterr()
    text = run(tmp_path, capsys, "--target", "U3001")
    assert "U3001 FOUND in 2 module(s)" in text      # EBKV via import, EBBC via OBD
    assert "EBKV [0x73B]  369232" in text
