#!/usr/bin/env python3
"""
hawkctl — configure the Kreo Hawk Pro gaming mouse on macOS.

Talks to the mouse's vendor HID config interface directly via IOKit
(through the small `research/hidtool` helper), bypassing Chrome's WebHID
restrictions that make Kreo's Kontrol web app unusable on macOS.

Protocol (reverse-engineered from Kreo's own WASM driver; see
research/notes/PROTOCOL.md):

  Feature report 6, 31-byte frames:
    [0] command   [1] seq   [2] frame flag   [3] checksum   [4..30] payload
    checksum = (reportId + sum(all other bytes)) & 0xFF

  commands: 02 DPI stages   03 DPI stage colors   04 report rate
            05 lift-off     06 lighting           07 sleep       08 sensor

Note: the report ID must be the first byte of the IOKit write buffer; that is
handled by `hidtool`. The device persists settings but does not expose them for
read-back, so this tool keeps a local model of the device state.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HIDTOOL = HERE / "research" / "hidtool"
REPORT_ID = 0x06
FRAME_LEN = 31
STATE_PATH = Path(os.environ.get("HAWKCTL_STATE", Path.home() / ".config" / "hawkctl" / "state.json"))

LIGHT_MODES = {
    0: "Off", 1: "Rainbow Flow", 2: "Breathe", 3: "Steady", 4: "Neon",
    5: "Marquee", 6: "Rainbow Steady", 7: "Wave",
}
# sleep index is in 10-second units; these are the presets shown by the app
SLEEP_PRESETS = {6: "1 min", 12: "2 mins", 18: "3 mins", 30: "5 mins", 60: "10 mins", 90: "15 mins"}

# Action tables generated from the vendor driver (see research/oracle + notes/).
NOTES_DIR = HERE / "research" / "notes"


def _load_table(name: str) -> dict:
    path = NOTES_DIR / name
    return json.loads(path.read_text()) if path.exists() else {}


BINDING_TABLE = _load_table("bindings.json")      # optionId -> "type lo hi"
MACRO_KEY_TABLE = _load_table("macro-keys.json")  # codeId  -> HID usage hex

# key name -> HID keyboard usage, derived from the macro key table
KEY_USAGE = {k[len("keyboard."):]: int(v, 16)
             for k, v in MACRO_KEY_TABLE.items() if k.startswith("keyboard.")}
# friendly aliases: digit1->1, numpad1->num1
for _d in "1234567890":
    if f"digit{_d}" in KEY_USAGE:
        KEY_USAGE.setdefault(_d, KEY_USAGE[f"digit{_d}"])
    if f"numpad{_d}" in KEY_USAGE:
        KEY_USAGE.setdefault(f"num{_d}", KEY_USAGE[f"numpad{_d}"])
MOD_BITS = {"ctrl": 0x01, "control": 0x01, "shift": 0x02, "alt": 0x04, "opt": 0x04,
            "option": 0x04, "cmd": 0x08, "command": 0x08, "meta": 0x08, "gui": 0x08, "win": 0x08}

# Kreo's shortcut.* presets are Windows combos (Ctrl+C, Win+D ...). These are the
# macOS equivalents, expressed as custom combinations (combo.any).
MAC_SHORTCUTS = {
    "copy": "cmd+c", "paste": "cmd+v", "cut": "cmd+x", "undo": "cmd+z", "redo": "cmd+shift+z",
    "select-all": "cmd+a", "save": "cmd+s", "save-as": "cmd+shift+s", "open": "cmd+o",
    "print": "cmd+p", "new": "cmd+n", "close": "cmd+w", "quit": "cmd+q", "find": "cmd+f",
    "preferences": "cmd+comma", "show-desktop": "f11", "spotlight": "cmd+space",
    "mission-control": "ctrl+arrowup", "app-windows": "ctrl+arrowdown",
}

# The driver writes BUTTON_4 into slot 4 and BUTTON_5 into slot 3.
CONTROL_SLOT = {"BUTTON_1": 0, "BUTTON_2": 1, "BUTTON_3": 2, "BUTTON_5": 3,
                "BUTTON_4": 4, "BUTTON_6": 5, "BUTTON_7": 6, "BUTTON_8": 7}
SLOT_CONTROL = {v: k for k, v in CONTROL_SLOT.items()}
MACRO_TYPE_BASE = {"FIXED_COUNT": 0x20, "UNTIL_RELEASED": 0x10, "UNTIL_ANY_PRESSED": 0x40}
DEFAULT_BINDINGS = {
    "BUTTON_1": "mouse.left", "BUTTON_2": "mouse.right", "BUTTON_3": "mouse.middle",
    "BUTTON_4": "mouse.forward", "BUTTON_5": "mouse.back", "BUTTON_6": "mouse.dpi_up",
    "BUTTON_7": "mouse.dpi_down", "BUTTON_8": "shortcut.show_desktop",
}

DEFAULT_STATE = {
    "dpi": {
        "currentStage": 2,
        "activeStages": 6,
        "stages": [
            {"value": 400,  "color": [234, 94, 86]},
            {"value": 800,  "color": [0, 102, 255]},
            {"value": 1600, "color": [50, 170, 112]},
            {"value": 2400, "color": [245, 156, 37]},
            {"value": 3200, "color": [0, 221, 228]},
            {"value": 6400, "color": [181, 0, 236]},
            {"value": 12000, "color": [255, 107, 107]},
            {"value": 26000, "color": [78, 205, 196]},
        ],
    },
    "lighting": {"modeId": 1, "brightness": 4, "speed": 3, "direction": 0, "color": [64, 158, 255]},
    "sensor": {"motionSync": True, "angleSnap": False, "ripple": False, "lodIndex": 2},
    "reportRateHz": 1000,
    "sleepIndex": 6,
    "seq": 2,
    "bindings": DEFAULT_BINDINGS,
    "macros": [
        {"name": "", "executionType": "FIXED_COUNT", "cycleCount": 1, "actions": []}
        for _ in range(8)
    ],
}

# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------

def checksum(frame: list[int]) -> int:
    return (REPORT_ID + sum(b for i, b in enumerate(frame) if i != 3)) & 0xFF


def frame(cmd: int, seq: int, flag: int, payload: list[int], length: int = FRAME_LEN) -> bytes:
    f = [cmd & 0xFF, seq & 0xFF, flag & 0xFF, 0] + [b & 0xFF for b in payload]
    f = (f + [0x00] * length)[:length]
    f[3] = checksum(f)
    return bytes(f)


def dpi_code(dpi: int) -> int:
    return max(0, min(0xFFFF, (dpi - 50) // 50))


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            state = {}
        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged.update(state)
        return merged
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def next_seq(state: dict) -> int:
    seq = state.get("seq", 2) & 0xFF
    state["seq"] = (seq + 1) & 0xFF
    return seq


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def send(frames: list[bytes], dry: bool = False) -> None:
    for fr in frames:
        if dry:
            print(f"  DRY  {fr.hex(' ')}")
            continue
        last = ""
        for attempt in range(5):  # the vendor app retries feature writes too
            if attempt:
                time.sleep(0.15 * attempt)
            proc = subprocess.run([str(HIDTOOL), "set", str(REPORT_ID), fr.hex(" ")],
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                break
            last = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        else:
            sys.exit(f"write failed after 5 attempts ({last}).\n"
                     f"Is the mouse connected, and is the Kreo software / a browser tab closed?")
        print(f"  sent {fr.hex(' ')}")
        # The vendor driver always reads report 6 back after a write; mirror that.
        subprocess.run([str(HIDTOOL), "get", str(REPORT_ID), "64"],
                       capture_output=True, text=True)


def read_report(rid: int, length: int = 64) -> bytes:
    proc = subprocess.run([str(HIDTOOL), "get", str(rid), str(length)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"hidtool get failed: {(proc.stderr or proc.stdout).strip()}")
    return bytes.fromhex(proc.stdout.strip().replace(" ", ""))


# ---------------------------------------------------------------------------
# command builders
# ---------------------------------------------------------------------------

def frames_dpi_settings(state: dict) -> list[bytes]:
    dpi = state["dpi"]
    n = max(1, min(8, dpi.get("activeStages", 6)))
    mask = (1 << n) - 1
    codes = []
    for s in dpi["stages"][:8]:
        c = dpi_code(s["value"])
        codes += [c & 0xFF, (c >> 8) & 0xFF]
    payload = [dpi.get("currentStage", 0) & 0xFF, mask] + codes + [0xFF] * 9
    return [
        frame(0x02, next_seq(state), 0x02, payload),
        frame(0x02, next_seq(state), 0x12, payload),
    ]


def frame_dpi_colors(state: dict) -> bytes:
    payload = []
    for s in state["dpi"]["stages"][:8]:
        r, g, b = s["color"]
        payload += [r & 0xFF, g & 0xFF, b & 0xFF]
    payload += [0xFF] * 3
    return frame(0x03, next_seq(state), 0x01, payload)


def frame_lighting(state: dict) -> bytes:
    lt = state["lighting"]
    brightness = max(1, min(15, lt.get("brightness", 4)))
    speed = max(1, min(15, lt.get("speed", 3)))
    r, g, b = lt.get("color", [255, 255, 255])
    payload = [0xFF, lt.get("modeId", 1) & 0xFF, (brightness << 4) | speed,
               lt.get("direction", 0) & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF] + [0xFF] * 24
    return frame(0x06, next_seq(state), 0x01, payload)


def frame_report_rate(state: dict) -> bytes:
    hz = state.get("reportRateHz", 1000)
    if hz not in (125, 250, 500, 1000):
        sys.exit("report rate must be one of 125, 250, 500, 1000")
    payload = [1000 // hz] + [0x00] * 30
    return frame(0x04, next_seq(state), 0x01, payload)


def frame_lod(state: dict) -> bytes:
    payload = [state["sensor"].get("lodIndex", 2) & 0xFF] + [0xFF] * 30
    return frame(0x05, next_seq(state), 0x01, payload)


def frame_sensor(state: dict) -> bytes:
    s = state["sensor"]
    payload = [1 if s.get("angleSnap") else 0,
               1 if s.get("motionSync", True) else 0,
               1 if s.get("ripple") else 0] + [0xFF] * 28
    return frame(0x08, next_seq(state), 0x01, payload)


def encode_combo(spec: str) -> list[int]:
    """Encode a key combination as a binding: 'cmd+shift+v', 'f11', ..."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        sys.exit("empty key combination")
    mask = 0
    for m in parts[:-1]:
        if m not in MOD_BITS:
            sys.exit(f"unknown modifier '{m}' (use ctrl / shift / alt / cmd)")
        mask |= MOD_BITS[m]
    key = parts[-1]
    usage = KEY_USAGE.get(key)
    if usage is None:
        sys.exit(f"unknown key '{key}' for a combination "
                 f"(see research/notes/macro-keys.json)")
    return [0x70, mask & 0xFF, usage & 0xFF]


