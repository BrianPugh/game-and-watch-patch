"""Sylverb's SD bootloader, which ``--sd-bootloader`` builds expect at
``ADDRESS``.

github.com/sylverb/game-and-watch-bootloader (GPL-2.0), so it's downloaded
when it's flashed and never committed. The release is pinned and checked by
SHA256 (``make flash`` and gnwmanager both use this pin).
"""

import hashlib
import urllib.request

ADDRESS = 0x0803_2000
VERSION = "v1.0.8"
ASSET = "gnw_bootloader_0x08032000.bin"
SHA256 = "43fd718c1a53dda140f29e4025fc8b6d3bfa71f19a710139135f998e4de022d7"
URL = f"https://github.com/sylverb/game-and-watch-bootloader/releases/download/{VERSION}/{ASSET}"


class ChecksumError(Exception):
    pass


def check(data):
    """Raises ``ChecksumError`` unless ``data`` is the pinned release."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        raise ChecksumError(f"{URL}: SHA256 {digest}, expected {SHA256}")


def fetch():
    """The pinned release's bytes, downloaded and checked."""
    with urllib.request.urlopen(URL) as response:
        data = response.read()
    check(data)
    return data
