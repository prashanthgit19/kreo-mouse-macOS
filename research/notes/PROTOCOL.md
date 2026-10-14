# Kreo Hawk Pro — HID protocol notes

Reverse-engineered from Kreo's own Kontrol driver. No USB sniffing and no
Windows machine were required: Kreo ships the driver as WebAssembly, so we run
it offline as an oracle (`research/oracle/`) and record the frames it emits.

Status: DPI (values + stage switch), lighting (mode/brightness/speed), report
rate, lift-off distance and sensor toggles are implemented and verified on real
hardware. Macros, button remapping and sleep are not yet implemented.

## Device

| | |
|---|---|
| Vendor | `0x04F3` (1267) |
| Wired product | `0x026E` (622) |
| 2.4G dongle product | `0x026F` (623) |
| Protocol family | `yuandaxin` |
| Driver id | `yuandaxin:hawk-pro:v1` |
| Sensor | PixArt PAW3395 |

Two HID interfaces:

* interface 0 — mouse (`usage page 1 / usage 2`), input only, no feature reports.
* interface 1 — config interface. Top-level collections: keyboard (`1:6`),
  consumer (`0x0C:1`), system control (`1:0x80`), vendor (`0x0A:0`).
  This is where all configuration happens.

### Why the Kontrol web app fails on macOS

The config interface exposes a **keyboard** collection. Chromium's WebHID
refuses to open any interface that contains a protected usage like a keyboard
(Kreo's own error map calls this `WEBHID_OPEN_OS_REFUSED`). Windows Chrome is
allowed to, which is why the app is "Windows only". A native process using
IOKit is not subject to that restriction — `IOHIDDeviceOpen(..., None)`
succeeds, no root required.

(`hidapi` on macOS failed to open these nodes; we use IOKit directly.)

## Report descriptor (wired)

```
05 0A 09 00 A1 01 09 01 85 04 ... 81 02 C0   input report  4 : 31 bytes
05 0A 09 00 A1 01 09 01 85 05 ... B1 02       feature report 5 : 31 bytes
09 01 85 06 95 1F B1 02                        feature report 6 : 31 bytes  <-- commands
09 01 85 BC 95 07 B1 02                       feature report 0xBC : 7 bytes
09 01 85 BD 95 87 B1 02                       feature report 0xBD : 135 bytes (wired only)
09 01 85 BE 95 87 B1 02                       feature report 0xBE : 135 bytes
09 01 85 BF 95 87 B1 02                       feature report 0xBF : 135 bytes
```

The dongle exposes only reports 4, 5, 6 and 0xBC (no 135-byte reports), and its
report 6 returns an all-zero frame where the wired device returns `06 01 00...`.

## IOKit gotcha

`IOHIDDeviceSetReport` requires the report ID **as the first byte of the write
buffer** for devices with multiple reports (per `IOHIDDevice.h`):

> reportID … If the device supports multiple reports, this should also be set
> in the first byte of the report.

So a write is `[0x06] + 31 payload bytes` with `reportLength = 32`. Omitting the
leading byte means the command byte is interpreted as the report ID — a DPI
frame (command `0x02`) then lands on report 2 and is silently dropped. This was
the single hardest bug in the project. `research/hidtool` handles it.

## Command frames (feature report 6)

```
byte 0 : command
byte 1 : sequence counter (increments per frame; the device does not enforce it)
byte 2 : frame flag — 0x01 single frame; 0x02 / 0x12 for the two frames of a DPI write
byte 3 : checksum = (reportId + sum(all other bytes)) & 0xFF     # note the +6
byte 4..30 : payload (27 bytes)
```

Checksum verified against every frame captured from the driver.

### 0x02 — DPI stages (two frames: flags `0x02` then `0x12`)

```
[0]    current stage (0-based)
[1]    active-stage bitmask (0x3F = 6 stages)
[2..17] 8 × uint16 little-endian, code = (dpi - 50) / 50   (50..26000, step 50)
[18..26] 0xFF
```

### 0x03 — DPI stage colours (single frame)

```
[0..23]  8 × RGB
[24..26] 0xFF
```

### 0x04 — report rate

```
[0]    1000 / hz   (125→8, 250→4, 500→2, 1000→1)
[1..30] 0x00
```

### 0x05 — lift-off distance

```
[0]    lod index (1 or 2)
[1..30] 0xFF
```

### 0x06 — lighting

```
[0]    0xFF
[1]    mode id
[2]    (brightness << 4) | speed
[3]    direction
[4..6] R, G, B   (ignored — no mode supports colour)
[7..30] 0xFF
```

Modes: `0` Off, `1` Rainbow Flow, `2` Breathe, `3` Steady, `4` Neon, `5` Marquee,
`6` Rainbow Steady, `7` Wave. Only `3` and `6` support brightness; `1,2,4,5,7`
support speed; `1` supports direction. No mode supports colour, so the mouse
shows a fixed colour per mode.

### 0x07 — sleep timer

The driver always emits `[0x01, 0x01, 0x00]` whatever `sleepIndex` is passed, so
the timeout does not appear to be settable through this frame. Unresolved.

```
[0..2]  0x01, 0x01, 0x00
[3..30] 0xFF
```

### 0x08 — sensor

```
[0]    angle snap (0/1)
[1]    motion sync (0/1)
[2]    ripple (0/1)
[3..30] 0xFF
```

## Reading state

The device pushes input report 4 (31 bytes) — e.g. `01 03 01 00 02 05 00 01 06 …`
— spontaneously and on button presses, but the payload does not change with
settings, and feature reports 5/6 return static data. **There is no working
read-back channel**, so `hawkctl` keeps a local model of device state in
`~/.config/hawkctl/state.json`. Only report 5 exposes something useful:
`05 31 30 31 30 …` ("1010", probably a firmware/device id).

## Capabilities (from the driver's own schema)

* DPI: 50–26000, step 50, up to 6 active stages, per-stage colours, DPI lock.
* Report rate: 125 / 250 / 500 / 1000 Hz.
* Sensor: motion sync, LOD 1/2, ripple, angle snap. No angle-tune, performance
  mode or 20k-fps support.
* Lighting: single zone (device manifest has `zones: []`).
* 8 buttons, 8 macro slots (80 actions each), 3 execution types.
* Battery reporting (`levelPercent`, `charging`, `voltageMv`).
* `supportsFactoryReset: true`.

## Method (to reproduce/extend)

```bash
# 1. load the vendor driver offline and dump the schema
cd research/oracle && node oracle.mjs schema

# 2. probe a method, printing the frames it would send
node oracle.mjs call applyDpiStage '{"stage":0,"value":1234}'

# 3. run a sweep and diff frames (see captures/*.json)
node oracle.mjs sweep ../captures/decode-sweep.json

# 4. drive the real device (feature reads are safe; --allow-write sends)
node oracle.mjs call applyLighting '{"channel":"main","modeId":3}' --live --allow-write
```

## Still open

* Macros and button remapping use feature report `0xBD`/`0xBE`/`0xBF`
  (135 bytes, wired only). Not decoded yet.
* Sleep timer: not settable via the frames the driver emits.
* Dongle vs wired: config paths differ slightly (report set).
* Firmware update: out of scope (bricking risk).
