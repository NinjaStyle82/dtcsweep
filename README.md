# dtcsweep

A **read-only** diagnostic trouble code (DTC) scanner for VW Group cars, written in Python for Linux SocketCAN adapters.

It sweeps the OBD port for every diagnostic module it can reach, on both 11-bit and 29-bit CAN IDs. It reads each module's identity and fault codes, then prints a color-coded report plus a plain-text log and a JSON file with all the raw data.

It never clears, codes, adapts or writes anything. See [Read-only guarantee](#read-only-guarantee).

> **Tested on exactly one car:** a 2026 VW Golf R (Mk8.5, MQB-Evo), using the [hardware below](#hardware). Other VW Group cars on MQB / MQB-Evo will probably work, but nobody has confirmed that yet. Reports from other cars are very welcome (see [Contributing](#contributing)).

## Why

Generic OBD-II scanners only see emissions codes from the engine ECU. On newer VW Group cars, many modules sit on 29-bit diagnostic IDs that older and generic tools never ask. On the test car, an 11-bit-only scan missed the engine, the transmission, the radars, the 4Motion units, and the electric brake booster's OBD interface, which was the module holding the code being hunted.

dtcsweep checks four address schemes in a few seconds and reads everything that answers.

## Features

- **Fast discovery.** It sends TesterPresent to about 640 candidate addresses and collects whoever answers, which takes a few seconds:

  | Scheme     | Request          | Response         |
  |------------|------------------|------------------|
  | 11-bit VAG | `0x700`–`0x774`  | request + `0x6A` |
  | 11-bit OBD | `0x7E0`–`0x7E7`  | request + `0x08` |
  | 29-bit OBD | `0x18DAxxF1`     | `0x18DAF1xx`     |
  | 29-bit VAG | `0x17FC00xx`     | `0x17FE00xx`     |

  It also sends functional OBD requests on `0x7DF` and `0x18DB33F1`.
- **Identity per module:** system name, part number, software and hardware versions. For OBD-only ECUs it uses OBD Mode 09 to get the ECU name, calibration ID and VIN.
- **Only real faults shown.** DTCs are requested with a status mask, so the report shows codes that have actually failed, not the hundreds of supported-but-never-tested entries some modules list. `--all` shows everything.
- **VW fault numbers.** Codes from VW modules are shown as the fault number VW tools use (e.g. `369232 [05A250]`), so you can look them up in VCDS, ODIS or OBDeleven. OBD codes keep their SAE format (`U3001`).
- **Who turned on the check engine light.** Each emissions-relevant ECU reports whether it is requesting the light (OBD Mode 01 PID 01), so the summary names the module behind it, along with emissions readiness monitor status.
- **OBD Modes 03 / 07 / 0A** (stored, pending, permanent) for emissions-relevant ECUs, plus a **Mode 02 freeze frame** including module supply voltage.
- **Odometer and check engine light counters.** Odometer, distance and time driven with the light on, and distance and time since codes were cleared, at the top of every scan; `diff` shows the distance driven between scans.
- **Battery check.** Module supply voltage is read at the start and end of every scan, with a warning when it's low, and with whether the engine is running. `--min-voltage 12.2` refuses to scan below a threshold.
- **Extended data decoded.** For VW's common extended data record, dtcsweep shows how many times a fault was seen, its reset counter, and the mileage when it was last recorded (layout worked out against VCDS on the test car).
- **Target hunting:** `--target 369232` (or `0x05A250`, or `U3001` for OBD lists) flags one code wherever it appears and pulls its extended data (UDS 19 06) and snapshot (19 04).
- **Readable output.** Faults are color-coded by severity, there's a summary table, and a `.log` (plain text) and `.json` (all raw bytes) are written alongside.
- **Your own VCDS descriptions.** `dtcsweep.py import-vcds Log.txt` reads a VCDS Auto-Scan you made and shows its descriptions next to your codes, stored only on your machine.
- **Scan diff.** `dtcsweep.py diff OLD.json NEW.json` shows what changed between two scans: new and cleared codes, status changes, check engine light and readiness changes, software updates, and byte-level changes in extended data.
- **`--redact`** masks the VIN everywhere, for sharing logs publicly.

## Hardware

You need two things: a USB-to-CAN adapter that Linux sees as a SocketCAN interface, and a cable from the car's OBD-II port to the adapter's CAN terminals. The tested setup costs well under $50 in total.

### Tested setup

| Part | What was used | Notes |
|---|---|---|
| USB-CAN adapter | **Jhoinrch RH-02 PRO** isolated USB to CAN FD adapter ([Amazon](https://www.amazon.com/dp/B0FSVNBKQ4)) | Based on the open-source CANable 2.0. Ships with slcan firmware, so it works with no flashing. Signal and power isolation. Screw terminals labeled `CAN_H`, `GND`, `CAN_L`, `5V`. |
| OBD-II cable | **OBD2 J1962 16-pin male connector to open-end pigtail** | Sold under many brands. Search for "OBD2 male pigtail" or "OBD2 open end cable". Examples: iKKEGOL OBD2 male pigtail, ([Amazon](https://www.amazon.com/iKKEGOL-Connector-Diagnostic-Extension-Pigtail/dp/B0828YHWFG?crid=38I212527UUZY&dib=eyJ2IjoiMSJ9.9vYU3uV7PMfIn9p8sSsPbejbIzuHu6xFNVJgimuKxKPKP2GahirbcPnaTAa0pZbbzG7m1hte_Q9m943tKy87cMmAgnpgQmE_jzjUn8oPkZEnmls3kKsFLRajkhzHeuqpqL0ehzf4pSqULHWlk64WaAfj7LVDuREi0SPETD5HnmE8mgaNRIIn84_0Fuv48E3QY5VtBIF0w0uMTOZhdYarYaAn2s6hrGn4etiK-9mcD7i-97QiLnQAuxAlOxixrLG-2ohZi3ZIFLTY8L6JxE43Dwt6VgpaQpZTxgWttCkHlnI.R3q_tgV0ggNyp-E7yNplGTD0AOKZaEG6Usyh3agQxOw&dib_tag=se&keywords=odb+pigtail&qid=1792807811&sprefix=odb+pigtail%2Caps%2C205&sr=8-5)). |
| Computer | Laptop running Ubuntu | Any Linux with SocketCAN should work. |

**Why isolated:** an isolated adapter keeps the laptop's ground and the car's ground separate. That protects both ends from ground loops and voltage spikes, for example when the laptop is on its charger. Non-isolated CANable-style adapters work electrically, but isolation is cheap insurance when plugging a computer into a car.

### Other adapters

Anything with a mainline Linux SocketCAN driver should work, but these are **untested**:

- Other CANable 1.0 / 2.0 / 2.5 boards (slcan or candleLight firmware)
- candleLight-family adapters (`gs_usb` driver)
- PEAK PCAN-USB and Kvaser USB adapters (built-in kernel drivers)

ELM327-style Bluetooth/USB dongles **won't work**. They aren't SocketCAN devices and can't do the fast raw-frame discovery.

## Wiring

Connect only these three wires from the OBD-II pigtail to the adapter:

| OBD-II pin | Signal | Adapter terminal |
|---|---|---|
| **6** | CAN High | `CAN_H` |
| **14** | CAN Low | `CAN_L` |
| **5** | Signal ground | `GND` |

> ⚠️ **Wire colors are not standardized between sellers, and some listings have printed pin charts with mistakes.** Before connecting anything, use a multimeter in continuity mode to check which wire goes to pins 6, 14 and 5 on the connector itself. Pin numbers are molded into the face of most J1962 connectors.

> ⚠️ **Leave pin 16 disconnected and insulate its end.** Pin 16 is constant +12 V from the car's battery. Connecting it to the adapter's `5V` terminal or to `GND` can destroy the adapter or blow the car's OBD fuse. Tape or heat-shrink **every unused wire** individually so no bare ends can touch.

Two more things:

- **Termination off.** If your adapter has a 120 Ω termination switch or jumper, leave it off. The car's bus is already terminated.
- **Swapped wires are harmless but won't work.** If nothing answers, swapping CAN_H and CAN_L is the first thing to check.

## Setup

These steps are for Ubuntu with the adapter's stock slcan firmware. For candleLight firmware, Windows, and troubleshooting, see **[docs/setup.md](docs/setup.md)**.

### 1. Install (one time)

```bash
sudo apt update
sudo apt install git python3-venv can-utils

git clone https://github.com/NinjaStyle82/dtcsweep.git
cd dtcsweep

python3 -m venv ~/canenv
source ~/canenv/bin/activate
pip install -r requirements.txt
```

### 2. Connect

1. Wire the pigtail to the adapter as described in [Wiring](#wiring).
2. Plug the adapter into the laptop's USB port.
3. Plug the OBD-II connector into the car. On VW Group cars it's under the dash, left of the steering column.
4. Turn the **ignition on with the engine off**. On push-button cars, press start without your foot on the brake.

### 3. Bring up the CAN interface

```bash
ls /dev/ttyACM*                              # should show /dev/ttyACM0
sudo modprobe slcan
sudo slcand -o -c -s6 /dev/ttyACM0 can0      # -s6 = 500 kbit/s
sudo ip link set can0 up
sudo ip link set can0 txqueuelen 1000
```

If `modprobe slcan` reports the module is missing, run `sudo apt install linux-modules-extra-$(uname -r)` and try again.

### 4. Scan

```bash
source ~/canenv/bin/activate                 # in each new terminal
python3 dtcsweep.py
```

A full scan takes 15–30 seconds. Results print to the terminal and are saved as `dtcsweep-<timestamp>.log` and `.json` in the current directory.

### 5. Disconnect

```bash
sudo ip link set can0 down
sudo pkill slcand
```

Then turn the ignition off and unplug the OBD connector.

**Battery:** dtcsweep prints the module supply voltage at the start and end of each scan and warns when it's low. Each minute of ignition-on time drains the 12 V battery. Keep sessions short, or connect a battery maintainer for longer work. Low voltage can itself cause modules to log faults.

## Usage

```text
python3 dtcsweep.py [options]

  --target CODE      hunt one DTC (VW fault number 369232, hex 0x05A250, or OBD U3001) and pull its detail records
  --details          pull extended/snapshot data for every active or confirmed DTC
  --all              also list DTCs that have never failed
  --schemes LIST     comma list of 11vag, 11obd, 29obd, 29vag (default: all four)
  --slow             also probe silent addresses with a real DTC request
  --probe SECONDS    timeout per --slow probe (default 0.25)
  --redact           mask the VIN in all output
  --min-voltage V    refuse to scan if module voltage is below V (e.g. 12.2)
  --out-dir DIR      where to write the .log and .json (default: current dir)
  --fd               send CAN FD frames (needs an FD-capable adapter and firmware)
  --interface        socketcan (default) or gs_usb
  --channel          SocketCAN interface name (default can0)
  --no-color         plain terminal output
  --version
```

Examples:

```bash
python3 dtcsweep.py --target U3001             # where is U3001 stored?
python3 dtcsweep.py --details --redact         # full detail, safe to share
python3 dtcsweep.py --schemes 29obd            # quick emissions-ECU check
```

### Using your own VCDS scan

dtcsweep ships no VW descriptions. If you have VCDS, save an Auto-Scan as text and import it once:

```bash
python3 dtcsweep.py import-vcds Log-<VIN>-<mileage>.txt
```

The fault descriptions are stored in `~/.config/dtcsweep/vcds-import.json` (or `$DTCSWEEP_CONFIG_DIR`), matched by module part number and fault number. The VIN and mileage from the log are not stored. Later scans show them under each code:

```
  ●   369232 [05A250]  0xA8  confirmed, failed since clear, warning lamp
      [your VCDS import] U3001 00 · <VCDS's description>
```

With an import, `--target U3001` also finds VW codes that VCDS lists as U3001. Import newer logs any time; entries are updated, not duplicated. **Never commit this file or your VCDS logs**: the descriptions come from VCDS, and the logs contain your VIN.

### Comparing scans

```bash
python3 dtcsweep.py diff dtcsweep-20261024-012055.json dtcsweep-20261031-090412.json
```

The diff shows modules that started or stopped answering, new and cleared codes, status changes (for example a warning-lamp request switching off), OBD stored/pending/permanent changes, check engine light and readiness changes, and software version changes (usually a sign a module was reflashed).

For extended data and snapshots, it lists exactly which bytes changed. That's the main tool for working out what those records mean: scan regularly, and fields that move with the odometer, with each recurrence of a fault, or once per drive cycle reveal themselves.

See [examples/sample-output.txt](examples/sample-output.txt) for real output from the test car.

## Reading the results

**Read [docs/interpreting.md](docs/interpreting.md) before acting on anything dtcsweep prints.** In short:

- The letter-and-digit codes (P/C/B/U) only carry standard SAE meanings for emissions-relevant codes. Most VW modules use their own numbering in the same format. A "P0304" from the body control module is **not** a misfire.
- Extended data and snapshot records are printed as raw hex. Their layout comes from VW's diagnostic database, which this project doesn't have.
- Severity comes straight from the UDS status byte. "Confirmed" and "failed since clear" can be old history, not a current fault.

## Read-only guarantee

dtcsweep sends only these services:

| Service | Purpose |
|---|---|
| `0x3E 00` | TesterPresent (discovery ping) |
| `0x22` | ReadDataByIdentifier (identity) |
| `0x19 02 / 04 / 06` | ReadDTCInformation (list, snapshot, extended data) |
| OBD `0x01`, `0x02`, `0x03`, `0x07`, `0x09`, `0x0A` | Standard OBD-II reads |

It never sends session changes, security access, clear-DTC (`0x14`), writes (`0x2E`), routines (`0x31`) or resets (`0x11`). A test (`tests/test_scan.py::test_read_only_guarantee`) records every request during a simulated scan and fails if anything outside this list appears. Please keep that test passing in any contribution.

Read-only describes what dtcsweep *asks for*. It doesn't make physical-layer mistakes harmless: use the right bitrate (500 kbit/s classic CAN on the test car), keep termination off, and keep sessions short.

## How it works

1. **Discovery.** It sends a raw single-frame `3E 00` to every candidate address with ~3 ms spacing, collecting replies as they arrive, then listens for one more second. It also sends functional `01 00` (supported PIDs) on both OBD IDs to find emissions ECUs.
2. **Per module**, over ISO-TP (can-isotp 2.x), it reads identity DIDs `F197`, `F187`, `F189`, `F191` and `F19E`. Then it reads `19 02` with mask `0x2D` (failing now / pending / confirmed / failed since clear). If a module answers `responseTooLong`, the mask is narrowed automatically.
3. **For OBD-capable ECUs**, it reads Mode 01 PID 01 (check engine light and readiness), Modes 03, 07 and 0A, Mode 09 PIDs 0A, 04 and 02, and a Mode 02 freeze frame when anything is stored.
4. **Results** go to the terminal, a plain-text `.log`, and a `.json` with every raw response.

## Contributing

Reports from other cars are the most useful contribution. Run with `--redact`, and open an issue with:

- the car (model, year, platform if known),
- your adapter and firmware,
- the `.log` (check it for anything personal first).

If an address scheme is missing for your car, the `--slow` probe output helps identify it.

**Development.** The test suite runs against a simulated car, so no hardware is needed:

```bash
pip install -r requirements-dev.txt
pytest
```

The simulated car lives in `tests/sim_car.py`, with minimal stand-ins for `python-can` and `can-isotp` in `tests/mocks/`. Add modules to the simulator to cover new behavior.

Ground rules: keep it read-only, and keep the read-only test passing.

**Code descriptions and third-party tools.** Notes based on what VCDS, OBDeleven, ODIS or similar tools show are welcome, with these limits:

- **Write descriptions in your own words.** Facts learned from a third-party tool are fine ("VCDS shows this as a brake booster voltage fault" becomes your own short summary). Text copied word for word from those tools is not.
- **Say where it came from.** Each description records its source (public standard, your own observation, or a third-party tool), how confident you are, and how many reports back it up. dtcsweep shows that source next to the description.
- **Never add proprietary data:** VW label files, ODX/PDX databases, or bulk exports from any tool.

Anything imported from your own tool output (exact wording included) stays on your machine in a gitignored file and is never committed.

## License and disclaimer

MIT. See [LICENSE](LICENSE).

This project is not affiliated with or endorsed by Volkswagen AG, any VW Group brand, or any hardware vendor mentioned here. Brand and product names are used only to describe compatibility. Hardware links are for convenience; none are affiliate links.

It is provided as-is, without warranty. You connect it to your own car at your own risk. It is not a substitute for dealer diagnostics: if a module requests a warning lamp, or anything brake-, airbag- or steering-related shows up, get it checked properly.
