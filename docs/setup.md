# Setup

## Hardware

See the **Hardware** and **Wiring** sections of the [README](../README.md#hardware) for the tested adapter, the OBD-II cable, and the pin connections. The short version:

- **Pins:** OBD-II pin 6 to `CAN_H`, pin 14 to `CAN_L`, pin 5 to `GND`. Verify with a multimeter, since wire colors vary.
- **Pin 16 (+12 V) stays disconnected and insulated.**
- **Termination off.**
- **Bus:** 500 kbit/s classic CAN. The OBD side of the gateway on the test car is classic CAN, even though the car uses CAN FD internally.

## Linux

```bash
sudo apt update
sudo apt install python3-venv can-utils
python3 -m venv ~/canenv
source ~/canenv/bin/activate
pip install -r requirements.txt
```

Recent Ubuntu releases block system-wide `pip install`, which is why this uses a virtual environment. Rerun `source ~/canenv/bin/activate` in each new terminal, or call `~/canenv/bin/python` directly.

### Option A: slcan firmware (stock CANable firmware)

The adapter appears as `/dev/ttyACM0`. Turn it into `can0`:

```bash
sudo modprobe slcan
sudo slcand -o -c -s6 /dev/ttyACM0 can0   # -s6 = 500 kbit/s
sudo ip link set can0 up
sudo ip link set can0 txqueuelen 1000
```

When you're done: `sudo pkill slcand`.

slcan through the kernel supports classic CAN only, which is what the test car needs.

### Option B: candleLight / gs_usb firmware

The adapter appears directly as `can0`:

```bash
sudo modprobe gs_usb
sudo ip link set can0 down 2>/dev/null
sudo ip link set can0 up type can bitrate 500000 restart-ms 100
sudo ip link set can0 txqueuelen 1000
```

If `modprobe` says the module isn't found, install `linux-modules-extra-$(uname -r)` (this also applies to `slcan`).

**Before reflashing a CANable 2.0:** mainline candleLight firmware doesn't support the STM32G4 chip on the CANable 2.0. Builds for it come from the CANable project's own web updater. If your adapter is a third-party product built around a CANable, check its vendor's documentation first. Option A needs no flashing at all.

### Which firmware do I have?

```bash
ls /dev/ttyACM*          # present -> slcan firmware (Option A)
ip link show can0        # present without slcand -> gs_usb firmware (Option B)
sudo dmesg | tail -20    # right after plugging in, shows how it enumerated
```

## Windows

Untested. In principle:

1. Use [Zadig](https://zadig.akeo.ie) to install the WinUSB driver for a candleLight-firmware adapter.
2. Install the dependencies: `pip install -r requirements.txt gs_usb libusb-package`.
3. Run `python dtcsweep.py --interface gs_usb`.

The gs_usb backend is classic CAN only. WSL2's stock kernel doesn't include `gs_usb` or `slcan`, so USB passthrough into WSL won't work without a custom kernel.

## Running

Ignition on, engine off. Keep sessions short or use a battery maintainer.

```bash
python3 dtcsweep.py
```

While it runs, `candump can0` in a second terminal shows the raw traffic. That's useful if nothing answers.

## Troubleshooting

| Symptom | Things to check |
|---|---|
| Nothing answers | Ignition on? `can0` up at 500k (`ip -details link show can0`)? CAN-H and CAN-L swapped? Then try `--slow`, then `--fd`. |
| Only 11-bit modules found | Make sure `--schemes` includes `29obd` and `29vag` (both are on by default). |
| A module answers but its DTC read fails with NRC `0x11` | Normal for OBD-only ECUs. Their codes come from the OBD Mode 03/07/0A lines instead. |
| `responseTooLong` note | Handled automatically by narrowing the status mask. |
| `No buffer space available` | `sudo ip link set can0 txqueuelen 1000` |
| Error frames in `candump` | Wrong bitrate, missing ground, or termination switched on. Stop and fix that before scanning. |
