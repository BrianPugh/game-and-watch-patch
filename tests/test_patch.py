import zlib

import pytest

from patches.exception import UnexpectedBytesError
from patches.patch import FirmwarePatchMixin


class Image(FirmwarePatchMixin, bytearray):
    FLASH_BASE = 0x0800_0000


def _expect(data):
    return f"{len(data)}:{zlib.crc32(data):08x}"


def test_expect_match():
    image = Image(b"\x01\x02\x03\x04\x00\x00")
    image.nop(0, 2, expect=_expect(b"\x01\x02\x03\x04"))
    assert image[:4] == b"\x00\xbf\x00\xbf"


def test_expect_mismatch_leaves_image_unchanged():
    image = Image(b"\x01\x02\x03\x04")
    found = _expect(b"\x01\x02\x03\x04")
    with pytest.raises(UnexpectedBytesError, match=f'found "{found}"'):
        image.bl(0, 0x100, expect="4:0")
    assert image == b"\x01\x02\x03\x04"


def test_expect_omitted():
    image = Image(4)
    image.replace(0, 0x12345678, size=4)
    assert image == bytes.fromhex("78563412")
