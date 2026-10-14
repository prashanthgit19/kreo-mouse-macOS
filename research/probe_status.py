#!/usr/bin/env python3
"""Send frames and capture the device's status input report (programmatic probe)."""
import subprocess, sys, os, re

HIDTOOL = os.path.join(os.path.dirname(__file__), "hidtool")
FEATURE_ID = 6

def chk(f):
    return (FEATURE_ID + sum(b for i, b in enumerate(f) if i != 3)) & 0xFF

def frame(cmd, seq, flag, payload):
    f = [cmd, seq, flag, 0] + list(payload)
    f = f + [0] * (31 - len(f))
    f[3] = chk(f)
    return bytes(f)

def sendwatch(fr, seconds=2):
    out = subprocess.run([HIDTOOL, "sendwatch", "6", fr.hex(" "), str(seconds)],
                         capture_output=True, text=True).stdout
    m = re.findall(r"IN id=\d+ len=\d+: ([0-9A-Fa-f ]+)", out)
    return m

seq = 0x40

def bump():
    global seq
    seq = (seq + 1) & 0xFF
    return seq

def lighting(channel_sel, mode_id, brightness=0x40, speed=0x43, direction=0x00, color=(0x40, 0x9E, 0xFF)):
    # from driver: [FF, modeId, 0x43, 0x00, R, G, B]  (channel not yet fully understood)
    payload = [0xFF, mode_id, speed, direction, color[0], color[1], color[2]] + [0xFF] * 24
    return frame(0x06, bump(), 0x01, payload)

print("== lighting mode sweep: capture status report per mode ==")
for mode in [0, 1, 2, 3, 4, 5, 6, 7]:
    fr = lighting(0xFF, mode)
    reps = sendwatch(fr, 2)
    print(f"modeId={mode}: {reps[0] if reps else '(no report)'}")

print("\n== DPI all-stages sweep: does status report change? ==")
for dpi in [400, 26000, 800]:
    code = (dpi - 50) // 50
    payload = [0, 0x3F] + [b for _ in range(8) for b in (code & 0xFF, (code >> 8) & 0xFF)] + [0xFF] * 9
    fr1 = frame(0x02, bump(), 0x02, payload)
    fr2 = frame(0x02, bump(), 0x12, payload)
    r1 = sendwatch(fr1, 2)
    r2 = sendwatch(fr2, 2)
    print(f"dpi={dpi}: {r1[0] if r1 else '(none)'} | {r2[0] if r2 else '(none)'}")
