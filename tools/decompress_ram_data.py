#!/usr/bin/env python3
""" Note: this data is already dumped and decoded by game-and-watch-backup.

So you should never really need to use this script.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from patches import lz77_decompress  # noqa E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    data = args.src.read_bytes()
    decompressed = lz77_decompress(data)
    args.dst.write_bytes(decompressed)


if __name__ == "__main__":
    main()
