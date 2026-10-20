"""The C build's configuration for PATCH_PARAMS (``build/config.mk``, from
``patches/prebuilt.py``) and the files generated from it: the device linker
variables and the stock function symbols.
"""

import sys
from pathlib import Path

from patches import Device
from patches.prebuilt import config_for
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
        from patches.amalgam import OUR_CODE, SD_BOOTLOADER, ZELDA_DST

        ld = f"\n__zelda_vector_table__ = 0x{ZELDA_DST:08X};\n"
        for model, (origin, length) in RAM.items():
            ld += f"__{model.upper()}_RAM_ORIGIN__ = 0x{origin:08X};\n"
            ld += f"__{model.upper()}_RAM_LENGTH__ = 0x{length:X};\n"
        if not sd_bootloader:
            ld += f"__STOCK_ROM_END__ = 0x{OUR_CODE - FLASH_BASE:08X};\n"
            return (
                ld + "MEMORY\n{\n  FLASH (xr) : ORIGIN = 0x8000000, LENGTH = 256K\n}\n"
            )
        # Our code ends at the SD bootloader; amalgam_text.ld (written by
        # scripts/amalgam_sd.py) sets where it starts.
        length = SD_BOOTLOADER - FLASH_BASE
        return (
            ld
            + f"MEMORY\n{{\n  FLASH (xr) : ORIGIN = 0x8000000, LENGTH = 0x{length:X}\n}}\n"
        )
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
    # Exits with the parser's message on a bad option.
    config = config_for(sys.argv[1:])
    device = config.device
    sd_bootloader = "SD_BOOTLOADER" in config.defines
    _write_if_changed(Path("build/config.mk"), config.makefile())

    models = list(RAM) if device == AMALGAM else [device]
    _write_if_changed(Path("build/device.ld"), device_ld(device, sd_bootloader))
    if device == AMALGAM:
        # The amalgam's linker script INCLUDEs amalgam_text.ld from the -L
        # path. With --sd-bootloader, the Makefile first links the plain
        # amalgam layout (build/pass1/), and scripts/amalgam_sd.py writes
        # build/amalgam_text.ld from that link's size of our code.
        text_ld = "INCLUDE STM32H7B0VBTx_FLASH_text.ld\n"
        pass1 = Path("build/pass1")
        if sd_bootloader:
            _write_if_changed(pass1 / "device.ld", device_ld(device))
            _write_if_changed(pass1 / "amalgam_text.ld", text_ld)
        else:
            _write_if_changed(Path("build/amalgam_text.ld"), text_ld)
    _write_if_changed(Path("build/stock_symbols.s"), stock_symbols(models))