def encode_binding(option_id: str, params: dict | None = None) -> list[int]:
    """Encode one button binding as 3 bytes: [type][value lo][value hi]."""
    params = params or {}
    low = option_id.lower()
    if low.startswith("combo:"):
        return encode_combo(option_id[6:])
    if low.startswith("mac:"):
        name = low[4:]
        if name not in MAC_SHORTCUTS:
            sys.exit(f"unknown macOS shortcut '{name}' "
                     f"(available: {', '.join(sorted(MAC_SHORTCUTS))})")
        return encode_combo(MAC_SHORTCUTS[name])
    if option_id == "macro.slot" or params.get("macroSlot") is not None:
        if not MACRO_TYPE_BASE:
            sys.exit("macros not supported: missing option tables")
        slot = int(params.get("macroSlot", 0)) & 0x07
        et = params.get("macroExecutionType", "FIXED_COUNT")
        base = MACRO_TYPE_BASE.get(et, 0x20)
        cycle = int(params.get("macroCycleCount", 1) or 1) if et == "FIXED_COUNT" else 0
        return [0x90, base | (slot + 1), cycle & 0xFF]
    entry = BINDING_TABLE.get(option_id)
    if not entry:
        sys.exit(f"unknown binding '{option_id}' (see research/notes/bindings.json)")
    return [int(b, 16) for b in entry.split()]


