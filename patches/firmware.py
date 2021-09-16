import hashlib

from Crypto.Cipher import AES
from elftools.elf.elffile import ELFFile

from .patch import DevicePatchMixin, FirmwarePatchMixin
from .exception import InvalidStockRomError, MissingSymbolError
from .lz import decompress

class Firmware(FirmwarePatchMixin, bytearray):

    RAM_BASE = 0x02000000
    RAM_LEN  = 0x00020000
    ENC_LEN  = 0

    def __init__(self, firmware, elf=None):
        with open(firmware, 'rb') as f:
            firmware_data = f.read()

        super().__init__(firmware_data)
        self._verify()

    def int(self, offset : int, size=4):
        return int.from_bytes(self[offset:offset+size], 'little')

    def set_range(self, start : int, end : int, val : bytes):
        self[start:end] = val * (end - start)
        return end - start

    def clear_range(self, start : int, end : int):
        return self.set_range(start, end, val=b"\x00")

    def show(self, wrap=1024, show=True):
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
        if show:
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

        self.rwdata = decompress(self[0x18e75:0x18e75+(0x8fc >> 1)])

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


def _nonce_to_iv(nonce):
    # need to convert nonce to 2
    assert len(nonce) == 8
    nonce = nonce[::-1]
    # The lower 28bits (counter) will be updated in `crypt` method
    return nonce + b"\x00\x00" + b"\x71\x23" + b"\x20\x00" + b"\x00\x00"


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

    def crypt(self, key, nonce):
        """ Decrypts if encrypted; encrypts if in plain text.
        """
        key = bytes(key[::-1])
        iv = bytearray(_nonce_to_iv(nonce))

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

class Device(DevicePatchMixin):
    def __init__(self, internal, external):
        self.internal = internal
        self.external = external

    def crypt(self):
        self.external.crypt(self.internal.key, self.internal.nonce)

    def show(self, show=True):
        import matplotlib.pyplot as plt
        plt.subplot(2, 1, 1)
        self.internal.show(show=False)
        plt.subplot(2, 1, 2)
        self.external.show(show=False)
        if show:
            plt.show()

