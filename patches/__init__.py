import patches.ips

from .compression import lz77_decompress, lzma_compress
from .firmware import Device, ExtFirmware, Firmware, IntFirmware
from .mario import MarioGnW
from .zelda import ZeldaGnW
