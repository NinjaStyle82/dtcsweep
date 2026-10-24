# CLAUDE.md: dtcsweep

Context for working on this repo. Read this first.

## What this is

dtcsweep is a **read-only** diagnostic trouble code (DTC) scanner for VW Group cars (MQB / MQB-Evo), written in Python for Linux SocketCAN adapters. It sweeps the OBD port for diagnostic modules on four address schemes, reads each module's identity and DTCs, and writes a color-coded terminal report, a plain `.log`, and a `.json` with all raw data.

- Repo: `git@github.com:NinjaStyle82/dtcsweep.git`. MIT license. Current release: v0.1.0.
- Tested on exactly one car: a 2026 VW Golf R (Mk8.5), Jhoinrch RH-02 PRO adapter (CANable 2.0-based, slcan firmware), 500 kbit/s classic CAN, Ubuntu.
- The maintainer edits and pushes from Windows (PowerShell) and runs scans from a separate Ubuntu laptop connected to the car.

## Hard rules

1. **Read-only, always.** The only services dtcsweep may send are:
   - UDS `0x3E 00` (TesterPresent), `0x22` (ReadDataByIdentifier), `0x19` with subfunctions `02`, `04`, `06` only
   - OBD `0x01`, `0x02`, `0x03`, `0x07`, `0x09`, `0x0A`

   Never add session control (`0x10`), security access (`0x27`), clear DTCs (`0x14`), OBD clear (Mode `0x04`), writes (`0x2E`), I/O control (`0x2F`), routines (`0x31`), resets (`0x11`), or any download/upload service. `tests/test_scan.py::test_read_only_guarantee` enforces this and must keep passing. If a feature seems to need a non-read service, stop and discuss it with the maintainer instead.
2. **Proprietary data and third-party notes.**
   - **Never in the repo:** VW label files, ODX/PDX databases, bulk exports, or text copied word for word from VCDS, ODIS, OBDeleven, erWin or similar tools.
   - **Allowed in the repo:** descriptions written in our own words, including facts learned from third-party tools (e.g. "VCDS reports this as a brake booster voltage fault", summarized in our own short wording). Each entry records its source (`public standard`, `observation`, `third-party tool`), a confidence level, and how many reports back it up. Output shows the source next to the description.
   - **Local only (gitignored):** anything imported from the user's own tool output, exact wording included (e.g. `--import-vcds`). Shown marked as `[your VCDS import]`.
3. **Never commit scan output or personal notes.** `.log` and `.json` files contain the VIN. `.gitignore` covers `dtcsweep-*.log`, `dtcsweep-*.json` and `CLAUDE.local.md`; keep it that way. `--redact` masks the VIN for sharing.
4. **Python 3.9 compatible.** No `match`, no backslashes inside f-string expressions, no 3.10+ typing syntax (`X | Y`). CI runs 3.9, 3.11 and 3.13.
5. **No new runtime dependencies** beyond `python-can>=4.0` and `can-isotp>=2.0` without asking. Use the standard library otherwise.
6. **Git workflow:** feature branches and PRs into `main`. Never push to `main` directly and never merge PRs. The maintainer tests on the real car first, then merges.
7. **Safety framing:** keep the battery, termination and wiring warnings intact in docs. Brake, airbag and steering faults should always point people to proper diagnostics.

## Layout

```
dtcsweep.py            the whole tool (single file, ~1000 lines)
tests/
  sim_car.py           simulated car: modules keyed by (is_extended, request_id)
  mocks/can/           minimal stand-in for python-can, backed by sim_car
  mocks/isotp/         minimal stand-in for can-isotp 2.x, backed by sim_car
  conftest.py          puts mocks first on sys.path; resets sim_car per test
  test_decoding.py     pure functions (DTC parsing, status, readiness, voltage)
  test_scan.py         full scans against the simulator, incl. read-only guarantee
  test_diff.py         diff command, changing the sim car between scans
docs/setup.md          hardware, Linux/Windows setup, troubleshooting
docs/interpreting.md   how to read output (codes, status bits, OBD, freeze frame, extended data)
examples/sample-output.txt   trimmed real output, VIN redacted
.github/workflows/tests.yml  pytest on 3.9/3.11/3.13; python-can is NOT installed (mocks only)
```

## Architecture (dtcsweep.py)

- `SCHEMES`: four address schemes.
  - `11vag`: `0x700`–`0x774`, response = request + `0x6A`
  - `11obd`: `0x7E0`–`0x7E7`, response = request + 8
  - `29obd`: `0x18DAxxF1` → `0x18DAF1xx`
  - `29vag`: `0x17FC00xx` → `0x17FE00xx`
