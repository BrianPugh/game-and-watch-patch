import hashlib

from Crypto.Cipher import AES
from elftools.elf.elffile import ELFFile
from colorama import Fore, Back, Style

from .patch import DevicePatchMixin, FirmwarePatchMixin
from .exception import InvalidStockRomError, MissingSymbolError, NotEnoughSpaceError
from .compression import lz77_decompress, lzma_compress


def _val_to_color(val):
    if 0x9010_0000 > val >= 0x9000_0000:
        return Fore.YELLOW
    elif 0x0804_0000 > val >= 0x0800_0000:
        return Fore.MAGENTA
    else:
        return ""

class Lookup(dict):
    def __repr__(self):
        substrs = []
        substrs.append("{")
        for k, v in sorted(self.items()):
            k_color = _val_to_color(k)
            v_color = _val_to_color(v)

            substrs.append(f"    {k_color}0x{k:08X}{Style.RESET_ALL}: "
                           f"{v_color}0x{v:08X}{Style.RESET_ALL},"
                           )
        substrs.append("}")
        return "\n".join(substrs)


class Firmware(FirmwarePatchMixin, bytearray):

    RAM_BASE = 0x02000000
    RAM_LEN  = 0x00020000
    ENC_LEN  = 0

    FLASH_BASE = 0x0000_0000
    FLASH_LEN = 0

    def __init__(self, firmware=None):
        if firmware:
            with open(firmware, 'rb') as f:
                firmware_data = f.read()
            super().__init__(firmware_data)
        else:
            super().__init__(self.FLASH_LEN)

        self._lookup = Lookup()
        self._verify()

    def _verify(self):
        pass

    def __getitem__(self, key):
        """ Properly raises index error if trying to access oob regions.
        """

        if isinstance(key, slice):
            if key.start is not None:
                try:
                    self[key.start]
                except IndexError:
                    raise IndexError(f"Index {key.start} ({hex(key.start)}) out of range")
            if key.stop is not None:
                try:
                    self[key.stop - 1]
                except IndexError:
                    raise IndexError(f"Index {key.stop - 1} ({hex(key.stop - 1)}) out of range")

        return super().__getitem__(key)

    def __setitem__(self, key, new_val):
        """ Properly raises index error if trying to access oob regions.
        """

        if isinstance(key, slice):
            if key.start is not None:
                try:
                    self[key.start]
                except IndexError:
                    raise NotEnoughSpaceError(f"Starting index {key.start} ({hex(key.start)}) exceeds firmware length {len(self)} ({hex(len(self))})")
            if key.stop is not None:
                try:
                    self[key.stop - 1]
                except IndexError:
                    raise NotEnoughSpaceError(f"Ending index {key.stop - 1} ({hex(key.stop - 1)}) exceeds firmware length {len(self)} ({hex(len(self))})")

        return super().__setitem__(key, new_val)

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

