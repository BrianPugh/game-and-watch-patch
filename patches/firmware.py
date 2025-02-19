import hashlib
import struct
from dataclasses import dataclass

from colorama import Fore, Style
from Crypto.Cipher import AES
from elftools.elf.elffile import ELFFile

from .compression import lz77_decompress, lzma_compress
from .exception import (
    InvalidStockRomError,
    MissingSymbolError,
    NotEnoughSpaceError,
    ParsingError,
)
from .patch import FirmwarePatchMixin
from .utils import round_down_word, round_up_page, round_up_word


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

            substrs.append(
                f"    {k_color}0x{k:08X}{Style.RESET_ALL}: "
                f"{v_color}0x{v:08X}{Style.RESET_ALL},"
            )
        substrs.append("}")
        return "\n".join(substrs)


@dataclass
class HeaderMetaData:
    """4 bytes of data that can be stored at the hdmi-cec handler in the vector-table (0x01B8)"""

    external_flash_size: int  # Actual size in bytes (will be divided by 4096 for storage)
    is_mario: bool  # 1 bit
    is_zelda: bool  # 1 bit

    def pack(self) -> bytes:
        # Convert size to 4K blocks (right shift by 12)
        blocks_4k = round_up_page(self.external_flash_size) >> 12

        # Ensure the shifted value fits in 3 bytes
        if not (0 <= blocks_4k < (1 << 24)):
            raise ValueError(
                "external_flash_size must fit in 3 bytes when divided by 4096"
            )

        # Pack the flags into a single byte
        flags = (int(self.is_mario) << 0) | (int(self.is_zelda) << 1)

        # Pack as little-endian:
        # - First 3 bytes: external_flash_size
        # - Last byte: flags
        return struct.pack("<I", (blocks_4k & 0xFFFFFF) | (flags << 24))

    @classmethod
    def unpack(cls, data: bytes) -> "HeaderMetaData":
        # Unpack the 32-bit value
        [value] = struct.unpack("<I", data)

        # Extract fields
        # Convert from 4K blocks back to bytes (left shift by 12)
        external_flash_size = (value & 0xFFFFFF) << 12
        flags = (value >> 24) & 0xFF
        is_mario = bool(flags & (1 << 0))
        is_zelda = bool(flags & (1 << 1))

        return cls(external_flash_size, is_mario, is_zelda)


