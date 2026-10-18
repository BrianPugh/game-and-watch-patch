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
FLASH_BASE = 0x0800_0000
FLASH_END = FLASH_BASE + 0x4_0000


def _write_if_changed(path, text):
    try:
        old = path.read_text()
    except FileNotFoundError:
        old = ""
    if text != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def device_ld(name, sd_bootloader=False):
    if name == AMALGAM:
        # Our code starts past Zelda's relocated stock code (patches/amalgam.py).
        # Each firmware owns all RAM while it runs, so each keeps its own
        # objects' RAM in its own free window (STM32H7B0VBTx_FLASH_amalgam.ld).
        from patches.amalgam import OUR_CODE, SD_HOLES, ZELDA_DST

        ld = f"\n__zelda_vector_table__ = 0x{ZELDA_DST:08X};\n"
        for model, (origin, length) in RAM.items():
            ld += f"__{model.upper()}_RAM_ORIGIN__ = 0x{origin:08X};\n"
            ld += f"__{model.upper()}_RAM_LENGTH__ = 0x{length:X};\n"
        if not sd_bootloader:
            ld += f"__STOCK_ROM_END__ = 0x{OUR_CODE - FLASH_BASE:08X};\n"
            return (
                ld + "MEMORY\n{\n  FLASH (xr) : ORIGIN = 0x8000000, LENGTH = 256K\n}\n"
            )
        # Our code goes into the holes (scripts/amalgam_holes.py), the rest
        # into FLASH, what's left past Zelda's code; .text starts at its origin.
        ld += "__STOCK_ROM_END__ = 0;\nMEMORY\n{\n"
        ld += f"  BIN_ORIGIN (r) : ORIGIN = 0x{FLASH_BASE:08X}, LENGTH = 4\n"
        for start, end, _ in SD_HOLES:
            ld += f"  HOLE_{start:08X} (xr) : ORIGIN = 0x{start:08X}, LENGTH = 0x{end - start:X}\n"
        ld += f"  FLASH (xr) : ORIGIN = 0x{OUR_CODE:08X}, LENGTH = 0x{FLASH_END - OUR_CODE:X}\n"
        return ld + "}\n"
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
    parser.add_argument("--sd-bootloader", action="store_true")
    args, _ = parser.parse_known_args()
    print(args.device.upper())

    models = list(RAM) if args.device == AMALGAM else [args.device]
    _write_if_changed(
        Path("build/device.ld"), device_ld(args.device, args.sd_bootloader)
    )
    if args.device == AMALGAM:
        # The amalgam's linker script INCLUDEs amalgam_text.ld from the -L
        # path. With --sd-bootloader, the Makefile first links the plain
        # amalgam layout (build/pass1/), and scripts/amalgam_holes.py writes
        # build/amalgam_text.ld from that link's map.
        text_ld = "INCLUDE STM32H7B0VBTx_FLASH_text.ld\n"
        pass1 = Path("build/pass1")
        if args.sd_bootloader:
            _write_if_changed(pass1 / "device.ld", device_ld(args.device))
            _write_if_changed(pass1 / "amalgam_text.ld", text_ld)
        else:
            _write_if_changed(Path("build/amalgam_text.ld"), text_ld)
    _write_if_changed(Path("build/stock_symbols.s"), stock_symbols(models))
