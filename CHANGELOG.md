# Changelog

## Unreleased

- Show which ECU is requesting the check engine light, and emissions readiness monitor status (OBD Mode 01 PID 01)
- Battery voltage check at the start and end of each scan, with `--min-voltage` to refuse low-voltage scans; engine running/off (OBD PID 0C) shown alongside, and `diff` notes when it differs between scans
- `diff` command to compare two scans, including byte-level extended data changes
- Failure type byte (the `-XX` after a DTC) decoded into plain words from the public SAE/ISO scheme, marked as a hint since VW-internal codes may not follow it
- OBD-only ECUs are labelled as such instead of "read failed"

## 0.1.0

First public release (developed as vag-dtc-scan).

- Discovery across 11-bit VAG, 11-bit OBD, 29-bit OBD and 29-bit VAG address schemes
- Per-module identity (UDS DIDs, or OBD Mode 09 for OBD-only ECUs)
- DTC reads filtered to real faults, with automatic mask narrowing on `responseTooLong`
- OBD Modes 03/07/0A and Mode 02 freeze frame
- `--target`, `--details`, `--redact`, `--out-dir`
- Test suite against a simulated car, including a read-only guarantee test