def frame_control_bindings(state: dict) -> bytes:
    payload: list[int] = []
    for slot in range(8):
        spec = state["bindings"].get(SLOT_CONTROL[slot], "key.disabled")
        if isinstance(spec, dict):
            payload += encode_binding(spec.get("optionId", "key.disabled"), spec.get("parameters"))
        else:
            payload += encode_binding(spec)
    payload += [0xFF] * 3
    return frame(0x01, next_seq(state), 0x01, payload)


def encode_macro_action(action: dict) -> list[int]:
    """Encode one macro action as 3 bytes: [0x30|delay>>8|release][delay lo][usage]."""
    code = MACRO_KEY_TABLE.get(action["codeId"])
    if code is None:
        sys.exit(f"unknown macro key '{action['codeId']}' (see research/notes/macro-keys.json)")
    delay = max(0, min(0xFFF, int(action.get("delayMs", 0))))
    base = 0xB0 if action.get("event") == "release" else 0x30
    return [base | ((delay >> 8) & 0x0F), delay & 0xFF, int(code, 16)]


def frames_macro_slot(state: dict, slot: int) -> list[bytes]:
    actions = (state["macros"][slot].get("actions") or [])
    if not actions:
        sys.exit("macro has no actions")
    body: list[int] = []
    for a in actions[:80]:
        body += encode_macro_action(a)
    frames = [frame(0x11, next_seq(state), 0x01, [0xFF] + [0x00] * 26)]  # prepare
    for i in range(0, len(body), 25):                                 # 25 action bytes/frame
        part = body[i:i + 25]
        payload = [(slot + 1) & 0xFF] + part
        frames.append(frame(0x10, next_seq(state), 0x01, [len(payload)] + payload))
    return frames


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_info(state: dict, args) -> None:
    r5 = read_report(5, 64)
    ascii_id = "".join(chr(c) if 32 <= c < 127 else "." for c in r5[1:5])
    print(f"device : Kreo Hawk Pro (feature id5 = {r5.hex(' ')})")
    print(f"id     : {ascii_id!r}  (looks like firmware/device id)")
    print(f"state  : {json.dumps(state, separators=(',', ':'))}")


