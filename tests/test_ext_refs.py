"""Device.fix_ext_refs() on a synthetic device and scan (no stock firmware)."""

import pytest

import patches.xrefs
from patches.firmware import Device, Firmware, Lookup


class Int(Firmware):
    FLASH_BASE = 0x0800_0000
    FLASH_LEN = 0x100
    STOCK_ROM_END = 0x80


class Ext(Firmware):
    FLASH_BASE = 0x9000_0000
    FLASH_LEN = 0x100


class Sram3(Firmware):
    FLASH_BASE = 0x240F_2124
    FLASH_LEN = 0x100


class RWData:
    def __init__(self):
        self.datas = [bytearray(0x20)]
        self.dsts = [0x2000_0800]


def make_device(refs, monkeypatch, extra=()):
    """A device whose scan finds ``refs`` ({src: target, ...}, kind from src)
    and the ``extra`` ref dicts."""
    device = Device.__new__(Device)
    device.internal = Int()
    device.internal.rwdata = RWData()
    device.external = Ext()
    device.compressed_memory = Sram3()
    device.lookup = Lookup()
    for fw in (device.internal, device.external, device.compressed_memory):
        fw._lookup = device.lookup
    device.int_freed = []
    device.expected_refs = []
    device.ext_refs_fixed = False
    device._stock_map = object()

    def kind(src):
        if src >= 0x9000_0000:
            return "external pointer table"
        return "DTCM .data" if src >= 0x2000_0000 else "literal"

    scan = [{"kind": kind(s), "src": s, "target": t} for s, t in refs.items()]
    scan += extra
    monkeypatch.setattr(patches.xrefs, "ext_refs", lambda _: scan)
    return device


def word(buf, off):
    return int.from_bytes(buf[off : off + 4], "little")


def put(buf, off, value):
    buf[off : off + 4] = value.to_bytes(4, "little")


def test_relocates_internal_rwdata_and_moved_external_sites(monkeypatch):
    device = make_device(
        {0x0800_0010: 0x9000_0040, 0x2000_0804: 0x9000_0041, 0x9000_0020: 0x9000_0040},
        monkeypatch,
    )
    put(device.internal, 0x10, 0x9000_0040)
    put(device.internal.rwdata.datas[0], 4, 0x9000_0041)
    put(device.external, 0x20, 0x9000_0040)
    # The data moves to SRAM3, and so does the external table that points at it.
    device._move(device.compressed_memory, 0x10, device.external, 0x40, 8)
    device._move(device.compressed_memory, 0x30, device.external, 0x20, 4)

    counts = device.fix_ext_refs()

    assert counts == {"literal": 1, "DTCM .data": 1, "external pointer table": 1}
    assert word(device.internal, 0x10) == 0x240F_2134
    assert word(device.internal.rwdata.datas[0], 4) == 0x240F_2135
    assert word(device.compressed_memory, 0x30) == 0x240F_2134
    assert device.ext_refs_fixed


def test_leaves_unmoved_erased_and_freed_sites(monkeypatch):
    device = make_device(
        {0x0800_0010: 0x9000_0080, 0x0800_0014: 0x9000_0040, 0x0800_0018: 0x9000_0040},
        monkeypatch,
    )
    put(device.internal, 0x10, 0x9000_0080)  # data that doesn't move
    put(device.internal, 0x14, 0)  # a patch erased this reference
    put(device.internal, 0x18, 0x9000_0040)
    device.int_freed.append((0x18, 0x1C))  # stock code the patcher removed
    device._move(device.compressed_memory, 0, device.external, 0x40, 4)

    assert device.fix_ext_refs() == {}
    assert word(device.internal, 0x10) == 0x9000_0080
    assert word(device.internal, 0x18) == 0x9000_0040


def test_changed_site_raises(monkeypatch):
    device = make_device({0x0800_0010: 0x9000_0040}, monkeypatch)
    put(device.internal, 0x10, 0x9000_0044)  # a patch rewrote it
    device._move(device.compressed_memory, 0, device.external, 0x40, 8)
    with pytest.raises(ValueError, match="can't be relocated"):
        device.fix_ext_refs()


def test_verify_word_into_moved_data_raises(monkeypatch):
    verify = {"kind": "external data (verify)", "src": 0x9000_0020}
    device = make_device({}, monkeypatch, [{**verify, "target": 0x9000_0040}])
    device._move(device.compressed_memory, 0, device.external, 0x40, 4)
    with pytest.raises(ValueError, match="classify it"):
        device.fix_ext_refs()


def test_move_reference_must_be_scanned(monkeypatch):
    device = make_device({0x0800_0010: 0x9000_0040}, monkeypatch)
    put(device.internal, 0x10, 0x9000_0040)
    put(device.internal, 0x20, 0x9000_0040)
    device._move(device.compressed_memory, 0, device.external, 0x40, 4)
    device._expect_refs([0x10], 0x40, 4)
    device._expect_refs(0x20, 0x40, 4)  # the scan doesn't know this one
    with pytest.raises(ValueError, match="0x08000020"):
        device.fix_ext_refs()


def test_move_reference_must_point_into_the_moved_range(monkeypatch):
    device = make_device({0x0800_0010: 0x9000_0040}, monkeypatch)
    put(device.internal, 0x10, 0x9000_0040)
    device._move(device.compressed_memory, 0, device.external, 0x80, 4)
    device._expect_refs(0x10, 0x80, 4)
    with pytest.raises(ValueError, match="isn't a reference"):
        device.fix_ext_refs()


def test_reference_in_our_code_is_relocated(monkeypatch):
    device = make_device({}, monkeypatch)
    put(device.internal, 0x90, 0x9000_0040)  # past STOCK_ROM_END
    device._move(device.compressed_memory, 0, device.external, 0x40, 4)
    device._expect_refs(0x90, 0x40, 4)
    assert device.fix_ext_refs() == {"our pointer": 1}
    assert word(device.internal, 0x90) == 0x240F_2124
