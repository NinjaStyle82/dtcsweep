# Interpreting the output

## DTC codes

Each UDS DTC is 3 bytes, printed as `P0072-3C`: a letter, four hex digits, then the failure-type byte.

The letter comes from the top two bits of the first byte: `P` powertrain, `C` chassis, `B` body, `U` network.

**Standard SAE meanings apply only to emissions-relevant codes** (most `P0xxx` codes from the engine and transmission, plus codes reported through OBD Modes 03/07/0A). VW modules use their own internal numbering in the same 3-byte format. So a `P0304-0C` from the body control module, or a dozen `P0007-xx` from the ACC radar, are VW-specific codes that happen to print that way. They are not misfires or fuel-system faults. Looking them up in a generic OBD code list will give wrong answers.

To get proper descriptions for VW-internal codes, use VW-aware tools (ODIS, VCDS, OBDeleven). This project deliberately ships no VW label data.

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
