"""
Responsibilities of this script:
"""

from pathlib import Path
import argparse
import hashlib
from elftools.elf.elffile import ELFFile

STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"
STOCK_ROM_END = 0x00019300

class InvalidStockRomError(Exception):
    """The provided stock ROM did not contain the expected data."""

class InvalidPatchError(Exception):
    """"""

def parse_args():
    parser = argparse.ArgumentParser(description="Game and Watch Firmware Patcher.")
    parser.add_argument('--firmware', type=Path, default="internal_flash_backup.bin",
                        help="Input stock firmware.")
    parser.add_argument('--patch', type=Path, default="build/gw_patch.bin",
                        help="")
    parser.add_argument('--elf', type=Path, default="build/gw_patch.elf",
                        help="")
    parser.add_argument('--output', '-o', type=Path, default="build/internal_flash_patched.bin",
                        help="")

    return parser.parse_args()

def verify_stock_firmware(data):
    h = hashlib.sha1(data).hexdigest()
    if h != STOCK_ROM_SHA1_HASH:
        raise InvalidStockRomError

def main():
    args = parse_args()

    firmware = bytearray(args.firmware.read_bytes())
    verify_stock_firmware(firmware)

    # Copy over novel code
    patch = args.patch.read_bytes()
    if len(firmware) != len(patch):
        raise InvalidPatchError(f"Expected patch length {len(firmware)}, got {len(patch)}")
    firmware[STOCK_ROM_END:] = patch[STOCK_ROM_END:]

    # Perform all replacements in stock code.
    # TODO: make this an engine
    with open(args.elf, 'rb') as f:
        elf = ELFFile(f)
        symtab = elf.get_section_by_name('.symtab')

        def address(name):
            return symtab.get_symbol_by_name(name)[0]['st_value']

        def print_sym(name):
            print(f"{name} is at 0x{address(name):08x}")

        print_sym("foo")
        #print_sym("main")
        print_sym("Reset_Handler")

    # Save patched firmware
    args.output.write_bytes(firmware)


if __name__ == "__main__":
    main()
