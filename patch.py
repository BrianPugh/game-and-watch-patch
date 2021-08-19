"""
Responsibilities of this script:
"""

from pathlib import Path
import argparse
import hashlib
from elftools.elf.elffile import ELFFile

from patches import parse_patches


class MissingSymbolError(Exception):
    """"""

class Firmware(bytearray):
    STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"
    STOCK_ROM_END = 0x00019300

    def __init__(self, firmware, elf):
        with open(firmware, 'rb') as f:
            firmware_data = f.read()

        super().__init__(firmware_data)

        self._elf_f = open(elf, 'rb')
        self.elf = ELFFile(self._elf_f)
        self.symtab = self.elf.get_section_by_name('.symtab')

    def address(self, symbol_name):
        symbols = self.symtab.get_symbol_by_name(symbol_name)
        if not symbols:
            raise MissingSymbolError(f"Cannot find symbol \"{symbol_name}\"")
        address = symbols[0]['st_value']
        if not address or not ((0x20000000 <= address <= 0x2002000) or (0x08000000 <= address <= 0x08100000)):
            raise MissingSymbolError(f"Symbol \"{symbol_name}\" has invalid address 0x{address:08X}")
        print(f"found {symbol_name} at 0x{address:08X}")
        return address


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
    if h != Firmware.STOCK_ROM_SHA1_HASH:
        raise InvalidStockRomError

def main():
    args = parse_args()

    firmware = Firmware(args.firmware, args.elf)
    verify_stock_firmware(firmware)

    # Copy over novel code
    patch = args.patch.read_bytes()
    if len(firmware) != len(patch):
        raise InvalidPatchError(f"Expected patch length {len(firmware)}, got {len(patch)}")
    firmware[Firmware.STOCK_ROM_END:] = patch[Firmware.STOCK_ROM_END:]

    # Perform all replacements in stock code.
    patches = parse_patches(args)
    for p in patches:
        if p.message:
            print(f"Applying patch:  \"{p.message}\"")
        p(firmware)

    # Save patched firmware
    args.output.write_bytes(firmware)


if __name__ == "__main__":
    main()