def cmd_dpi(state: dict, args) -> None:
    st = state["dpi"]
    if not args.action or args.action == "show":
        print(f"current stage : {st['currentStage']} (active stages: {st['activeStages']})")
        for i, s in enumerate(st["stages"]):
            mark = "*" if i == st["currentStage"] else " "
            r, g, b = s["color"]
            print(f"  {mark} {i}: {s['value']:>6} DPI   #{r:02X}{g:02X}{b:02X}")
        return
    if args.action == "set":
        if args.stage is None or args.dpi is None:
            sys.exit("usage: hawkctl dpi set <stage> <dpi>")
        st["stages"][args.stage]["value"] = int(args.dpi)
    elif args.action == "stage":
        if args.stage is None:
            sys.exit("usage: hawkctl dpi stage <n>")
        st["currentStage"] = int(args.stage)
    elif args.action == "color":
        if args.stage is None or args.color is None:
            sys.exit("usage: hawkctl dpi color <stage> <RRGGBB>")
        c = args.color.lstrip("#")
        st["stages"][args.stage]["color"] = [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)]
    elif args.action == "active":
        if args.stage is None:
            sys.exit("usage: hawkctl dpi active <n>")
        st["activeStages"] = int(args.stage)
    elif args.action == "apply":
        pass
    else:
        sys.exit(f"unknown dpi action: {args.action}")
    if not args.dry_run:
        save_state(state)
    frames = frames_dpi_settings(state)
    if args.action == "color":
        frames = [frame_dpi_colors(state)]
    send(frames, args.dry_run)
    print("done (state saved)")


def cmd_lighting(state: dict, args) -> None:
    lt = state["lighting"]
    if args.mode is not None:
        lt["modeId"] = args.mode
    if args.brightness is not None:
        lt["brightness"] = args.brightness
    if args.speed is not None:
        lt["speed"] = args.speed
    if args.color is not None:
        c = args.color.lstrip("#")
        lt["color"] = [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)]
    if not args.dry_run:
        save_state(state)
    send([frame_lighting(state)], args.dry_run)
    print(f"lighting: mode={LIGHT_MODES.get(lt['modeId'], lt['modeId'])} "
          f"brightness={lt['brightness']} speed={lt['speed']} color={lt['color']}")


def cmd_rate(state: dict, args) -> None:
    if args.hz is None:
        print(f"report rate: {state['reportRateHz']} Hz")
        return
    state["reportRateHz"] = int(args.hz)
    if not args.dry_run:
        save_state(state)
    send([frame_report_rate(state)], args.dry_run)
    print(f"report rate: {state['reportRateHz']} Hz")


