# kreo-hawk-pro

Configure the **Kreo Hawk Pro** gaming mouse on macOS from the command line.

Kreo's Kontrol web app is effectively Windows-only: its config interface exposes
a keyboard collection, and Chromium's WebHID refuses to open such devices on
macOS. This tool talks to the mouse's vendor HID interface directly through
IOKit instead, so DPI, lighting, polling rate and sensor settings are all
configurable on a Mac — no browser, no VM, no root.

## Status

Working and verified on real hardware:

- DPI values and active-stage switching (up to 6 stages, 50–26000 DPI)
- Lighting mode, brightness and speed
- Report rate (125 / 250 / 500 / 1000 Hz)
- Lift-off distance and sensor toggles (motion sync, angle snap, ripple)

Not yet implemented: macros, button remapping, sleep timer. See
[`research/notes/PROTOCOL.md`](research/notes/PROTOCOL.md) for the protocol and
what remains.

## Install

Needs macOS, Python 3 and the Xcode command line tools (for `clang`).

```bash
clang -O2 -framework IOKit -framework CoreFoundation research/hidtool.c -o research/hidtool
python3 hawkctl.py selftest      # verify frame encoding (no device needed)
```

Close the official software / any browser tab holding the mouse before using it.

## Usage

```bash
python3 hawkctl.py info                             # device id + current model
python3 hawkctl.py dpi                              # show DPI stages
python3 hawkctl.py dpi set 0 1600                   # set stage 0 to 1600 DPI
python3 hawkctl.py dpi stage 0                      # make stage 0 active
python3 hawkctl.py lighting --mode 3 --brightness 4 # Steady mode
python3 hawkctl.py rate 1000                        # polling rate
python3 hawkctl.py sensor --lod 2 --motion-sync on   # sensor settings
python3 hawkctl.py defaults                         # restore defaults
python3 hawkctl.py --dry-run lighting --mode 1      # print frames, send nothing
```

Lighting modes: `0` Off, `1` Rainbow Flow, `2` Breathe, `3` Steady, `4` Neon,
`5` Marquee, `6` Rainbow Steady, `7` Wave. The Hawk Pro has no per-mode colour
control, so `--color` is accepted but ignored by the device.

## Notes

The device stores its settings but does not expose them for reading, so
`hawkctl` keeps a local model in `~/.config/hawkctl/state.json` (override with
`HAWKCTL_STATE`). If it drifts from the mouse, run `defaults` or set the values
again.

Wired (`PID 0x026E`) and 2.4G dongle (`PID 0x026F`) are both supported.

## Layout

```
hawkctl.py                     the CLI
research/hidtool.c             native IOKit transport (get/set/watch/descriptor/server)
research/oracle/               offline harness that runs Kreo's WASM driver as a protocol oracle
research/notes/PROTOCOL.md     the reverse-engineered protocol
research/probe_*.py, *.c       reconnaissance scripts used along the way
```

## Credits & licence

Protocol discovered by running Kreo's own WebAssembly driver as an oracle; the
driver itself is Kreo's and is **not** redistributed here (see
`.gitignore`). Prior art that informed the approach:
[chimera-mapper](https://github.com/SKetU-l/chimera-mapper),
[kreo-backlight](https://github.com/yashsinghcodes/kreo-backlight),
[kreo-macos](https://github.com/shreyasvinaya/kreo-macos).

MIT for this project's own code. Not affiliated with Kreo.
