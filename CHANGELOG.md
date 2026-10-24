# Changelog

## 0.1.0

First public release (developed as vag-dtc-scan).

- Discovery across 11-bit VAG, 11-bit OBD, 29-bit OBD and 29-bit VAG address schemes
- Per-module identity (UDS DIDs, or OBD Mode 09 for OBD-only ECUs)
- DTC reads filtered to real faults, with automatic mask narrowing on `responseTooLong`
- OBD Modes 03/07/0A and Mode 02 freeze frame
- `--target`, `--details`, `--redact`, `--out-dir`
- Test suite against a simulated car, including a read-only guarantee test