- `functional_pid()`: functional OBD Mode 01 request on `0x7DF` / `0x18DB33F1`. `read_battery()` (PID `42`, median) and `read_rpm()` (PID `0C`, highest; running at 300 rpm or more) use it at start and end; JSON keys `battery` and `engine_rpm`. `--min-voltage` aborts before discovery.
- `discover()`: fire-and-collect raw single-frame `3E 00` to every candidate (~3 ms spacing), then listen 1 s. `discover_obd()`: functional `01 00`. `--slow`: real `19 02` probe per silent address.
- Per module, via `make_stack()` (can-isotp 2.x: `stack.start()`, `recv(block=True, timeout=...)`, `stack.stop()`) and `uds()`, which handles NRC `0x78` (pending) and `0x21` (busy):
  - `read_identity()`: DIDs `F197`, `F187`, `F189`, `F191`, `F19E`
  - `read_dtcs()`: `19 02` with mask `0x2D`; narrows to `0x08`, then `0x01`, on NRC `0x14`. Each DTC gets `failure_type` from `failure_type()` (`FTB_TYPES`, falling back to `FTB_CATEGORIES` by high nibble; own wording)
  - `read_details()`: `19 04 FF` and `19 06 FF` for the target DTC, or all active/confirmed with `--details`
  - `read_obd()` for OBD ECUs: `01 01` (MIL + readiness, `decode_monitor_status`), Modes `03`/`07`/`0A`, Mode `09` PIDs `0A`/`04`/`02`, Mode `02` freeze frame if anything is stored
- Output: `print_module()`, `print_cel()`, `print_summary()`. `out()` writes to the terminal and the log (ANSI stripped). JSON has every raw response.
- `diff_main()`: `python3 dtcsweep.py diff OLD.json NEW.json`. Compares modules, DTCs, status bits, OBD lists, CEL and readiness, identity (software changes = reflash), freeze frame, and byte-level extended/snapshot changes (index 0 = `0x59`, 2–4 = DTC, 5 = status).

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

All tests run against `tests/sim_car.py`; no hardware is needed. New behavior needs a simulator change plus tests. The mocks implement only what the scanner uses, so extend them when adding new python-can or can-isotp calls. `sim_car.SENT` records every request, for assertions about what was sent.

Real-car verification is manual, by the maintainer. When a change affects on-car behavior, say what to look for on the next real scan.

## Current state

- **main**: v0.1.0 (initial release).
- **feature/cel-battery-diff**: feature commits (plus this file), car-tested on 2026-10-24 (all features verified, engine off and running), awaiting CI and PR review:
  1. Which ECU is requesting the check engine light, plus readiness (Mode 01 PID 01). OBD-only ECUs labeled instead of "read failed".
  2. Battery voltage at start and end, with `--min-voltage`.
  3. `diff` command.
  4. Failure type byte decoding (`-XX` → plain words), shown as a hint.
  5. Fixes from the first car test (2026-10-24): `-01`..`-09` failure types, single-monitor wording, engine running/off with the battery reading, one summary line per OBD code (stored, permanent merged).

  The suite has 60 tests, all passing under real pytest on Python 3.13 (Windows). The first CI run (3.9/3.11/3.13) is on this branch's PR, so check it, especially 3.9.
- `CHANGELOG.md` has an "Unreleased" section for these.

## Known limitations

- Only one car tested. The 29-bit VAG scheme (`0x17FC00xx`) is confirmed on that car only.
- `--fd` and the `gs_usb` / Windows path are untested on real hardware.
- VW-internal DTCs print in SAE format but don't carry SAE meanings outside emissions-relevant codes. Extended data and snapshots are raw hex; layouts come from VW's database, which we don't have.
- Many modules refuse `19 04` (snapshots) in the default session (NRC `0x12`). We deliberately don't open other sessions.

## Backlog (rough priority)

1. **Direct slcan interface** (`--interface slcan --channel COM5`, python-can's serial backend) so Windows and macOS work without SocketCAN. Biggest reach win.
2. **PyPI packaging** with a `dtcsweep` console entry point.
3. **Observation export** (`--export-observations`: redacted module identities, codes, raw data, plus a "what was happening" field) and a GitHub issue template, as the start of a community code database.
4. **`--import-vcds autoscan.txt`**: parse a user's own VCDS Auto-Scan into a local, gitignored code→description map. Needs a real Auto-Scan sample before writing the parser; don't guess the format.
5. **Saved car profiles**: remember discovered addresses per car to skip discovery.
6. **Mode 06** monitor test results.
7. **Raw CAN trace option** (candump format) for replaying bug reports.
8. **Cross-module network-fault analysis**: group U-codes by the module they point at.
9. **Event grouping**: once extended-data mileage/timestamps are decoded, cluster faults from one event.
10. Later: read-only DID sweep for live data, watch mode (parked only, low rate), terminal UI, DoIP.

**Community data plan:** key entries by module identity (`F19E` ODX id + part number) plus DTC, since the same code number means different things in different modules. Record provenance (source type), confidence and report count per entry. License the data separately (CC BY 4.0). Own-words descriptions only, per rule 2; facts from third-party tools are fine, their wording is not.

## Maintainer preferences

- Explain changes plainly; the maintainer is learning CAN/UDS as the project goes.
- Small, focused commits with descriptive messages.
- Keep docs (README, `docs/`, CHANGELOG) updated in the same commit as the feature.
- Personal car context, if present, is in `CLAUDE.local.md` (gitignored).
