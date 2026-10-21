"""Download Sylverb's SD bootloader (--sd-bootloader flashes it at 0x08032000).

The release is pinned in ``patches/sd_bootloader.py``.
"""

import argparse
import sys
from pathlib import Path

from patches import sd_bootloader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    try:
        data = sd_bootloader.fetch()
    except sd_bootloader.ChecksumError as e:
        sys.exit(str(e))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)


if __name__ == "__main__":
    main()
