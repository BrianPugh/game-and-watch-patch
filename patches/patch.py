from keystone import *

COMMAND_DESCRIPTIONS = {
    "replace": "simple byte substitution",
    "bl": "replace bl",
    "ks_arm": "compile a snippet of assembly into ARM",
    "ks_thumb": "compile a snippet of assembly into Thumb",
}
VALID_COMMANDS = set(list(COMMAND_DESCRIPTIONS.keys()))

ks_arm = Ks(KS_ARCH_ARM, KS_MODE_ARM)
ks_thumb = Ks(KS_ARCH_ARM, KS_MODE_THUMB)

class Patch:
    def __init__(self, command, offset, data, size=None, message=None):
        if command not in VALID_COMMANDS:
            raise ValueError(f"Invalid command \"{command}\"")
        self.command = command
        self.offset = offset
        self.data = data
        self.size = size
        self.message = message

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
            raise IndexError(f"Patch offset {self.offset} exceeds firmware length {len(self)}")

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

        print(f"        \"{self.data}\" -> {encoding}")

        assert len(encoding) == self.size

        for i, val in enumerate(encoding):
            firmware[self.offset + i] = val

        return len(encoding)

    def ks_arm(self, firmware):
        return self._ks(ks_arm, firmware)

    def ks_thumb(self, firmware):
        return self._ks(ks_thumb, firmware)


class Patches(list):
    def append(self, *args, **kwargs):
        super().append(Patch(*args, **kwargs))


