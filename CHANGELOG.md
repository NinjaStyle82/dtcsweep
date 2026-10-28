# Changelog

## Unreleased

- Show which ECU is requesting the check engine light, and emissions readiness monitor status (OBD Mode 01 PID 01)
- Battery voltage check at the start and end of each scan, with `--min-voltage` to refuse low-voltage scans; engine running/off (OBD PID 0C) shown alongside, and `diff` notes when it differs between scans
- Odometer (OBD PID A6) and check engine light counters (PIDs 21, 4D, 31, 4E) at the top of each scan, compared by `diff`
- `diff` command to compare two scans, including byte-level extended data changes
- Snapshots: when a module refuses all records at once, dtcsweep reads the record numbers it lists (`19 03`), or records 01/02
- `--extended-session` (opt-in): retries refused snapshots in the extended diagnostic session (`10 03`, then `10 01`); engine off and stationary only, never the airbag module
- `import-vcds` command: descriptions from your own VCDS Auto-Scan, stored locally (never in the repo) and shown next to matching codes; `--target U3001` also finds VW codes VCDS lists as U3001
- VW extended data record 01 decoded (priority, times seen, reset counter, mileage), with decoded changes in `diff`
- VW module codes are shown as VW fault numbers (e.g. `369232 [05A250]`, as VCDS shows them) instead of SAE-style codes, which were misleading for VW-internal numbering; `--target` accepts the number or its hex
- OBD-only ECUs are labelled as such instead of "read failed"

## 0.1.0

First public release (developed as vag-dtc-scan).

- Discovery across 11-bit VAG, 11-bit OBD, 29-bit OBD and 29-bit VAG address schemes
- Per-module identity (UDS DIDs, or OBD Mode 09 for OBD-only ECUs)
- DTC reads filtered to real faults, with automatic mask narrowing on `responseTooLong`
- OBD Modes 03/07/0A and Mode 02 freeze frame
- `--target`, `--details`, `--redact`, `--out-dir`
- Test suite against a simulated car, including a read-only guarantee test
