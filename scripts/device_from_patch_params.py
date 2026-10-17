""" Dictates device used in Makefile and C parts of the code
"""
import argparse
from pathlib import Path

from patches import Device
from patches.syms import stock_functions

# Our .data/.bss: RAM the stock firmware never writes.
RAM = {
    "mario": (0x3001_0000, 64 * (1 << 10) - 8192),
    # AXI RAM stock never writes, below app_state_timer (tools/re/zelda.syms).
    # Most other AXI RAM that looks free holds the per-game app_state_*
    # buffers, which must survive our runtime LZMA calls (e.g. the timer).
    "zelda": (0x240D_3560, 0x240D_6000 - 0x240D_3560),
}

# --device=amalgam builds one image for both firmwares (Mario's stock code stays
# at the start of bank 1).
AMALGAM = "amalgam"


def _write_if_changed(path, text):
    try:
        old = path.read_text()
    except FileNotFoundError:
        old = ""
    if text != old:
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)


def device_ld(name):
    if name == AMALGAM:
        # Our code starts past both stock images: each firmware is patched in
        # its own buffer at its stock addresses (patches/amalgam.py). Each
        # owns all RAM while it runs, so each keeps its own objects' RAM in its
        # own free window (STM32H7B0VBTx_FLASH_amalgam.ld).
        end = max(Device.registry[m].Int.STOCK_ROM_END for m in RAM)
        ld = f"\n__STOCK_ROM_END__ = 0x{end:08X};\n"
        for model, (origin, length) in RAM.items():
            ld += f"__{model.upper()}_RAM_ORIGIN__ = 0x{origin:08X};\n"
            ld += f"__{model.upper()}_RAM_LENGTH__ = 0x{length:X};\n"
        return ld
    origin, length = RAM[name]
    return f"""
__STOCK_ROM_END__ = 0x{Device.registry[name].Int.STOCK_ROM_END:08X};
__RAM_ORIGIN__ = 0x{origin:08X};
__RAM_LENGTH__ = 0x{length:X};
"""


def stock_symbols(models):
    """Every stock function in tools/re/<model>.syms as an absolute Thumb
    function symbol stock_<model>_<name>, so ld emits plain BLs and each use
    is a relocation the patcher can follow."""
    s = ".syntax unified\n.thumb\n"
    for model in models:
        for name, addr in sorted(stock_functions(model).items()):
            s += f".global {name}\n.type {name}, %function\n.set {name}, 0x{addr:08X}\n"
    return s


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--device", default="mario", choices=["mario", "zelda", AMALGAM]
    )
    args, _ = parser.parse_known_args()
    print(args.device.upper())

    models = list(RAM) if args.device == AMALGAM else [args.device]
    _write_if_changed(Path("build/device.ld"), device_ld(args.device))
    _write_if_changed(Path("build/stock_symbols.s"), stock_symbols(models))
