# Interpreting the output

## DTC codes

### VW modules: fault numbers

Each UDS DTC from a VW module is 3 bytes. VW modules don't use the SAE layout (a letter, four digits and a failure-type byte); the 3 bytes are one **VW fault number**. dtcsweep prints it the way VW tools such as VCDS do, as a decimal number, with the raw bytes in brackets:

```
  ●   369232 [05A250]  0xA8  confirmed, failed since clear, warning lamp
```

That number is what you look up in a VW-aware tool (ODIS, VCDS, OBDeleven). Those tools also show an SAE-style code and a description, but both come from VW's database, not from the bytes, so dtcsweep can't derive them. If you have your own VCDS Auto-Scan, `dtcsweep.py import-vcds` shows its descriptions next to your codes, marked `[your VCDS import]` (see the README).

Printing the bytes in SAE layout gives misleading results. On the test car, the brake booster's `05 A2 50` would print as "P05A2-50" but is VCDS's 369232, improper shutdown (U3001). The engine's `00 72 3C` would print as "P0072-3C", which looks like the SAE ambient air temperature code, but it is 29244: the engine ECU recording that another module asked for the check engine light. Only 1 of 46 faults on that car happened to line up with its SAE-style code.

To hunt one code, use `--target 369232` or `--target 0x05A250`.

### OBD codes

Codes from OBD Modes 03/07/0A (and the freeze frame trigger) are 2 bytes in the standard SAE format, printed as e.g. `U3001`. These do carry SAE meanings: the letter is the system (`P` powertrain, `C` chassis, `B` body, `U` network) and `P0xxx` codes have standard descriptions. `--target U3001` finds them.

### The same fault twice

The same fault can appear twice in different forms. On the test car, the electric brake booster reported 369232 over UDS and `U3001` over OBD; VCDS confirms both are the same improper-shutdown fault. Match modules by calibration ID or part number to spot this. The OBD-only address (`0x18DA03F1`, named `EBBC-ElecBrkBstCtrl`) and the UDS module (`EBKV` at `0x73B`) had matching part and software numbers.

## Status byte and severity

| Bit | Meaning |
|---|---|
| `0x01` | test failed (failing right now) |
| `0x02` | test failed this operation cycle |
| `0x04` | pending |
| `0x08` | confirmed |
| `0x10` | test not completed since last clear |
| `0x20` | test failed since last clear |
| `0x40` | test not completed this operation cycle |
| `0x80` | warning indicator requested (module wants a dash lamp on) |

The scanner groups these into severities:

- **active** (red): failing now, or warning lamp requested
- **confirmed / pending** (yellow): stored fault
- **history** (cyan): failed at some point since the last clear, not confirmed
- **untested** (hidden unless `--all`): the module supports this code but hasn't seen it fail

By default, DTCs are requested with mask `0x2D`, so untested entries never appear. Some modules list hundreds of supported codes when asked with `0xFF`, which is what `--all` does.

## OBD sections

For emissions-relevant ECUs:

- **stored** (Mode 03): confirmed codes, the ones that turn on the check engine light
- **pending** (Mode 07): failed once, not yet confirmed
- **permanent** (Mode 0A): can't be cleared with a scan tool; the ECU clears it after re-running the test and passing

The check engine light goes off after a few fault-free drive cycles, but the stored code can stay for up to 40 warm-up cycles, and a permanent code stays until the ECU clears it. A light that "keeps clearing itself and coming back" often means one long-lived fault, not several separate ones.

Non-engine modules (brakes, transmission) can be emissions-relevant and can turn on the check engine light.

## Battery voltage

dtcsweep asks the OBD ECUs for their supply voltage (OBD PID 42) at the start and end of each scan, and engine speed (PID 0C) to tell whether the engine is running. With the engine off:

| Reading | Meaning |
|---|---|
| 13.2 V and up | engine running or a charger connected |
| 12.4 V and up | healthy |
| 12.2–12.4 V | partly discharged; fine for a short scan |
| 11.8–12.2 V | low; use a battery maintainer |
| below 11.8 V | very low; charge before scanning |

With the engine running, the alternator holds the voltage around 13.5–14.5 V, so the reading says nothing about the battery's health; dtcsweep says so. For a battery check, scan with the ignition on and the engine off.

This is the voltage *at the module*, which typically reads slightly below a meter on the battery terminals because of wiring losses. A large drop between the start and end of a short scan can point at a weak battery. Low voltage matters because modules log their own faults when supply voltage sags, which can send you chasing problems that are really the battery.

## Odometer and check engine light counters

