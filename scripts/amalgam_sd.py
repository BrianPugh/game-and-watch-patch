"""Link our code to end at the SD bootloader (the amalgam with --sd-bootloader).

The first link (build/pass1/, the plain amalgam layout) gives our code's size;
this writes the second link's amalgam_text.ld, which starts our code that far
below ``SD_BOOTLOADER``. Zelda's compacted stock code has to end below it
(``Relocation.finalize`` checks), and what's between is a hole for data.

Usage: python -m scripts.amalgam_sd <pass-1 ELF> <amalgam_text.ld>
"""

import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile

from patches.amalgam import BASE, SD_BOOTLOADER

# Room for alignment padding that differs between the 2 links.
SLACK = 16


def code_start(size):
    """Where our ``size`` bytes of code and data start, ending by the SD
    bootloader."""
    return (SD_BOOTLOADER - size - SLACK) & ~0xF


def text_ld(elf_path):
    with open(elf_path, "rb") as f:
        symtab = ELFFile(f).get_section_by_name(".symtab")
        ours = symtab.get_symbol_by_name("__our_code__")[0]["st_value"]
        end = symtab.get_symbol_by_name("__flash_end__")[0]["st_value"]
    start = code_start(end - ours)
    return (
        f"__STOCK_ROM_END__ = 0x{start - BASE:08X};\n"
        "INCLUDE STM32H7B0VBTx_FLASH_text.ld\n"
    )


def main():
    elf_path, out = sys.argv[1:]
    Path(out).write_text(text_ld(elf_path))


if __name__ == "__main__":
    main()
