
class Patch:
    def __init__(self, offset, data, size=None, message=None):
        self.offset = offset
        self.data = data
        self.size = size
        self.message = message

class Patches(list):
    def append(self, *args, **kwargs):
        super().append(Patch(*args, **kwargs))

def parse_patches(arg):
    patches = Patches()

    patches.append(0x4, "bootloader", message="Invoke custom bootloader prior to calling stock Reset_Handler")

    return patches