class RWData:
    """
    Assumptions (which are valid for this firmware):
        1. Only compressed rwdata is after this table
        2. We are only modifying the lz_decompress stuff.
    """

    # THIS HAS TO AGREE WITH THE LINKER
    MAX_TABLE_ELEMENTS = 5

    def __init__(self, firmware, table_start, table_len):
        # We want to be able to extend the table.

        self.firmware = firmware
        self.table_start = table_start

        self.datas, self.dsts = [], []

        for i in range(table_start, table_start + table_len - 4, 16):
            # First thing is pointer to executable, need to always replace this
            # to our lzma
            rel_offset_to_fn = firmware.int(i)
            if rel_offset_to_fn > 0x8000_0000:
                rel_offset_to_fn -= 0x1_0000_0000
            fn_addr = i + rel_offset_to_fn
            assert fn_addr == 0x18005  # lz_decompress function
            i += 4

            data_addr = i + firmware.int(i)
            i += 4
            data_len = firmware.int(i) >> 1
            i += 4
            data_dst = firmware.int(i)
            i += 4

            data = lz77_decompress(firmware[data_addr : data_addr + data_len])
            print(f"    lz77 decompressed data {data_len} -> {len(data)}")
            firmware.clear_range(data_addr, data_addr + data_len)

            self.append(data, data_dst)

        last_element_offset = table_start + table_len - 4
        self.last_fn = firmware.int(last_element_offset)
        if self.last_fn > 0x8000_0000:
            self.last_fn -= 0x1_0000_0000
        self.last_fn += last_element_offset

        # Mark this area as reserved; there's nothing special about 0x77, its
        # just not 0x00
        firmware.set_range(table_start, table_start + 16 * self.MAX_TABLE_ELEMENTS + 4, b"\x77")


    def __getitem__(self, k):
        return self.datas[k]

    @property
    def table_end(self):
        return self.table_start + 4 * 4 * len(self.datas) + 4

    def append(self, data, dst):
        """ Add a new element to the table
        """

        if len(self.datas) >= self.MAX_TABLE_ELEMENTS:
            raise NotEnoughSpaceError(f"MAX_TABLE_ELEMENTS exceeded; increase this value")

        self.datas.append(data)
        self.dsts.append(dst)

        assert len(self.datas) == len(self.dsts)

    @property
    def compressed_len(self):
        compressed_len = 0
        for data in self.datas:
            compressed_data = lzma_compress(bytes(data))
            compressed_len += len(compressed_data)
        return compressed_len

    def write_table_and_data(self, data_offset=None):
        """
        Parameters
        ----------
        data_offset : int
            Where to write the compressed data
        """

        # Write Compressed Data
        data_addrs, data_lens = [], []
        if data_offset is None:
            index = self.table_end
        else:
            index = data_offset

        total_len = 0
        for data in self.datas:
            compressed_data = lzma_compress(bytes(data))
            print(f"    compressed {len(data)}->{len(compressed_data)} bytes (saves {len(data)-len(compressed_data)}). Writing to 0x{index:05X}")
            self.firmware[index:index+len(compressed_data)] = compressed_data

            data_addrs.append(index)
            data_lens.append(len(compressed_data))

            index += len(compressed_data)
            total_len += len(compressed_data)

        # Write Table
        index = self.table_start
        assert len(data_addrs) == len(data_lens) == len(self.dsts)
        for data_addr, data_len, data_dst in zip(data_addrs, data_lens, self.dsts):
            self.firmware.relative(index, "rwdata_inflate")
            index += 4

            # Assumes that the data will be after the table.
            rel_addr = data_addr - index
            if rel_addr < 0:
                rel_addr += 0x1_0000_0000
            self.firmware.replace(index, rel_addr, size=4)
            index += 4

            self.firmware.replace(index, data_len, size=4)
            index += 4

            self.firmware.replace(index, data_dst, size=4)
            index += 4

        self.firmware.relative(index, self.last_fn, size=4)
        index += 4

        assert index == self.table_end

        # Update the pointer to the end of table in the loader
        self.firmware.relative(0x17db4, index, size=4)

        print(self)

        return total_len

    def __str__(self):
        """ Returns the **written** table.

        Doesn't show unstaged changes.
        """
        substrs = []
        substrs.append("")
        substrs.append("RWData Table")
        substrs.append("------------")
        for addr in range(self.table_start, self.table_end - 4, 16):
            substrs.append(f"0x{addr:08X}:  "
                   f"0x{self.firmware.int(addr + 0):08X}  "
                  f"0x{self.firmware.int(addr + 4):08X}  "
                  f"0x{self.firmware.int(addr + 8):08X}  "
                  f"0x{self.firmware.int(addr + 12):08X}  "
                  )
        addr = self.table_end - 4
        substrs.append(f"0x{addr:08X}:  0x{self.firmware.int(addr + 0):08X}")
        substrs.append("")
        return "\n".join(substrs)




class IntFirmware(Firmware):
    STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"

    FLASH_BASE = 0x08000000
    FLASH_LEN  = 0x00020000

    STOCK_ROM_END = 0x00019300  # Actual stock rom end

    def __init__(self, firmware, elf):
        super().__init__(firmware)
        self._elf_f = open(elf, 'rb')
        self.elf = ELFFile(self._elf_f)
        self.symtab = self.elf.get_section_by_name('.symtab')

        self.rwdata = RWData(self, 0x1_80a4, 36)

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

class SRAM3(Firmware):
    # This value was reached by trial and error until SMB2 would work.
    FLASH_BASE = 0x240e7480
    FLASH_LEN = 0x24100000 - FLASH_BASE

    def __str__(self):
        return "sram3"


class Device(DevicePatchMixin):
    def __init__(self, internal, external):
        self.internal = internal
        self.external = external

        self.sram3 = SRAM3()

        self.lookup = Lookup()
        self.internal._lookup = self.lookup
        self.external._lookup = self.lookup
        self.sram3._lookup = self.lookup

        # insert some noops so we can hijack some ram
        self.internal.nop(0x677a, 1)
        self.internal.asm(0x677a+2, "add r0, r0, r1")


    def crypt(self):
        self.external.crypt(self.internal.key, self.internal.nonce)

    def show(self, show=True):
        import matplotlib.pyplot as plt
        if len(self.external):
            plt.subplot(2, 1, 1)
            self.internal.show(show=False)
            plt.subplot(2, 1, 2)
            self.external.show(show=False)
        else:
            self.internal.show(show=False)
        if show:
            plt.show()
