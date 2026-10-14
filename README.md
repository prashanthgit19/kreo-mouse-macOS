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
- Button remapping (mouse, DPI, media, shortcuts, lighting toggle, disable)
- On-board macros (up to 8 slots, keys with per-action delays)

Not implemented: sleep timer, custom arbitrary key combos. See
[`research/notes/PROTOCOL.md`](research/notes/PROTOCOL.md) for the protocol.

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
python3 hawkctl.py bind                              # show button bindings
python3 hawkctl.py bind BUTTON_6 media.volume_up     # remap a button
python3 hawkctl.py bind BUTTON_6 none                # disable a button
python3 hawkctl.py macro set --slot 0 --name hi \
        --actions keyboard.h:press:0,keyboard.h:release:20,keyboard.i:press:40,keyboard.i:release:60
python3 hawkctl.py bind BUTTON_7 macro:0 --mode repeat --cycles 1
python3 hawkctl.py defaults                         # restore defaults
python3 hawkctl.py --dry-run lighting --mode 1      # print frames, send nothing
```

Bindable actions: `mouse.left/right/middle/back/forward`, `mouse.dpi_up/down/cycle`,
`dpi.lock_200`…`dpi.lock_1200`, `media.*`, `shortcut.*` (copy, paste, show_desktop, …),
`lighting.toggle`, `none`, `macro:<slot>` (with `--mode repeat|button|toggle`), or any
key combination with `combo:<mods>+<key>` (e.g. `combo:ctrl+arrow_right`) and
macOS-friendly aliases with `mac:<name>` (copy, paste, show-desktop, spotlight, …).

Example — side buttons to switch macOS Spaces:

```bash
python3 hawkctl.py bind BUTTON_4 combo:ctrl+arrow_right   # forward
python3 hawkctl.py bind BUTTON_5 combo:ctrl+arrow_left    # back
```

`lighting.toggle` binds a button to step through the lighting modes:

```bash
python3 hawkctl.py bind BUTTON_8 lighting.toggle          # cycle lighting modes
```

> If a binding change doesn't take effect, power-cycle the mouse (off/on, or
> replug the dongle). The device sometimes stops accepting binding writes while
> still reporting success; a power cycle clears it.

Lighting modes: `0` Off, `1` Rainbow Flow, `2` Breathe, `3` Steady, `4` Neon,
`5` Marquee, `6` Rainbow Steady, `7` Wave. The Hawk Pro has no per-mode colour
control, so `--color` is accepted but ignored by the device.

## Notes

The device stores its settings but does not expose them for reading, so
`hawkctl` keeps a local model in `~/.config/hawkctl/state.json` (override with
`HAWKCTL_STATE`). If it drifts from the mouse, run `defaults` or set the values
again.

Three connection modes: wired (`0x026E`), 2.4G dongle (`0x026F`) and Bluetooth
(`0x026D`). Settings are stored on the mouse, so they survive power-off and
apply on every mode.

**Configure over the cable or the dongle.** On Bluetooth, macOS rejects
feature-report *writes* (`kIOReturnError`) — reads work, but you can't change
settings there. Configure once over USB/dongle and the mouse will use it over
Bluetooth too.

Wired (`PID 0x026E`) and 2.4G dongle (`PID 0x026F`) are both writable.

## Layout

```
hawkctl.py                     the CLI
research/hidtool.c             native IOKit transport (get/set/watch/descriptor/server)
research/oracle/               offline harness that runs Kreo's WASM driver as a protocol oracle
research/notes/PROTOCOL.md     the reverse-engineered protocol
research/find_kreo.py          locate the mouse on any connection (USB / dongle / Bluetooth)
research/probe_*.py, *.c       reconnaissance scripts used along the way
```

## Legal

Unofficial — not affiliated with, endorsed by, or connected to Kreo. It exists
because the official Kontrol software is Windows-only and cannot open this mouse
on macOS.

The protocol was reverse-engineered for interoperability by running Kreo's own
WebAssembly driver locally as an oracle. **Kreo's driver is not redistributed** —
`research/oracle/vendor/` is gitignored and anyone reproducing the work fetches
it themselves (see [`research/notes/PROTOCOL.md`](research/notes/PROTOCOL.md)).
No firmware is modified; firmware updating is deliberately not implemented.

## Credits

Prior art that informed the approach:
[chimera-mapper](https://github.com/SKetU-l/chimera-mapper),
[kreo-backlight](https://github.com/yashsinghcodes/kreo-backlight),
[kreo-macos](https://github.com/shreyasvinaya/kreo-macos).

## License

MIT — see [LICENSE](LICENSE).
