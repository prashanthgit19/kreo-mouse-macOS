#!/usr/bin/env python3
"""Enumerate Kreo Hawk Pro HID interfaces (Phase 0 recon)."""
import hid

VID = 0x04F3
PIDS = {0x026E: "wired", 0x026F: "2.4G dongle", 0x026D: "bluetooth"}


def main() -> None:
    devs = [d for d in hid.enumerate(VID) if d["product_id"] in PIDS]
    if not devs:
        print(f"no Kreo Hawk Pro found (VID 0x{VID:04X})")
        # show anything that looks close to help debugging
        for d in hid.enumerate():
            if d["vendor_id"] in (VID,):
                print("  other:", hex(d["vendor_id"]), hex(d["product_id"]),
                      d.get("product_string"))
        return

    print(f"found {len(devs)} interface(s) for VID 0x{VID:04X}\n")
    for d in devs:
        mode = PIDS.get(d["product_id"], "?")
        print(f"[{mode}] PID 0x{d['product_id']:04X}  iface={d['interface_number']}  "
              f"usage_page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X}")
        print(f"    product={d.get('product_string')!r} mfr={d.get('manufacturer_string')!r}")
        print(f"    serial={d.get('serial_number')!r} release={d.get('release_number')}")
        print(f"    path={d['path'].decode() if isinstance(d['path'], bytes) else d['path']}")
        print()

    # Try opening the vendor/config interface (usage page 0x0A per Kontrol registry)
    target = next((d for d in devs if d["usage_page"] == 0x0A), None)
    if target:
        print("opening vendor interface (usage_page 0x0A, usage 0x00)...")
        try:
            dev = hid.device()
            dev.open_path(target["path"])
            print(f"  opened OK, nonblocking probe:")
            # probe feature reads of a few report ids / lengths to map the channel
            for rid in (0, 1, 2, 3):
                for length in (17, 32, 64):
                    try:
                        data = dev.get_feature_report(rid, length)
                        print(f"    feature id={rid} len={length} -> {len(data)}B: {bytes(data).hex()}")
                    except Exception as e:  # noqa: BLE001
                        print(f"    feature id={rid} len={length} -> FAILED: {e}")
            dev.close()
        except Exception as e:  # noqa: BLE001
            print(f"  open FAILED: {e}")
    else:
        print("no usage_page 0x0A interface found — inspect the list above")


if __name__ == "__main__":
    main()
