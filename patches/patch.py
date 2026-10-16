import zlib

from .compression import lzma_compress
from .exception import UnexpectedBytesError
from .thumb_asm import assemble as thumb_assemble


def twos_compliment(value, bits):
    if value >= 0:
        return value
    else:
        # Two's Compliment
        return (1 << bits) + value


class FirmwarePatchMixin:
    """Patch commands that apply to a single firmware instance.

    Every command takes an optional ``expect``, ``"<length>:<crc32 hex>"`` of
    the bytes that must be at ``offset`` before it writes. It catches a patch
    aimed at the wrong offset, or at code an earlier patch changed. It's a hash
    so no stock firmware bytes end up in the repo; for a new patch, pass e.g.
    ``"4:0"`` and copy the found value from the error.
    """

    def _expect(self, offset: int, expect):
        if expect is None:
            return
        length, crc = expect.split(":")
        found = bytes(self[offset : offset + int(length)])
        found_crc = zlib.crc32(found)
        if found_crc != int(crc, 16):
            raise UnexpectedBytesError(
                f'0x{offset:X}: expected "{expect}", found "{length}:{found_crc:08x}" '
                f"({found.hex()})"
            )

    def replace(self, offset: int, data, size=None, expect=None) -> int:
        """ """
        self._expect(offset, expect)

        if offset >= len(self):
            raise IndexError(
                f"Patch offset {offset} exceeds firmware length {len(self)}"
            )

        n_bytes_patched = 0

        if isinstance(data, bytes):
            # Write the bytes at that address as is.
            self[offset : offset + len(data)] = data
            n_bytes_patched = len(data)
        elif isinstance(data, str):
            if size:
                raise ValueError("Don't specify size when providing a symbol name.")
            data = self.address(data)
            self[offset : offset + 4] = data.to_bytes(4, "little")
            n_bytes_patched = 4
        elif isinstance(data, int):
            # must be 1, 2, or 4 bytes
            if size is None:
                raise ValueError('Must specify "size" when providing int data')
            if size not in (1, 2, 4):
                raise ValueError(f"Size must be one of {1, 2, 4}; got {size}")
            self[offset : offset + size] = data.to_bytes(size, "little")
            n_bytes_patched = size
        else:
            raise ValueError(f'Don\'t know how to parse data type "{data}"')

        return n_bytes_patched

    def relative(self, offset, data, size=None, expect=None) -> int:
        """
        data
            If str, looks up a function
        """
        self._expect(offset, expect)
        src = self.FLASH_BASE + offset

        if isinstance(data, str):
            if size:
                raise ValueError("Don't specify size when providing a symbol name.")
            dst = self.address(data)
        elif isinstance(data, int):
            # must be 1, 2, or 4 bytes
            if size is None:
                raise ValueError('Must specify "size" when providing int data.')
            if data < self.FLASH_BASE:
                data += self.FLASH_BASE
            dst = data
        rel_distance = dst - src
        if rel_distance < 0:
            rel_distance += 0x1_0000_0000

        print(f"Computed relative distance 0x{rel_distance:08X}")

        return self.replace(offset, rel_distance, size=4)

    def b(self, offset: int, data: int, expect=None) -> int:
        """Unconditional branch inserted at ``offset`` to offset ``data``

        data should be the absolute offset into the firmware (i.e. NOT in the
        form 0x08XX_XXXX or 0x9XXX_XXXX)

        2 byte command
        """
        self._expect(offset, expect)

        pc = offset + 4
        jump = data - pc

        if abs(jump) > (2 * (1 << 10)):
            # Max +-2KB jump
            raise ValueError(f"Too large of a jump {jump} specified.")

        jump >>= 1
        jump = twos_compliment(jump, 11)

        byte_0 = 0b1110_0000 | ((jump >> 8) & 0x7)
        byte_1 = jump & 0xFF

        self[offset + 0] = byte_1
        self[offset + 1] = byte_0

        return 2

    def bl(self, offset: int, data, expect=None) -> int:
        """Replace a branch-link statement to a branch to one of our functions

        4 byte command.
        """
        self._expect(offset, expect)

        if isinstance(data, str):
            dst_address = self.address(data)
        else:
            dst_address = self.FLASH_BASE + data

        pc = self.FLASH_BASE + offset + 4

        jump = dst_address - pc

        if abs(jump) > (4 * (1 << 20)):
            # Max +-4MB jump
            raise ValueError(f"Too large of a jump {jump} specified.")

        # Where H=0
        offset_stage_1 = jump >> 12
        offset_stage_1 = twos_compliment(offset_stage_1, 11)

        stage_1_byte_0 = 0b1111_0000 | ((offset_stage_1 >> 8) & 0x7)
        stage_1_byte_1 = offset_stage_1 & 0xFF

        # Where H=1
        offset_stage_2 = (jump - (offset_stage_1 << 12)) >> 1
        if offset_stage_2 >> 11:
            raise ValueError(f"bl jump 0x{jump:08X} too large!")

        stage_2_byte_0 = 0b1111_1000 | ((offset_stage_2 >> 8) & 0x7)
        stage_2_byte_1 = offset_stage_2 & 0xFF

        # Store the instructions in little endian order
        self[offset + 0] = stage_1_byte_1
        self[offset + 1] = stage_1_byte_0
        self[offset + 2] = stage_2_byte_1
        self[offset + 3] = stage_2_byte_0

        return 4

    def bkpt(self, offset, size=2, expect=None):
        """Insert software breakpoint(s)"""
        self._expect(offset, expect)
        if size % 2:
            raise ValueError("Size of breakpoints must be even.")

        n_bkpts = size // 2
        self[offset : offset + size] = b"\x00\xbe" * n_bkpts
        return size

    def asm(self, offset: int, data: str, size=None, expect=None) -> int:
        """
        Parameters
        ----------
        data : str
            Assembly instructions
        """
        self._expect(offset, expect)
        data = data.strip()
        if data.startswith("b.w"):
            encoding = thumb_assemble(data, self.FLASH_BASE + offset)
        else:
            encoding = thumb_assemble(data)
        print(f'    "{data}" -> {[hex(x) for x in encoding]}')
        if size:
            assert len(encoding) == size
        for i, val in enumerate(encoding):
            self[offset + i] = val
        return len(encoding)

    def nop(self, offset: int, data: int, expect=None) -> int:
        """Insert N NOP operations (each 2 bytes long)."""
        self._expect(offset, expect)
        size = data * 2
        self[offset : offset + size] = b"\x00\xbf" * data
        return size

    def _move_copy(self, offset: int, data: int, size: int, delete: bool) -> int:
        """Move from offset -> data"""

        if not isinstance(data, int):
            raise ValueError(f"Data must be int, got {type(data)}")

        old_start = offset
        old_end = old_start + size
        new_start = offset + data
        new_end = new_start + size
        print(f"    moving {size} bytes from 0x{old_start:08X} to 0x{new_start:08X}")
        self[new_start:new_end] = self[old_start:old_end]

        # Erase old copy
        if delete:
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

        for i in range(size):
            self._lookup[self.FLASH_BASE + old_start + i] = (
                self.FLASH_BASE + new_start + i
            )

        return size

    def move(self, offset: int, data: int, size: int) -> int:
        return self._move_copy(offset, data, size, True)

    def copy(self, offset: int, data: int, size: int) -> int:
        return self._move_copy(offset, data, size, False)

    def add(self, offset: int, data: int, size: int = 4, expect=None) -> int:
        self._expect(offset, expect)
        val = self.int(offset, size)
        val += data
        self[offset : offset + size] = val.to_bytes(size, "little")

        return size

    def shorten(self, data: int) -> int:
        data = abs(data)
        if data == 0:
            return

        self.ENC_END -= data
        if self.ENC_END < self.ENC_START:
            self.ENC_END = self.ENC_START
        if len(self) == data:
            self[:] = bytearray()
        else:
            self[:] = self[:-data]

        return data

    def compress(self, offset: int, size: int) -> int:
        """Apply in-place LZMA compression."""
        data = self[offset : offset + size]

        compressed_data = lzma_compress(data)

        # Clear the original data
        self.clear_range(offset, offset + size)
        # Insert the compressed data
        self[offset : offset + len(compressed_data)] = compressed_data

        print(
            f"    compressed {len(data)}->{len(compressed_data)} bytes (saves {len(data)-len(compressed_data)})"
        )

        return len(compressed_data)

    def lookup(self, offsets):
        size = 4

        if not isinstance(offsets, list):
            offsets = [offsets]

        for offset in offsets:
            val = self.int(offset, size)
            try:
                new_val = self._lookup[val]
            except KeyError:
                raise KeyError(f"0x{val:08X} at offset 0x{offset:08X}")
            self[offset : offset + size] = new_val.to_bytes(size, "little")
            # print(f"    lookup 0x{val:08X}->0x{new_val:08x}")