def cmd_sensor(state: dict, args) -> None:
    s = state["sensor"]
    changed = False
    if args.lod is not None:
        s["lodIndex"] = int(args.lod); changed = True
    if args.motion_sync is not None:
        s["motionSync"] = args.motion_sync == "on"; changed = True
    if args.angle_snap is not None:
        s["angleSnap"] = args.angle_snap == "on"; changed = True
    if args.ripple is not None:
        s["ripple"] = args.ripple == "on"; changed = True
    if not changed:
        print(f"sensor: motionSync={s['motionSync']} angleSnap={s['angleSnap']} "
              f"ripple={s['ripple']} lod={s['lodIndex']}")
        return
    if not args.dry_run:
        save_state(state)
    send([frame_lod(state), frame_sensor(state)], args.dry_run)
    print(f"sensor: motionSync={s['motionSync']} angleSnap={s['angleSnap']} "
          f"ripple={s['ripple']} lod={s['lodIndex']}")


def cmd_bind(state: dict, args) -> None:
    if not args.button:
        print("button bindings:")
        for c in sorted(CONTROL_SLOT):
            spec = state["bindings"].get(c, "key.disabled")
            if isinstance(spec, dict):
                p = spec.get("parameters", {})
                label = f"macro slot {p.get('macroSlot')} ({p.get('macroExecutionType')}, x{p.get('macroCycleCount')})"
            else:
                label = spec
            print(f"  {c:<9} {label}")
        return
    control = args.button.upper()
    if control not in CONTROL_SLOT:
        sys.exit("button must be BUTTON_1 .. BUTTON_8")
    if not args.action:
        sys.exit("usage: hawkctl bind <BUTTON_n> <action>   (action = optionId | none | macro:<slot>)")
    action = args.action
    if action in ("none", "disabled", "key.disabled"):
        state["bindings"][control] = "key.disabled"
    elif action.startswith("macro"):
        if ":" in action:
            slot = int(action.split(":", 1)[1])
        else:
            slot = args.slot if args.slot is not None else 0
        mode = {"repeat": "FIXED_COUNT", "button": "UNTIL_RELEASED",
                "toggle": "UNTIL_ANY_PRESSED"}.get(args.mode or "repeat", "FIXED_COUNT")
        state["bindings"][control] = {"optionId": "macro.slot", "parameters": {
            "macroSlot": slot, "macroExecutionType": mode,
            "macroCycleCount": args.cycles if args.cycles is not None else 1}}
    else:
        state["bindings"][control] = action
    if not args.dry_run:
        save_state(state)
    send([frame_control_bindings(state)], args.dry_run)
    print(f"{control} -> {state['bindings'][control]}")


def cmd_macro(state: dict, args) -> None:
    macros = state["macros"]
    if not args.action:
        print("macro slots (local + device):")
        for i, m in enumerate(macros):
            print(f"  {i}: {(m.get('name') or '(unnamed)'):<16} "
                  f"{len(m.get('actions') or []):>2} action(s)  {m.get('executionType')}")
        return
    slot = args.slot if args.slot is not None else 0
    if not 0 <= slot < 8:
        sys.exit("macro slot must be 0..7")
    if args.action == "clear":
        macros[slot] = {"name": "", "executionType": "FIXED_COUNT", "cycleCount": 1, "actions": []}
        print(f"slot {slot} cleared locally (bind a button away from it to forget it on the mouse)")
        return
    if args.action == "set":
        acts = []
        for item in [x for x in (args.actions or "").split(",") if x.strip()]:
            parts = item.strip().split(":")
            if len(parts) != 3:
                sys.exit("each action must be key:event:delayMs, e.g. keyboard.a:press:0")
            acts.append({"codeId": parts[0], "event": parts[1], "delayMs": int(parts[2])})
        if not acts:
            sys.exit("no actions given; use --actions keyboard.a:press:0,keyboard.a:release:50")
        macros[slot] = {"name": args.name or "", "executionType": args.exec or "FIXED_COUNT",
                        "cycleCount": args.cycles or 1, "actions": acts}
        if not args.dry_run:
            save_state(state)
        send(frames_macro_slot(state, slot), args.dry_run)
        print(f"slot {slot}: {len(acts)} action(s) written. Bind a button with: "
              f"hawkctl bind BUTTON_6 macro:{slot}")
        return
    sys.exit("usage: hawkctl macro [set|clear] --slot N [--name ...] [--actions k:event:ms,...]")


