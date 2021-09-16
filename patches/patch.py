import struct
from .compression import lzma_compress



class FirmwarePatchMixin:
    """ Patch commands that apply to a single firmware instance.
    """

    def replace(self, offset : int, data, size=None) -> int:
        """
        """

        if offset >= len(self):
            raise IndexError(f"Patch offset {offset} exceeds firmware length {len(self)}")

        if offset >= self.STOCK_ROM_END:
            raise IndexError(f"Patch offset {offset} exceeds stock firmware region {self.STOCK_ROM_END}")

        n_bytes_patched = 0

        if isinstance(data, bytes):
            # Write the bytes at that address as is.
            self[offset:offset + len(data)] = data
            n_bytes_patched = len(data)
        elif isinstance(data, str):
            if size:
                raise ValueError("Don't specify size when providing a symbol name.")
            data = self.address(data)
            self[offset:offset+4] = data.to_bytes(4, 'little')
        elif isinstance(data, int):
            # must be 1, 2, or 4 bytes
            if size is None:
                raise ValueError("Must specify \"size\" when providing int data")
            if size not in (1, 2, 4):
                raise ValueError(f"Size must be one of {1, 2, 4}; got {size}")
            self[offset:offset+size] = data.to_bytes(size, 'little')
        else:
            raise ValueError(f"Don't know how to parse data type \"{data}\"")

        return n_bytes_patched

    def relative(self, offset, data, size=None):
        """
        data
            If str, looks up a function
        """
        src = self.FLASH_BASE + offset

        if isinstance(data, str):
            if size:
                raise ValueError("Don't specify size when providing a symbol name.")
            dst = self.address(data)
        elif isinstance(data, int):
            # must be 1, 2, or 4 bytes
            if size is None:
                raise ValueError("Must specify \"size\" when providing int data.")
            if data < self.FLASH_BASE:
                raise ValueError("Data {hex(data)} below FLASH_BASE {hex(self.FLASH_BASE)}.")
            dst = self.int(self, offset, size=size)
        rel_distance = dst - src
        import ipdb; ipdb.set_trace()
        print("meow")


    def bl(self, offset : int, data : str) -> int:
        """ Replace a branching statement to a branch to one of our functions
        """

        dst_address = self.address(data)

        pc = self.FLASH_BASE + offset + 4

        jump = dst_address - pc

        if jump <= 0:
            raise NotImplementedError("negative jump")

        offset_stage_1 = jump >> 12
        if offset_stage_1 >> 11:
            raise ValueError(f"bl jump 0x{jump:08X} too large!")

        stage_1_byte_0 = 0b11110000 | ((offset_stage_1 >> 8) & 0x7)
        stage_1_byte_1 = offset_stage_1 & 0xFF

        offset_stage_2 = (jump - (offset_stage_1 << 12)) >> 1
        if offset_stage_2 >> 11:
            raise ValueError(f"bl jump 0x{jump:08X} too large!")

        stage_2_byte_0 = 0b11111000 | ((offset_stage_2 >> 8) & 0x7)
        stage_2_byte_1 = offset_stage_2 & 0xFF

        # Store the instructions in little endian order
        self[offset + 0] = stage_1_byte_1
        self[offset + 1] = stage_1_byte_0
        self[offset + 2] = stage_2_byte_1
        self[offset + 3] = stage_2_byte_0

        return 4

    @property
    def _ks(self):
        try:
            return self._ks_inst
        except AttributeError:
            from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB
            self._ks_inst = Ks(KS_ARCH_ARM, KS_MODE_THUMB)
        return self._ks_inst

    def asm(self, offset : int, data : str, size=None) -> int:
        """
        Parameters
        ----------
        data : str
            Assembly instructions
        """
        encoding, _ = self._ks.asm(data)
        print(f"    \"{data}\" -> {[hex(x) for x in encoding]}")
        if size:
            assert len(encoding) == size
        for i, val in enumerate(encoding):
            self[offset + i] = val
        return len(encoding)

    def nop(self, offset : int, data : int) -> int:
        """Insert N NOP operations (each 2 bytes long)."""
        size = data * 2
        self[offset:offset+size] = b"\x00\xbf" * data
        return size

    def move(self, offset : int, data : int, size : int) -> int:
        """ Move from offset -> data """
        old_start = offset
        old_end = old_start + size
        new_start = offset + data
        new_end = new_start + size
        print(f"    moving {size} bytes from 0x{old_start:08X} to 0x{new_start:08X}")
        self[new_start:new_end] = self[old_start:old_end]

        # Erase old copy
        if data < 0:
            if new_end > offset:
                self.clear_range(new_end, old_end)
            else:
                self.clear_range(old_start, old_end)
        else:
            if new_start < old_end:
                self.clear_range(old_start, new_start)
            else:
                self.clear_range(old_start, old_end)

        return size

    def copy(self, offset : int, data : int, size : int):
        """ Copy offset -> data """

        if not isinstance(self.data, int):
            raise ValueError(f"Data must be int, got {type(self.data)}")

        old_start = self.offset
        old_end = old_start + size
        new_start = self.offset + self.data
        new_end = new_start + size
        self[new_start:new_end] = self[old_start:old_end]

        return self.size

    def add(self, offset : int, data : int, size : int) -> int:
        val = self.int(self, offset, size=size)
        val += data
        self[offset:offset+size] = val.to_bytes(size, "little")

        return size

    def shorten(self, data : int) -> int:
        data = abs(data)
        if self.data == 0:
            return

        self.ENC_LEN -= data
        if self.ENC_LEN < 0:
            self.ENC_LEN = 0
        self[:] = self[:-data]

        return data

    def compress(self, offset : int, data : int) -> int:
        """ Apply in-place LZMA compression. """
        data = self[offset:offset+data]

        compressed_data = lzma_compress(data)

        # Clear the original data
        self[offset:offset+data] = b"\x00" * data
        # Insert the compressed data
        self[offset:offset+len(compressed_data)] = compressed_data

        print(f"    compressed {len(data)}->{len(compressed_data)} bytes")

        return len(compressed_data)

    # TODO: delete these once they're moved to their appropriate places

    #def lookup(self, firmware):
    #    if not isinstance(self.data, dict):
    #        raise ValueError(f"Data must be dict, got {type(self.data)}")
    #    if self.size is None:
    #        raise ValueError("Size must not be none")

    #    val = _addr(firmware, self.offset, size=self.size)
    #    try:
    #        new_val = self.data[val]
    #    except KeyError:
    #        if self.cond(val):
    #            # This was an expected miss
    #            return
    #        else:
    #            raise KeyError(hex(val))
    #        print(f"MISSING: {hex(val)}")
    #        return

    #    firmware[self.offset:self.offset+self.size] = new_val.to_bytes(self.size, "little")


class DevicePatchMixin:
    def move(self, dst, dst_offset : int, src, src_offset : int, size : int) -> int:
        dst[dst_offset:dst_offset+size] = src[src_offset:src_offset+size]
        src.clear_range(src_offset, src_offset + size)
        return size

    def copy(self, dst, dst_offset : int, src, src_offset : int, size : int) -> int:
        dst[dst_offset:dst_offset+size] = src[src_offset:src_offset+size]
        return size