Under the battery line, dtcsweep shows these readings, each only if some ECU reports it:

- **Odometer** (OBD PID A6), in km and miles.
- **Driven with check engine light on** (PIDs 21 and 4D): distance and minutes since the light came on. When the light goes off, the counter keeps its last value until the light comes on again, so with the light off dtcsweep shows it as "last time it was on, it stayed on for".
- **Since codes were last cleared** (PIDs 31 and 4E): distance and minutes since someone (or a module, after enough clean drive cycles) last cleared emissions codes.

Each value is the highest any ECU reported. The distance counters stop at 65,535 km.

These make `diff` much more useful. The diff prints how far the car went between two scans, and whether the check-engine-light distance grew. A "since codes cleared" value that went *down* means codes were cleared in between. To test whether bytes in an extended data record are a mileage stamp, scan before and after a drive: a mileage stamp only changes when the fault recurs, and then it should jump to roughly the current odometer.

## Check engine light and readiness

Each emissions-relevant ECU reports (OBD Mode 01 PID 01) whether it is currently requesting the check engine light, plus the status of its readiness monitors. The summary names every ECU requesting the light, which is the direct answer to "what turned my CEL on?". It's often not the engine: on the test car it was the electric brake booster.

**Readiness monitors** are the self-tests behind emissions inspections (catalyst, evaporative system, O2 sensors and so on). "Not ready" means a test hasn't finished since codes were last cleared or the battery was disconnected. That isn't a fault: monitors complete on their own over normal driving. It can matter for inspections, though, since many programs fail a car with too many monitors not ready. Non-engine ECUs often report just one monitor (on the test car, the transmission and both brake ECUs each report one), or none, which shows as "no monitors supported".

## Freeze frame (Mode 02)

The freeze frame records values at the moment the fault was detected. Two things to know:

- **Values a module doesn't have come back as filler.** For example, 16383 rpm and 255 km/h are all-ones bytes, and −40 °C is a zero byte. A brake booster has no engine speed, so ignore those values.
- **Module voltage is usually the useful one,** especially for shutdown or low-voltage codes. Improper-shutdown codes like U3001 are typically detected at the next power-up, so the voltage shown is at wake-up, not at the moment of the bad shutdown.

## Extended data and snapshots (UDS 19 06 / 19 04)

These are printed as raw hex. VW's extended data record 01 has a layout we worked out by comparing the test car's bytes with a VCDS freeze frame for the same fault, so dtcsweep also prints it decoded:

```
      extended: 590605a250a80102060228000176000000000000
      decoded (VW layout, from a VCDS comparison): priority 2  ·  seen 6 times  ·  reset counter 40  ·  at 374 km (232 mi)  ·  date not set
```

| Byte | Meaning |
|---|---|
| 0–1 | `59 06`, the reply to "read extended data" |
| 2–4 | the DTC (VW fault number) |
| 5 | its status byte |
| 6 | record number `01` |
| 7 | priority (VW's ranking; lower is more important) |
| 8 | frequency: how many times the fault has been seen |
| 9 | unknown |
| 10 | reset counter: counts down while the fault stays away; the code can be erased when it runs out |
| 11–13 | mileage in km when the fault was last recorded |
| 14–19 | date and time; blank in the only sample, so the layout isn't confirmed and dtcsweep shows these bytes raw |

Source and confidence: one module on one car, checked against a third-party tool (VCDS). It's printed only when the reply has exactly this shape. Other modules may use other layouts, so check the decoded values against a VW-aware tool before relying on them.

`dtcsweep.py diff` prints the index of every byte that changed (byte 0 is `0x59`) and, for decodable records, the decoded changes:

```
~ 369232    extended  bytes [5] A8→28, [8] 06→07, [10] 28→27
~ 369232    decoded   times seen 6 → 7, reset counter 40 → 39
```

Here the warning lamp request cleared (status byte), the fault was seen once more, and the reset counter dropped by one. Scans over time are also how to work out byte 9 and the date layout.

Many modules refuse snapshots (`19 04`) in the default diagnostic session and answer NRC `0x12`. This scanner doesn't open other sessions, to stay strictly read-only.

## Negative response codes

| NRC | Meaning |
|---|---|
| `0x11` | service not supported (normal for OBD-only ECUs asked for UDS) |
| `0x12` | subfunction not supported |
| `0x14` | response too long (handled by narrowing the mask) |
| `0x22` | conditions not correct |
| `0x31` | request out of range (DID not available) |
| `0x33` | security access denied |
| `0x78` | response pending (handled automatically; never shown) |