class Firmware(FirmwarePatchMixin, bytearray):

    RAM_BASE = 0x02000000
    RAM_LEN = 0x00020000

    FLASH_BASE = 0x0000_0000
    FLASH_LEN = 0

    def __init__(self, firmware=None):
        if firmware:
            with open(firmware, "rb") as f:
                firmware_data = f.read()
            super().__init__(firmware_data)
        else:
            super().__init__(self.FLASH_LEN)

        self._lookup = Lookup()
        self._verify()

    def _verify(self):
        pass

    def __getitem__(self, key):
        """Properly raises index error if trying to access oob regions."""

        if isinstance(key, slice):
            if key.start is not None:
                try:
                    self[key.start]
                except IndexError:
                    raise IndexError(
                        f"Index {key.start} ({hex(key.start)}) out of range"
                    ) from None
            if key.stop is not None:
                try:
                    self[key.stop - 1]
                except IndexError:
                    raise IndexError(
                        f"Index {key.stop - 1} ({hex(key.stop - 1)}) out of range"
                    ) from None

        return super().__getitem__(key)

    def __setitem__(self, key, new_val):
        """Properly raises index error if trying to access oob regions."""

        if isinstance(key, slice):
            if key.start is not None:
                try:
                    self[key.start]
                except IndexError:
                    raise NotEnoughSpaceError(
                        f"Starting index {key.start} ({hex(key.start)}) exceeds "
                        f"firmware length {len(self)} ({hex(len(self))})"
                    ) from None
            if key.stop is not None:
                try:
                    self[key.stop - 1]
                except IndexError:
                    raise NotEnoughSpaceError(
                        f"Ending index {key.stop - 1} ({hex(key.stop - 1)}) exceeds "
                        f"firmware length {len(self)} ({hex(len(self))})"
                    ) from None

        return super().__setitem__(key, new_val)

    def __str__(self):
        return self.__name__

    @staticmethod
    def hash(data):
        return hashlib.sha1(data).hexdigest()

    def int(self, offset: int, size=4):
        return int.from_bytes(self[offset : offset + size], "little")

    def set_range(self, start: int, end: int, val: bytes):
        self[start:end] = val * (end - start)
        return end - start

    def clear_range(self, start: int, end: int):
        return self.set_range(start, end, val=b"\x00")

    def show(self, wrap=1024, show=True):
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
        import numpy as np

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
    Assumptions:
        1. Only compressed rwdata is after this table
        2. We are only modifying the lz_decompress stuff.
    """

    # THIS HAS TO AGREE WITH THE LINKER
    MAX_TABLE_ELEMENTS = 5

    def __init__(self, firmware, table_start, table_len):
        # We want to be able to extend the table.

        self.firmware = firmware
        self.table_start = table_start
        self.__compressed_len_memo = {}

        self.datas, self.dsts = [], []

        for i in range(table_start, table_start + table_len - 4, 16):
            # First thing is pointer to executable, need to always replace this
            # to our lzma
            rel_offset_to_fn = firmware.int(i)
            if rel_offset_to_fn > 0x8000_0000:
                rel_offset_to_fn -= 0x1_0000_0000
            # fn_addr = i + rel_offset_to_fn
            # assert fn_addr == 0x18005  # lz_decompress function
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
        firmware.set_range(
            table_start, table_start + 16 * self.MAX_TABLE_ELEMENTS + 4, b"\x77"
        )

    def __getitem__(self, k):
        return self.datas[k]

    @property
    def table_end(self):
        return self.table_start + 4 * 4 * len(self.datas) + 4 + 4

    def append(self, data, dst):
        """Add a new element to the table"""

        if len(self.datas) >= self.MAX_TABLE_ELEMENTS:
            raise NotEnoughSpaceError(
                f"MAX_TABLE_ELEMENTS value {self.MAX_TABLE_ELEMENTS} exceeded"
            )

        self.datas.append(data)
        self.dsts.append(dst)

        assert len(self.datas) == len(self.dsts)

    @property
    def compressed_len(self):
        compressed_len = 0
        for data in self.datas:
            data = bytes(data)
            if data not in self.__compressed_len_memo:
                compressed_data = lzma_compress(bytes(data))
                self.__compressed_len_memo[data] = len(compressed_data)
            compressed_len += self.__compressed_len_memo[data]
        return compressed_len

    def write_table_and_data(self, end_of_table_reference, data_offset=None):
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
            print(
                f"    compressed {len(data)}->{len(compressed_data)} bytes "
                f"(saves {len(data)-len(compressed_data)}). "
                f"Writing to 0x{index:05X}"
            )
            self.firmware[index : index + len(compressed_data)] = compressed_data

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

        self.firmware.relative(index, "bss_rwdata_init")
        index += 4

        self.firmware.relative(index, self.last_fn, size=4)
        index += 4

        assert index == self.table_end

        # Update the pointer to the end of table in the loader
        self.firmware.relative(end_of_table_reference, index, size=4)

        print(self)

        return total_len

    def __str__(self):
        """Returns the **written** table.

        Doesn't show unstaged changes.
        """
        substrs = []
        substrs.append("")
        substrs.append("RWData Table")
        substrs.append("------------")
        for addr in range(self.table_start, self.table_end - 4 - 4, 16):
            substrs.append(
                f"0x{addr:08X}:  "
                f"0x{self.firmware.int(addr + 0):08X}  "
                f"0x{self.firmware.int(addr + 4):08X}  "
                f"0x{self.firmware.int(addr + 8):08X}  "
                f"0x{self.firmware.int(addr + 12):08X}  "
            )
        addr = self.table_end - 8
        substrs.append(f"0x{addr:08X}:  0x{self.firmware.int(addr + 0):08X}")
        addr = self.table_end - 4
        substrs.append(f"0x{addr:08X}:  0x{self.firmware.int(addr + 0):08X}")

        substrs.append("")
        return "\n".join(substrs)


class IntFirmware(Firmware):
    FLASH_BASE = 0x08000000
    FLASH_LEN = 0x00020000
    RWDATA_OFFSET = None
    RWDATA_LEN = 0
    RWDATA_ITCM_IDX = None
    RWDATA_DTCM_IDX = None

    def __init__(self, firmware, elf):
        super().__init__(firmware)
        self._elf_f = open(elf, "rb")
        self.elf = ELFFile(self._elf_f)
        self.symtab = self.elf.get_section_by_name(".symtab")
        if self.RWDATA_OFFSET is None:
            self.rwdata = None
        else:
            self.rwdata = RWData(self, self.RWDATA_OFFSET, self.RWDATA_LEN)

    def _verify(self):
        h = hashlib.sha1(self).hexdigest()
        if h != self.STOCK_ROM_SHA1_HASH:
            raise InvalidStockRomError

    def address(self, symbol_name, sub_base=False):
        symbols = self.symtab.get_symbol_by_name(symbol_name)
        if not symbols:
            raise MissingSymbolError(f'Cannot find symbol "{symbol_name}"')
        address = symbols[0]["st_value"]
        if address == 0:
            raise MissingSymbolError(f"{symbol_name} has address 0x0")
        print(f"    found {symbol_name} at 0x{address:08X}")
        if sub_base:
            address -= self.FLASH_BASE
        return address

    @property
    def empty_offset(self):
        """Detect a series of 0x00 to figure out the end of the internal firmware.

        Returns
        -------
        int
            Offset into firmware where empty region begins.
        """

        if self.rwdata is None:
            search_start = self.STOCK_ROM_END
        else:
            search_start = self.rwdata.table_end
        for addr in range(search_start, self.FLASH_LEN, 0x10):
            if self[addr : addr + 256] == b"\x00" * 256:
                int_pos_start = addr
                break
        else:
            raise ParsingError("Couldn't find end of internal code.")
        return int_pos_start

    @property
    def key(self):
        return self[self.KEY_OFFSET : self.KEY_OFFSET + 16]

    @property
    def nonce(self):
        return self[self.NONCE_OFFSET : self.NONCE_OFFSET + 8]


def _nonce_to_iv(nonce):
    # need to convert nonce to 2
    assert len(nonce) == 8
    nonce = nonce[::-1]
    # The lower 28bits (counter) will be updated in `crypt` method
    return nonce + b"\x00\x00" + b"\x71\x23" + b"\x20\x00" + b"\x00\x00"


class ExtFirmware(Firmware):
    FLASH_BASE = 0x9000_0000
    FLASH_LEN = 0x0010_0000

    ENC_START = 0
    ENC_END = 0

    def crypt(self, key, nonce):
        """Decrypts if encrypted; encrypts if in plain text."""
        key = bytes(key[::-1])
        iv = bytearray(_nonce_to_iv(nonce))

        aes = AES.new(key, AES.MODE_ECB)

        for offset in range(self.ENC_START, self.ENC_END, 128 // 8):
            counter_block = iv.copy()

            counter = (self.FLASH_BASE + offset) >> 4
            counter_block[12] = ((counter >> 24) & 0x0F) | (counter_block[12] & 0xF0)
            counter_block[13] = (counter >> 16) & 0xFF
            counter_block[14] = (counter >> 8) & 0xFF
            counter_block[15] = (counter >> 0) & 0xFF

            cipher_block = aes.encrypt(bytes(counter_block))
            for i, cipher_byte in enumerate(reversed(cipher_block)):
                self[offset + i] ^= cipher_byte


class Device:
    registry = {}

    def __init_subclass__(cls, name, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.name = name
        cls.registry[name] = cls

    def __init__(self, internal_bin, internal_elf, external_bin):
        self.internal = self.Int(internal_bin, internal_elf)
        self.external = self.Ext(external_bin)
        self.compressed_memory = self.FreeMemory()

        # Link all lookup tables to a single device instance
        self.lookup = Lookup()
        self.internal._lookup = self.lookup
        self.external._lookup = self.lookup
        self.compressed_memory._lookup = self.lookup

        self.ext_offset = 0
        self.int_pos = 0
        self.compressed_memory_pos = 0

    def _move_copy(
        self, dst, dst_offset: int, src, src_offset: int, size: int, delete: bool
    ) -> int:
        dst[dst_offset : dst_offset + size] = src[src_offset : src_offset + size]
        if delete:
            src.clear_range(src_offset, src_offset + size)

        for i in range(size):
            self.lookup[src.FLASH_BASE + src_offset + i] = (
                dst.FLASH_BASE + dst_offset + i
            )

        return size

    def _move(self, dst, dst_offset: int, src, src_offset: int, size: int) -> int:
        return self._move_copy(dst, dst_offset, src, src_offset, size, True)

    def _copy(self, dst, dst_offset: int, src, src_offset: int, size: int) -> int:
        return self._move_copy(dst, dst_offset, src, src_offset, size, False)

    # Convenience methods for move and copy
    def _move_ext_to_int(self, ext_offset: int, int_offset: int, size: int) -> int:
        return self._move(self.internal, int_offset, self.external, ext_offset, size)

    def _copy_ext_to_int(self, ext_offset: int, int_offset: int, size: int) -> int:
        return self._copy(self.internal, int_offset, self.external, ext_offset, size)

    def _move_to_compressed_memory(
        self, ext_offset: int, compressed_memory_offset: int, size: int
    ) -> int:
        return self._move(
            self.compressed_memory,
            compressed_memory_offset,
            self.external,
            ext_offset,
            size,
        )

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

    def compressed_memory_compressed_len(self, add_index=0):
        index = self.compressed_memory_pos + add_index
        if not index:
            return 0

        data = bytes(self.compressed_memory[:index])
        if data in self.compressed_memory_compressed_len.memo:
            return self.compressed_memory_compressed_len.memo[data]

        compressed_data = lzma_compress(data)
        self.compressed_memory_compressed_len.memo[data] = len(compressed_data)
        return len(compressed_data)

    compressed_memory_compressed_len.memo = {}

    @property
    def compressed_memory_free_space(self):
        return len(self.compressed_memory) - self.compressed_memory_pos

    @property
    def int_free_space(self):
        out = (
            len(self.internal) - self.int_pos - self.compressed_memory_compressed_len()
        )
        if self.internal.rwdata is not None:
            out -= self.internal.rwdata.compressed_len
        return out

    def rwdata_lookup(self, lower, size):
        lower += self.external.FLASH_BASE
        upper = lower + size

        for i in range(0, len(self.internal.rwdata[self.internal.RWDATA_DTCM_IDX]), 4):
            val = int.from_bytes(
                self.internal.rwdata[self.internal.RWDATA_DTCM_IDX][i : i + 4], "little"
            )
            if lower <= val < upper:
                new_val = self.lookup[val]
                print(f"    updating rwdata 0x{val:08X} -> 0x{new_val:08X}")
                self.internal.rwdata[self.internal.RWDATA_DTCM_IDX][
                    i : i + 4
                ] = new_val.to_bytes(4, "little")

    def rwdata_erase(self, lower, size):
        """
        Erasing no longer used references makes it compress better.
        """
        lower += 0x9000_0000
        upper = lower + size

        for i in range(0, len(self.internal.rwdata[self.internal.RWDATA_DTCM_IDX]), 4):
            val = int.from_bytes(
                self.internal.rwdata[self.internal.RWDATA_DTCM_IDX][i : i + 4], "little"
            )
            if lower <= val < upper:
                self.internal.rwdata[self.internal.RWDATA_DTCM_IDX][
                    i : i + 4
                ] = b"\x00\x00\x00\x00"

    def move_to_int(self, ext, size, reference):
        if self.int_free_space < size:
            raise NotEnoughSpaceError

        new_loc = self.int_pos

        if isinstance(ext, (bytes, bytearray)):
            self.internal[self.int_pos : self.int_pos + size] = ext
        else:
            self._move_ext_to_int(ext, self.int_pos, size=size)
            print(f"    move_ext_to_int {hex(ext)} -> {hex(self.int_pos)}")
        self.int_pos += round_up_word(size)

        if reference is not None:
            self.internal.lookup(reference)

        return new_loc

    def move_ext_external(self, ext, size, reference):
        """Explicitly just moves ext->ext data"""
        if isinstance(ext, (bytes, bytearray)):
            self.external[self.ext_offset : self.ext_offset + size] = ext
        else:
            self.external.move(ext, self.ext_offset, size=size)

        if reference is not None:
            self.internal.lookup(reference)

        new_loc = ext + self.ext_offset

        return new_loc

    def move_ext(self, ext, size, reference):
        """Attempt to relocate in priority order:
        1. Internal
        2. External

        This is the primary moving function for data that is already compressed
        or is incompressible.
        """
        try:
            new_loc = self.move_to_int(ext, size, reference)
            if isinstance(ext, int):
                self.ext_offset -= round_down_word(size)
            return new_loc
        except NotEnoughSpaceError:
            print(
                f"        {Fore.RED}Not Enough Internal space. Using external flash{Style.RESET_ALL}"
            )
            return self.move_ext_external(ext, size, reference)

    def move_to_compressed_memory(self, ext, size, reference):
        """Attempt to relocate in priority order:
        1. compressed_memory
        2. Internal
        3. External

        This is the primary moving method for any compressible data.
        """
        current_len = self.compressed_memory_compressed_len()

        try:
            self.compressed_memory[
                self.compressed_memory_pos : self.compressed_memory_pos + size
            ] = self.external[ext : ext + size]
        except NotEnoughSpaceError:
            print(
                f"        {Fore.RED}compressed_memory full. Attempting to put in internal{Style.RESET_ALL}"
            )
            return self.move_ext(ext, size, reference)

        new_len = self.compressed_memory_compressed_len(size)
        diff = new_len - current_len
        compression_ratio = size / diff

        print(
            f"    {Fore.YELLOW}compression_ratio: {compression_ratio}{Style.RESET_ALL}"
        )

        if diff > self.int_free_space:
            print(
                f"        {Fore.RED}not putting into free memory due not enough free "
                f"internal storage for compressed data.{Style.RESET_ALL}"
            )
            self.compressed_memory.clear_range(
                self.compressed_memory_pos, self.compressed_memory_pos + size
            )
            return self.move_ext_external(ext, size, reference)
        elif compression_ratio < self.args.compression_ratio:
            # Revert putting this data into compressed_memory due to poor space_savings
            print(
                f"        {Fore.RED}not putting in free memory due to poor compression.{Style.RESET_ALL}"
            )
            self.compressed_memory.clear_range(
                self.compressed_memory_pos, self.compressed_memory_pos + size
            )
            return self.move_ext(ext, size, reference)
        # Even though the data is already moved, this builds the reference lookup
        self._move_to_compressed_memory(ext, self.compressed_memory_pos, size=size)

        print(
            f"    move_to_compressed_memory {hex(ext)} -> {hex(self.compressed_memory_pos)}"
        )
        if reference is not None:
            self.internal.lookup(reference)
        new_loc = self.compressed_memory_pos
        self.compressed_memory_pos += round_up_word(size)
        self.ext_offset -= round_down_word(size)

        return new_loc

    def __call__(self):
        from . import MarioGnW, ZeldaGnW

        self.int_pos = self.internal.empty_offset
        out = self.patch()
        is_mario, is_zelda = False, False
        if isinstance(self, MarioGnW):
            is_mario = True
        elif isinstance(self, ZeldaGnW):
            is_zelda = True
        metadata = HeaderMetaData(
            external_flash_size=len(self.external),
            is_mario=is_mario,
            is_zelda=is_zelda,
        )
        # hdmi-cec = 0x01B8; not used in the gnw hardware.
        self.internal.replace(0x01B8, metadata.pack())
        return out

    def patch(self):
        """Device specific argument parsing and patching routine.
        Called from __call__; not to be called otherwise.
        """
        raise NotImplementedError
