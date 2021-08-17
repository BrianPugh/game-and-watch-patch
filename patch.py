"""
Responsibilities of this script:
"""

from pathlib import Path
import argparse
import hashlib
from elftools.elf.elffile import ELFFile

from patches import parse_patches

STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"
STOCK_ROM_END = 0x00019300

class Firmware(bytearray):
    def __init__(self, firmware, elf):
        with open(firmware, 'rb') as f:
            firmware_data = f.read()

        super().__init__(firmware_data)

        self._elf_f = open(elf, 'rb')
        self.elf = ELFFile(self._elf_f)
        self.symtab = self.elf.get_section_by_name('.symtab')

    def address(self, symbol_name):
        return self.symtab.get_symbol_by_name(symbol_name)[0]['st_value']

    def patch(self, offset, data, size=None):
        """
        Parameters
        ----------
        size :
        Returns
        -------
        int
            Number of bytes patched
        """

        if not isinstance(offset, int):
            raise ValueError(f"offset must be an int; got {type(offset)}")

        if offset >= len(self):
            raise IndexError(f"Patch offset {offset} exceeds firmware length {len(self)}")

        if offset >= STOCK_ROM_END:
            raise IndexError(f"Patch offset {offset} exceeds stock firmware region {STOCK_ROM_END}")

        n_bytes_patched = 0

        if isinstance(data, bytes):
            # Write the bytes at that address as is.
            self[offset:offset + len(data)] = data
            n_bytes_patched = len(data)
        elif isinstance(data, list) or isinstance(data, tuple):
            for elem in data:
                n_bytes_patched += self.patch(offset + n_bytes_patched, elem)
        elif isinstance(data, str):
            if size:
                raise ValueError("Don't specify size when providing a symbol name.")
            data = self.address(data)
            self[offset:offset+4] = data.to_bytes(4, 'little')
        elif isinstance(data, int):
            # must be 1, 2, or 4 bytes
            if size is None:
                raise ValueError("Must specify \"size\" when providing int data")
            if size not in (1,2,4):
                raise ValueError(f"Size must be one of {1, 2, 4}; got {size}")
            self[offset:offset+size] = data.to_bytes(size, 'little')
        else:
            raise ValueError(f"Don't know how to parse data type \"{data}\"")

        return n_bytes_patched


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

    firmware = Firmware(args.firmware, args.elf)
    verify_stock_firmware(firmware)

    # Copy over novel code
    patch = args.patch.read_bytes()
    if len(firmware) != len(patch):
        raise InvalidPatchError(f"Expected patch length {len(firmware)}, got {len(patch)}")
    firmware[STOCK_ROM_END:] = patch[STOCK_ROM_END:]

    # Perform all replacements in stock code.
    patches = parse_patches(args)
    for p in patches:
        if p.message:
            print(f"Applying patch:  \"{p.message}\"")
        firmware.patch(p.offset, p.data, size=p.size)

    def print_sym(name):
        print(f"{name} is at 0x{firmware.address(name):08x}")

    print_sym("foo")
    print_sym("bootloader")
    #print_sym("main")
    print_sym("Reset_Handler")

    # Save patched firmware
    args.output.write_bytes(firmware)


if __name__ == "__main__":
    main()
