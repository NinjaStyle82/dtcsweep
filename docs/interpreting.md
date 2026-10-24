# Interpreting the output

## DTC codes

Each UDS DTC is 3 bytes, printed as `P0072-3C`: a letter, four hex digits, then the failure-type byte.

The letter comes from the top two bits of the first byte: `P` powertrain, `C` chassis, `B` body, `U` network.

**Standard SAE meanings apply only to emissions-relevant codes** (most `P0xxx` codes from the engine and transmission, plus codes reported through OBD Modes 03/07/0A). VW modules use their own internal numbering in the same 3-byte format. So a `P0304-0C` from the body control module, or a dozen `P0007-xx` from the ACC radar, are VW-specific codes that happen to print that way. They are not misfires or fuel-system faults. Looking them up in a generic OBD code list will give wrong answers.

To get proper descriptions for VW-internal codes, use VW-aware tools (ODIS, VCDS, OBDeleven). This project deliberately ships no VW label data.

### Failure type (the part after the dash)

The last byte, `-50` in `P05A2-50`, says *how* something failed. Public standards (SAE J2012 / ISO 14229) give it a structure that isn't VW-specific, so dtcsweep prints a `type (SAE scheme, may not apply):` line under each code:

| First hex digit | Category |
|---|---|
| `0` | general: `-01` to `-09` mean a general fault of category 1 to 9 (`-02` general signal fault); `-00` and `-0A` to `-0F` carry no detail, so nothing is printed |
| `1` | electrical circuit, e.g. `-11` short to ground, `-13` open circuit |
| `2` | signal, e.g. `-23` stuck low, `-29` invalid |
| `3` | frequency/PWM signal |
| `4` | module internal, e.g. `-49` internal electronics fault |
| `5` | programming/configuration, e.g. `-54` calibration missing |
| `6` | plausibility/algorithm, e.g. `-64` signal implausible |
| `7` | mechanical, e.g. `-71` actuator stuck |
| `8` | bus/network message, e.g. `-87` message missing |
| `9` | component, e.g. `-92` performance or wrong operation |
| `F` | manufacturer-specific |

When dtcsweep knows the specific value it prints that (`short to ground`). Otherwise it prints the category with "(category only)". Treat it as a hint about the kind of fault, not a diagnosis: manufacturers don't always follow the standard for their internal codes, so a VW-internal code's failure type can mean something else. On the test car, the ACC radar uses about 15 different failure types on the same `P0007` code, which suggests VW's own meanings there. The descriptions are written in our own words.

### The same fault twice

The same fault can appear twice in different forms. On the test car, the electric brake booster reported `P05A2-50` over UDS and `U3001` over OBD, which was very likely the same event. Match modules by calibration ID or part number to spot this. The OBD-only address (`0x18DA03F1`, named `EBBC-ElecBrkBstCtrl`) and the UDS module (`EBKV` at `0x73B`) had matching part and software numbers.

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

## Check engine light and readiness

Each emissions-relevant ECU reports (OBD Mode 01 PID 01) whether it is currently requesting the check engine light, plus the status of its readiness monitors. The summary names every ECU requesting the light, which is the direct answer to "what turned my CEL on?". It's often not the engine: on the test car it was the electric brake booster.

**Readiness monitors** are the self-tests behind emissions inspections (catalyst, evaporative system, O2 sensors and so on). "Not ready" means a test hasn't finished since codes were last cleared or the battery was disconnected. That isn't a fault: monitors complete on their own over normal driving. It can matter for inspections, though, since many programs fail a car with too many monitors not ready. Non-engine ECUs often report just one monitor (on the test car, the transmission and both brake ECUs each report one), or none, which shows as "no monitors supported".

## Freeze frame (Mode 02)

The freeze frame records values at the moment the fault was detected. Two things to know:

- **Values a module doesn't have come back as filler.** For example, 16383 rpm and 255 km/h are all-ones bytes, and −40 °C is a zero byte. A brake booster has no engine speed, so ignore those values.
- **Module voltage is usually the useful one,** especially for shutdown or low-voltage codes. Improper-shutdown codes like U3001 are typically detected at the next power-up, so the voltage shown is at wake-up, not at the moment of the bad shutdown.

## Extended data and snapshots (UDS 19 06 / 19 04)

These are printed as raw hex, for example:

```
59 06 | 05 A2 50 | A8 | 01 02 | 06 02 28 00 01 76 ...
reply   DTC        status  record 01   record 06 ...
```

The record numbers and field layouts come from VW's diagnostic database and are **not self-describing**. Common contents include an occurrence counter, a healing (aging) counter (often counting toward 40), and mileage or a timestamp. Treat any byte-level reading as a guess unless a VW-aware tool confirms it.

To work out what a record means, compare scans over time with `dtcsweep.py diff`. It prints the index of every byte that changed, counting from the start of the response (byte 0 is `0x59`, bytes 2–4 are the DTC, byte 5 is its status). For example:

```
~ P05A2-50  extended  bytes [5] A8→28, [7] 02→03, [10] 28→27
```

Here byte 5 is the status (warning lamp request cleared), byte 7 went up by one when the fault recurred (an occurrence counter is a good guess), and byte 10 went down by one (a healing counter counting drive cycles is a good guess). Several scans that agree turn a guess into a finding.

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