def cmd_defaults(state: dict, args) -> None:
    """Restore the factory-default DPI stages, lighting and sensor settings."""
    fresh = json.loads(json.dumps(DEFAULT_STATE))
    fresh["seq"] = state.get("seq", 2)
    frames = frames_dpi_settings(fresh)
    frames.append(frame_lighting(fresh))
    frames.append(frame_report_rate(fresh))
    frames.append(frame_lod(fresh))
    frames.append(frame_sensor(fresh))
    frames.append(frame_control_bindings(fresh))
    if not args.dry_run:
        save_state(fresh)
    send(frames, args.dry_run)
    print("restored defaults (DPI 400-6400, lighting Rainbow Flow, 1000 Hz, LOD 2)")


def cmd_raw(state: dict, args) -> None:
    data = bytes.fromhex(args.hex.replace(" ", ""))
    if len(data) != FRAME_LEN:
        sys.exit(f"expected {FRAME_LEN} bytes, got {len(data)}")
    send([data], args.dry_run)


def cmd_selftest(state: dict, args) -> None:
    """Reproduce golden frames captured from Kreo's own driver."""
    golden = [
        # (cmd, seq, flag, payload, expected hex)
        (0x02, 0x02, 0x02, [2, 0x3F, 7, 0, 15, 0, 31, 0, 47, 0, 63, 0, 127, 0, 239, 0, 7, 2] + [0xFF] * 9,
         "02 02 02 5E 02 3F 07 00 0F 00 1F 00 2F 00 3F 00 7F 00 EF 00 07 02 FF FF FF FF FF FF FF FF FF"),
        (0x06, 0x07, 0x01, [0xFF, 3, 0x43, 0x00, 0x40, 0x9E] + [0xFF] * 24,
         "06 07 01 22 FF 03 43 00 40 9E FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF"),
        (0x04, 0x17, 0x01, [2] + [0] * 30,
         "04 17 01 24 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (0x05, 0x19, 0x01, [1] + [0xFF] * 30,
         "05 19 01 0C 01 FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF"),
        (0x08, 0x1B, 0x01, [0, 1, 0] + [0xFF] * 28,
         "08 1B 01 13 00 01 00 FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF"),
        (0x01, 0x02, 0x01, [0x10, 0x01, 0x00, 0x10, 0x02, 0x00, 0x10, 0x04, 0x00, 0x10, 0x08, 0x00, 0x10, 0x10,
                            0x00, 0x10, 0x04, 0x00, 0x40, 0x03, 0x00, 0x70, 0x08, 0x07, 0xFF, 0xFF, 0xFF],
         "01 02 01 4C 10 01 00 10 02 00 10 04 00 10 08 00 10 10 00 10 04 00 40 03 00 70 08 07 FF FF FF"),
        (0x10, 0x03, 0x01, [0x07, 0x01, 0x30, 0x00, 0x04, 0xB0, 0x32, 0x04, 0x00] + [0x00] * 18,
         "10 03 01 3C 07 01 30 00 04 B0 32 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (0x11, 0x02, 0x01, [0xFF] + [0x00] * 26,
         "11 02 01 19 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]
    ok = True
    for cmd, seq, flag, payload, expected in golden:
        got = frame(cmd, seq, flag, payload).hex(" ").upper()
        if got != expected:
            ok = False
            print(f"FAIL cmd=0x{cmd:02X}\n  got      {got}\n  expected {expected}")

    checks = [
        ("binding mouse.left", encode_binding("mouse.left"), [0x10, 0x01, 0x00]),
        ("binding shortcut.copy", encode_binding("shortcut.copy"), [0x70, 0x01, 0x06]),
        ("binding macro slot3 x3",
         encode_binding("macro.slot", {"macroSlot": 3, "macroExecutionType": "FIXED_COUNT",
                                       "macroCycleCount": 3}), [0x90, 0x24, 0x03]),
        ("binding macro slot5 toggle",
         encode_binding("macro.slot", {"macroSlot": 5, "macroExecutionType": "UNTIL_ANY_PRESSED"}),
         [0x90, 0x46, 0x00]),
        ("action a press d0", encode_macro_action({"codeId": "keyboard.a", "event": "press", "delayMs": 0}),
         [0x30, 0x00, 0x04]),
        ("action a release d50",
         encode_macro_action({"codeId": "keyboard.a", "event": "release", "delayMs": 50}),
         [0xB0, 0x32, 0x04]),
        ("action f1 press d4095",
         encode_macro_action({"codeId": "keyboard.f1", "event": "press", "delayMs": 4095}),
         [0x3F, 0xFF, 0x3A]),
        ("combo cmd+c", encode_binding("combo:cmd+c"), [0x70, 0x08, 0x06]),
        ("combo cmd+shift+z", encode_binding("combo:cmd+shift+z"), [0x70, 0x0A, 0x1D]),
        ("mac:paste", encode_binding("mac:paste"), [0x70, 0x08, 0x19]),
        ("mac:show-desktop", encode_binding("mac:show-desktop"), [0x70, 0x00, 0x44]),
    ]
    for name, got, want in checks:
        if got != want:
            ok = False
            print(f"FAIL {name}: got {[hex(x) for x in got]} want {[hex(x) for x in want]}")
    print("selftest:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="hawkctl", description="Configure the Kreo Hawk Pro on macOS")
    parser.add_argument("--dry-run", action="store_true", help="print frames instead of sending")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("info", help="read device identity + local state")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("dpi", help="show or change DPI stages")
    p.add_argument("action", nargs="?", choices=["show", "set", "stage", "color", "active", "apply"])
    p.add_argument("stage", nargs="?", type=int, help="stage index (0-7)")
    p.add_argument("dpi", nargs="?", type=int, help="DPI value (for 'set')")
    p.add_argument("color", nargs="?", help="RRGGBB (for 'color')")
    p.set_defaults(func=cmd_dpi)

    p = sub.add_parser("lighting", help="set lighting")
    p.add_argument("--mode", type=int, help="mode id (0=Off 1=Rainbow Flow 2=Breathe 3=Steady 4=Neon 5=Marquee ...)")
    p.add_argument("--brightness", type=int)
    p.add_argument("--speed", type=int)
    p.add_argument("--color", help="RRGGBB")
    p.set_defaults(func=cmd_lighting)

    p = sub.add_parser("rate", help="get/set polling rate")
    p.add_argument("hz", nargs="?", type=int, help="125, 250, 500 or 1000")
    p.set_defaults(func=cmd_rate)

    p = sub.add_parser("sensor", help="sensor settings")
    p.add_argument("--lod", type=int, choices=[1, 2])
    p.add_argument("--motion-sync", choices=["on", "off"])
    p.add_argument("--angle-snap", choices=["on", "off"])
    p.add_argument("--ripple", choices=["on", "off"])
    p.set_defaults(func=cmd_sensor)

    p = sub.add_parser("raw", help="send a raw 31-byte frame to feature report 6")
    p.add_argument("hex")
    p.set_defaults(func=cmd_raw)

    p = sub.add_parser("selftest", help="verify frame encoding against captured golden frames")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("defaults", help="restore default DPI/lighting/sensor/button settings")
    p.set_defaults(func=cmd_defaults)

    p = sub.add_parser("bind", help="show or set button bindings")
    p.add_argument("button", nargs="?", help="BUTTON_1 .. BUTTON_8")
    p.add_argument("action", nargs="?", help="optionId (e.g. mouse.middle), 'none', or macro:<slot>")
    p.add_argument("--slot", type=int, help="macro slot for 'macro'")
    p.add_argument("--mode", choices=["repeat", "button", "toggle"], default="repeat",
                   help="macro execution: repeat / button (hold) / toggle")
    p.add_argument("--cycles", type=int, help="repeat count for --mode repeat")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("macro", help="write a macro to an on-board slot")
    p.add_argument("action", nargs="?", choices=["set", "clear"])
    p.add_argument("--slot", type=int, default=0, help="macro slot 0..7")
    p.add_argument("--name", help="macro name (host-side only)")
    p.add_argument("--exec", choices=["FIXED_COUNT", "UNTIL_RELEASED", "UNTIL_ANY_PRESSED"],
                   help="execution type (usually set on the button binding instead)")
    p.add_argument("--cycles", type=int, help="repeat count")
    p.add_argument("--actions", help="comma list of key:event:delayMs e.g. keyboard.a:press:0,keyboard.a:release:50")
    p.set_defaults(func=cmd_macro)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    if args.command != "selftest" and not HIDTOOL.exists():
        sys.exit(f"hidtool not found at {HIDTOOL} — build it: clang -O2 -framework IOKit -framework CoreFoundation research/hidtool.c -o research/hidtool")
    state = load_state()
    args.func(state, args)


if __name__ == "__main__":
    main()
