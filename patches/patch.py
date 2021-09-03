from keystone import *
import zopfli


COMMAND_DESCRIPTIONS = {
    "replace": "simple byte substitution",
    "bl": "replace bl",
    "ks_arm": "compile a snippet of assembly into ARM",
    "ks_thumb": "compile a snippet of assembly into Thumb",
    "nop": "replace data at address with no-operations (NOPs)",
    "move": "move block of data. Erase old location",
    "copy": "copy block of data.",
    "add": "Perform inplace addition on data at address",
    "shorten": "Shorten the firmware by removing these last bytes.",
    "compress": "Compress data inplace with zopfli."
}
VALID_COMMANDS = set(list(COMMAND_DESCRIPTIONS.keys()))

ks_arm = Ks(KS_ARCH_ARM, KS_MODE_ARM)
ks_thumb = Ks(KS_ARCH_ARM, KS_MODE_THUMB)


def _set_range(firmware, start, end, val=b"\x00"):
    firmware[start:end] = val * (end - start)
    return end - start


def _addr(firmware, offset, size=4):
    return int.from_bytes(firmware[offset:offset+size], 'little')


class Patch:
    def __init__(self, command, offset, data, size=None, cond=None, message=None):
        if command not in VALID_COMMANDS:
            raise ValueError(f"Invalid command \"{command}\"")
        self.command = command
        self.offset = offset
        self.data = data
        self.size = size
        self.message = message

        if cond is None:
            cond = lambda x: True
        self.cond = cond

    @property
    def command_callable(self):
        return getattr(self, self.command)

    def __call__(self, firmware):
        return self.command_callable(firmware)

    def replace(self, firmware):
        """
        Parameters
        ----------
        firmware : Firmware
        """

        if not isinstance(self.offset, int):
            raise ValueError(f"offset must be an int; got {type(self.offset)}")

        if self.offset >= len(firmware):
            raise IndexError(f"Patch offset {self.offset} exceeds firmware length {len(firmware)}")

        if self.offset >= firmware.STOCK_ROM_END:
            raise IndexError(f"Patch offset {self.offset} exceeds stock firmware region {firmware.STOCK_ROM_END}")

        n_bytes_patched = 0

        if isinstance(self.data, bytes):
            # Write the bytes at that address as is.
            firmware[self.offset:self.offset + len(self.data)] = self.data
            n_bytes_patched = len(self.data)
        elif isinstance(self.data, str):
            if self.size:
                raise ValueError("Don't specify size when providing a symbol name.")
            self.data = firmware.address(self.data)
            firmware[self.offset:self.offset+4] = self.data.to_bytes(4, 'little')
        elif isinstance(self.data, int):
            # must be 1, 2, or 4 bytes
            if self.size is None:
                raise ValueError("Must specify \"size\" when providing int data")
            if self.size not in (1, 2, 4):
                raise ValueError(f"Size must be one of {1, 2, 4}; got {self.size}")
            firmware[self.offset:self.offset+self.size] = self.data.to_bytes(self.size, 'little')
        else:
            raise ValueError(f"Don't know how to parse data type \"{self.data}\"")

        return n_bytes_patched

    def bl(self, firmware):
        if not isinstance(self.data, str):
            raise ValueError(f"Data must be str, got {type(self.data)}")

        dst_address = firmware.address(self.data)

        pc = firmware.FLASH_BASE + self.offset + 4

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
        firmware[self.offset + 0] = stage_1_byte_1
        firmware[self.offset + 1] = stage_1_byte_0

        firmware[self.offset + 2] = stage_2_byte_1
        firmware[self.offset + 3] = stage_2_byte_0

        return 4

    def _ks(self, ks, firmware):
        if not isinstance(self.data, str):
            raise ValueError(f"Data must be str, got {type(self.data)}")
        encoding, _ = ks.asm(self.data)

        print(f"    \"{self.data}\" -> {[hex(x) for x in encoding]}")

        assert len(encoding) == self.size

        for i, val in enumerate(encoding):
            firmware[self.offset + i] = val

        return len(encoding)

    def ks_arm(self, firmware):
        return self._ks(ks_arm, firmware)

    def ks_thumb(self, firmware):
        return self._ks(ks_thumb, firmware)

    def nop(self, firmware):
        if self.size is not None:
            raise ValueError("Size must be none; use data field to specify number of nops")
        size = self.data * 2
        firmware[self.offset:self.offset+size] = b"\x00\xbf" * self.data
        return self.data

    def move(self, firmware):
        if not isinstance(self.data, int):
            raise ValueError(f"Data must be int, got {type(self.data)}")

        old_start = self.offset
        old_end = old_start + self.size
        new_start = self.offset + self.data
        new_end = new_start + self.size
        print(f"    moving {self.size} bytes from 0x{old_start:08X} to 0x{new_start:08X}")
        firmware[new_start:new_end] = firmware[old_start:old_end]

        # Erase old copy
        if self.data < 0:
            if new_end > self.offset:
                _set_range(firmware, new_end, old_end)
            else:
                _set_range(firmware, old_start, old_end)
        else:
            if new_start < old_end:
                _set_range(firmware, old_start, new_start)
            else:
                _set_range(firmware, old_start, old_end)

        return self.size

    def copy(self, firmware):
        if not isinstance(self.data, int):
            raise ValueError(f"Data must be int, got {type(self.data)}")

        old_start = self.offset
        old_end = old_start + self.size
        new_start = self.offset + self.data
        new_end = new_start + self.size
        firmware[new_start:new_end] = firmware[old_start:old_end]

        return self.size


    def add(self, firmware):
        if not isinstance(self.data, int):
            raise ValueError(f"Data must be int, got {type(self.data)}")
        if self.size is None:
            raise ValueError("Size must not be none")
        val = _addr(firmware, self.offset, size=self.size)

        if not self.cond(val):
            return 0

        val += self.data
        firmware[self.offset:self.offset+self.size] = val.to_bytes(self.size, "little")


    def shorten(self, firmware):
        if self.size is not None:
            raise ValueError("Size must be none; use data field to specify number of bytes to shorten by.")
        self.data = abs(self.data)

        firmware.ENC_LEN -= self.data
        if firmware.ENC_LEN < 0:
            firmware.ENC_LEN = 0
        firmware[:] = firmware[:-self.data]
        #firmware[-self.data:] = b"\x00" * self.data

    def compress(self, firmware):
        if self.size is None:
            raise ValueError("Size must not be none")
        data = firmware[self.offset:self.offset+self.data]

        #import zlib
        #c = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15, memLevel=9)
        c = zopfli.ZopfliCompressor(zopfli.ZOPFLI_FORMAT_DEFLATE)
        compressed_data = c.compress(data) + c.flush()

        # Clear the original data
        firmware[self.offset:self.offset+self.data] = b"\x00" * self.data
        # Insert the compressed data
        firmware[self.offset:self.offset+len(compressed_data)] = compressed_data

        print(f"    compressed {len(data)}->{len(compressed_data)} bytes")
        if len(compressed_data) != self.size:
            raise Exception(f"Compressed {len(data)}->{len(compressed_data)} bytes. Expected {self.size}")
        return len(compressed_data)


class Patches(list):
    def append(self, *args, **kwargs):
        super().append(Patch(*args, **kwargs))

