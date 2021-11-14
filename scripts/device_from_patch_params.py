""" Dictates device used in Makefile and C parts of the code
"""
import argparse
from pathlib import Path

from patches import Device

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="mario")
    args, _ = parser.parse_known_args()
    device = args.device.upper()
    print(f"-DGNW_DEVICE_{device}=1")

    # Generate the device-specific LD file
    ld_path = Path("build/device.ld")

    try:
        old_ld = ld_path.read_text()
    except FileNotFoundError:
        old_ld = ""

    device = Device.registry[args.device]

    new_ld = f"""
__STOCK_ROM_END__ = 0x{device.Int.STOCK_ROM_END:08X};
"""
    if new_ld != old_ld:
        ld_path.parent.mkdir(exist_ok=True)
        ld_path.write_text(new_ld)
