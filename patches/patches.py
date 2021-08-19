
class Patch:
    def __init__(self, offset, data, size=None, message=None):
        self.offset = offset
        self.data = data
        self.size = size
        self.message = message

class Patches(list):
    def append(self, *args, **kwargs):
        super().append(Patch(*args, **kwargs))

def parse_patches(args):
    patches = Patches()

    patches.append(0x4, "bootloader",
                   message="Invoke custom bootloader prior to calling stock Reset_Handler")
    patches.append(0x6b52, "read_buttons",
                   message="Intercept button presses for macros")

    return patches
