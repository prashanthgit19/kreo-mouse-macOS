#!/usr/bin/env python3
"""Find any Kreo/Hawk HID device regardless of the connection type.

The Hawk Pro exposes PID 0x026E on USB and 0x026F on the 2.4G dongle. Over
Bluetooth it may appear with a different VID/PID, so this matches on the
product string too.
"""
import hid

NAME_HINTS = ("hawk", "kreo")


def main() -> None:
    found = []
    for d in hid.enumerate():
        product = (d.get("product_string") or "")
        mfr = (d.get("manufacturer_string") or "")
        blob = f"{product} {mfr}".lower()
        if d["vendor_id"] == 0x04F3 or any(h in blob for h in NAME_HINTS):
            found.append(d)

    if not found:
        print("no Kreo/Hawk HID device found")
        return

    print(f"{len(found)} interface(s):\n")
    for d in found:
        path = d["path"].decode() if isinstance(d["path"], bytes) else d["path"]
        print(f"  VID 0x{d['vendor_id']:04X}  PID 0x{d['product_id']:04X}  "
              f"iface={d['interface_number']}  usage 0x{d['usage_page']:04X}:0x{d['usage']:04X}")
        print(f"    product={d.get('product_string')!r} mfr={d.get('manufacturer_string')!r}")
        print(f"    serial={d.get('serial_number')!r} path={path}")
        print()


if __name__ == "__main__":
    main()
