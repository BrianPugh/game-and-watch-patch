"""
Responsibilities of this script:
"""

from pathlib import Path
import argparse
import hashlib

STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"

class InvalidStockRomError(Exception):
    """The provided stock ROM did not contain the expected data."""

def parse_args():
    parser = argparse.ArgumentParser(description="Game and Watch Firmware Patcher.")
    parser.add_argument('--firmware', type=Path, default="internal_flash_backup.bin",
                        help="Input stock firmware.")
    return parser.parse_args()

def verify_stock_firmware(data):
    h = hashlib.sha1(data).hexdigest()
    if h != STOCK_ROM_SHA1_HASH:
        raise InvalidStockRomError

def main():
    args = parse_args()
    firmware = args.firmware.read_bytes()
    verify_stock_firmware(firmware)

    # TODO

if __name__ == "__main__":
    main()
