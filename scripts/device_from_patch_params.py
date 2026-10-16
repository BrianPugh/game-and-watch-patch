""" Dictates device used in Makefile and C parts of the code
"""
import argparse
from pathlib import Path

from patches import Device
from patches.syms import stock_functions

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="mario", choices=["mario", "zelda"])
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
        # AXI RAM stock never writes, below app_state_timer (tools/re/zelda.syms).
        # Most other AXI RAM that looks free holds the per-game app_state_*
        # buffers, which must survive our runtime LZMA calls (e.g. the timer).
        new_ld += """
__RAM_ORIGIN__ = 0x240d3560;
__RAM_LENGTH__ = 0x240d6000 - 0x240d3560;
"""
    else:
        raise ValueError(f"Unsupported device {device.name}")
    if new_ld != old_ld:
        ld_path.parent.mkdir(exist_ok=True)
        ld_path.write_text(new_ld)

    # Every stock function in tools/re/<device>.syms as an absolute Thumb
    # function symbol stock_<name>, so ld emits plain BLs and each use is a
    # relocation the patcher can follow.
    syms_path = Path("build/stock_symbols.s")
    new_syms = ".syntax unified\n.thumb\n"
    for name, addr in sorted(stock_functions(device.name).items()):
        new_syms += (
            f".global {name}\n.type {name}, %function\n.set {name}, 0x{addr:08X}\n"
        )
    try:
        old_syms = syms_path.read_text()
    except FileNotFoundError:
        old_syms = ""
    if new_syms != old_syms:
        syms_path.parent.mkdir(exist_ok=True)
        syms_path.write_text(new_syms)
