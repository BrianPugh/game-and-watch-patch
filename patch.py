import sys
if sys.version_info[0] < 3 or sys.version_info[1] < 6:
    raise Exception("Must be using at least Python 3.6")


from pathlib import Path
import argparse
import hashlib
from elftools.elf.elffile import ELFFile
from Crypto.Cipher import AES

from patches import parse_patches, add_patch_args, patch_args_validation

import colorama
from colorama import Fore, Back, Style
colorama.init()


class MissingSymbolError(Exception):
    """"""

class Firmware(bytearray):

    RAM_BASE = 0x02000000
    RAM_LEN  = 0x00020000
    ENC_LEN  = 0

    def __init__(self, firmware, elf=None):
        with open(firmware, 'rb') as f:
            firmware_data = f.read()

        super().__init__(firmware_data)
        self._verify()

    def show(self, wrap=1024):
        import numpy as  np
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker

        def to_hex(x, pos):
            return f"0x{int(x):06X}"

        def to_hex_wrap(x, pos):
            return f"0x{int(x)*wrap:06X}"

        n_bytes = len(self)
        rows = int(np.ceil(n_bytes / wrap))
        occupied = np.array(self) != 0
        plt.imshow(occupied.reshape(rows, wrap))
        plt.title(str(self))
        axes = plt.gca()
        axes.get_xaxis().set_major_locator(ticker.MultipleLocator(128))
        axes.get_xaxis().set_major_formatter(ticker.FuncFormatter(to_hex))
        axes.get_yaxis().set_major_locator(ticker.MultipleLocator(32))
        axes.get_yaxis().set_major_formatter(ticker.FuncFormatter(to_hex_wrap))
        plt.show()


class IntFirmware(Firmware):
    STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"

    FLASH_BASE = 0x08000000
    FLASH_LEN  = 0x00020000

    STOCK_ROM_END = 0x00019300

    def __init__(self, firmware, elf):
        super().__init__(firmware)
        self._elf_f = open(elf, 'rb')
        self.elf = ELFFile(self._elf_f)
        self.symtab = self.elf.get_section_by_name('.symtab')

    def __str__(self):
        return "internal"

    def _verify(self):
        h = hashlib.sha1(self).hexdigest()
        if h != self.STOCK_ROM_SHA1_HASH:
            raise InvalidStockRomError

    def address(self, symbol_name):
        symbols = self.symtab.get_symbol_by_name(symbol_name)
        if not symbols:
            raise MissingSymbolError(f"Cannot find symbol \"{symbol_name}\"")
        address = symbols[0]['st_value']
        if not address or not (
                (self.RAM_BASE <= address <= self.RAM_BASE + self.RAM_LEN) or
                (self.FLASH_BASE <= address <= self.FLASH_BASE + self.FLASH_LEN)
        ):
            raise MissingSymbolError(f"Symbol \"{symbol_name}\" has invalid address 0x{address:08X}")
        print(f"    found {symbol_name} at 0x{address:08X}")
        return address

    @property
    def key(self):
        offset = 0x106f4
        return self[offset: offset+16]

    @property
    def nonce(self):
        offset = 0x106e4
        return self[offset:offset+8]


