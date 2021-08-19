COMMAND_DESCRIPTIONS = {
    "replace": "simple byte substitution",
    "bl": "replace bl",
}
VALID_COMMANDS = set(list(COMMAND_DESCRIPTIONS.keys()))

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


class Patches(list):
    def append(self, *args, **kwargs):
        super().append(Patch(*args, **kwargs))

def parse_patches(args):
    patches = Patches()

    patches.append("replace", 0x4, "bootloader",
                   message="Invoke custom bootloader prior to calling stock Reset_Handler")
    #patches.append(0x6b52, "read_buttons",
    #               message="Intercept button presses for macros")

    return patches
