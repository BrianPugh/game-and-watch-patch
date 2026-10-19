"""Download Sylverb's SD bootloader (--sd-bootloader flashes it at 0x08032000).

github.com/sylverb/game-and-watch-bootloader (GPL-2.0), so it's fetched at
build time and never committed. The release is pinned and checked by SHA256.
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

VERSION = "v1.0.8"
ASSET = "gnw_bootloader_0x08032000.bin"
SHA256 = "43fd718c1a53dda140f29e4025fc8b6d3bfa71f19a710139135f998e4de022d7"
URL = f"https://github.com/sylverb/game-and-watch-bootloader/releases/download/{VERSION}/{ASSET}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with urllib.request.urlopen(URL) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        sys.exit(f"{URL}: SHA256 {digest}, expected {SHA256}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)


if __name__ == "__main__":
    main()