class ExtFirmware(Firmware):
    STOCK_ROM_SHA1_HASH = "eea70bb171afece163fb4b293c5364ddb90637ae"

    FLASH_BASE = 0x9000_0000
    FLASH_LEN  = 0x0010_0000
    ENC_LEN    = 0xF_E000  # end address at 0x080106ec
    STOCK_ROM_END  = 0x0010_0000

    def __str__(self):
        return "external"

    def _verify(self):
        h = hashlib.sha1(self[:-8192]).hexdigest()
        if h != self.STOCK_ROM_SHA1_HASH:
            raise InvalidStockRomError

    def _nonce_to_iv(self, nonce):
        # need to convert nonce to 2
        assert len(nonce) == 8
        nonce = nonce[::-1]
        # The lower 28bits (counter) will be updated in `crypt` method
        return nonce + b"\x00\x00" + b"\x71\x23" + b"\x20\x00" + b"\x00\x00"

    def crypt(self, key, nonce):
        """ Decrypts if encrypted; encrypts if in plain text.
        """
        key = bytes(key[::-1])
        iv = bytearray(self._nonce_to_iv(nonce))

        aes = AES.new(key, AES.MODE_ECB)

        for offset in range(0, self.ENC_LEN, 128 // 8):
            counter_block = iv.copy()

            counter = (self.FLASH_BASE + offset) >> 4
            counter_block[12] = ((counter >> 24) & 0x0F) | (counter_block[12] & 0xF0)
            counter_block[13] = (counter >> 16) & 0xFF
            counter_block[14] = (counter >> 8) & 0xFF
            counter_block[15] = (counter >> 0) & 0xFF

            cipher_block = aes.encrypt(bytes(counter_block))
            for i, cipher_byte in enumerate(reversed(cipher_block)):
                self[offset + i] ^= cipher_byte


class InvalidStockRomError(Exception):
    """The provided stock ROM did not contain the expected data."""


class InvalidPatchError(Exception):
    """"""


def parse_args():
    parser = argparse.ArgumentParser(description="Game and Watch Firmware Patcher.")

    #########################
    # Global configurations #
    #########################
    parser.add_argument('--int-firmware', type=Path, default="internal_flash_backup.bin",
                        help="Input stock internal firmware.")
    parser.add_argument('--ext-firmware', type=Path, default="flash_backup.bin",
                        help="Input stock external firmware.")
    parser.add_argument('--patch', type=Path, default="build/gw_patch.bin",
                        help="Compiled custom code to insert at the end of the internal firmware")
    parser.add_argument('--elf', type=Path, default="build/gw_patch.elf",
                        help="ELF file corresponding to the bin provided by --patch")
    parser.add_argument('--int-output', type=Path, default="build/internal_flash_patched.bin",
                        help="Patched internal firmware.")
    parser.add_argument('--ext-output', type=Path, default="build/external_flash_patched.bin",
                        help="Patched external firmware.")

    parser.add_argument("--extended", action="store_true", default=False,
                        help="256KB internal flash image instead of 128KB.")

    debugging = parser.add_argument_group("debugging")
    debugging.add_argument("--show", action="store_true",
                           help="Show a picture representation of the external patched binary.")


    ########################
    # Patch configurations #
    ########################
    patches = parser.add_argument_group('patches')
    add_patch_args(patches)

    # Final Validation
    args = parser.parse_args()

    patch_args_validation(parser, args)

    return args


def main():
    args = parse_args()

    int_firmware = IntFirmware(args.int_firmware, args.elf)
    ext_firmware = ExtFirmware(args.ext_firmware)

    # Decrypt the external firmware
    ext_firmware.crypt(int_firmware.key, int_firmware.nonce)
    #Path("decrypt.bin").write_bytes(ext_firmware)

    # Copy over novel code
    patch = args.patch.read_bytes()
    if len(int_firmware) != len(patch):
        raise InvalidPatchError(f"Expected patch length {len(int_firmware)}, got {len(patch)}")
    int_firmware[int_firmware.STOCK_ROM_END:] = patch[int_firmware.STOCK_ROM_END:]
    del patch

    if args.extended:
        int_firmware.extend(b"\x00" * 0x20000)

    # Perform all replacements in stock code.
    patches = parse_patches(args)

    print(Fore.BLUE)
    print("#########################")
    print("# BEGINING BINARY PATCH #")
    print("#########################" + Style.RESET_ALL)

    for p in patches:
        if ext_firmware.FLASH_BASE <= p.offset < ext_firmware.FLASH_BASE + ext_firmware.FLASH_LEN:
            p.offset -= ext_firmware.FLASH_BASE
            firmware = ext_firmware
            color = Fore.YELLOW
        else:
            firmware = int_firmware
            color = Fore.MAGENTA
        if p.message:
            print(f"{color}Applying {str(firmware)} patch:{Style.RESET_ALL}  \"{p.message}\"")

        if p.command == "move_to_int":
            p(ext_firmware, int_firmware)
        else:
            p(firmware)

    if args.show:
        # Debug visualization
        int_firmware.show()
        ext_firmware.show()

    # Re-encrypt the external firmware
    Path("build/decrypt_flash_patched.bin").write_bytes(ext_firmware)
    ext_firmware.crypt(int_firmware.key, int_firmware.nonce)

    # Save patched firmware
    args.int_output.write_bytes(int_firmware)
    args.ext_output.write_bytes(ext_firmware)

    print(Fore.GREEN)
    print( "Binary Patching Complete!")
    print(f"    Internal Firmware Used: {len(int_firmware)} bytes")  # TODO: show free amount
    print(f"    External Firmware Used: {len(ext_firmware)} bytes")
    print(Style.RESET_ALL)


if __name__ == "__main__":
    main()
