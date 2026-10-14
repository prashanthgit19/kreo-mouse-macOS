#!/usr/bin/env python3
"""Probe which Hawk Pro HID interfaces can be opened, and whether feature reports work."""
import hid

VID = 0x04F3
PIDS = (0x026E, 0x026F)


def try_open(dev_info: dict) -> None:
    up, us = dev_info["usage_page"], dev_info["usage"]
    path = dev_info["path"]
    tag = f"iface={dev_info['interface_number']} usage=0x{up:04X}:0x{us:04X}"
    try:
        dev = hid.device()
        dev.open_path(path)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {tag}  ({e!r})")
        return
    print(f"  OK    {tag}")
    try:
        # feature round-trip: try a 17-byte read on a couple of report ids
        for rid in (0, 1):
            try:
                data = bytes(dev.get_feature_report(rid, 17))
                print(f"          feature id={rid} -> {len(data)}B {data.hex()}")
            except Exception as e:  # noqa: BLE001
                print(f"          feature id={rid} -> err {e!r}")
        # also try vid/pid open style used by macOS backend
        dev.write(bytes([0, 0x0A]))
    except Exception as e:  # noqa: BLE001
        print(f"          write err {e!r}")
    finally:
        dev.close()


def main() -> None:
    devs = [d for d in hid.enumerate(VID) if d["product_id"] in PIDS]
    print(f"{len(devs)} interfaces")
    for d in devs:
        try_open(d)

    print("\ntry open by vid/pid (opens first match):")
    try:
        dev = hid.device()
        dev.open(VID, 0x026E)
        print("  OK by vid/pid")
        dev.close()
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL by vid/pid: {e!r}")


if __name__ == "__main__":
    main()
