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
    print(device)

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
    if device.name == "mario":
        new_ld += f"""
__RAM_ORIGIN__ = 0x30010000;
__RAM_LENGTH__ = {64 * (1 << 10) - 8192};
"""
    elif device.name == "zelda":
        new_ld += """
__RAM_ORIGIN__ = 0x240ec524;
__RAM_LENGTH__ = 68308;
"""
    else:
        raise ValueError(f"Unsupported device {device.name}")
    if new_ld != old_ld:
        ld_path.parent.mkdir(exist_ok=True)
        ld_path.write_text(new_ld)
