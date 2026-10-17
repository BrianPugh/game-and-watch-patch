"""Device.fix_ext_refs() on a synthetic device and scan (no stock firmware)."""

import pytest

import patches.xrefs
from patches.firmware import Device, Firmware, Lookup
from patches.layout import ExtRegion, Region


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
    device.int_space = Region("internal", Int.FLASH_BASE, Int.FLASH_LEN)
    device.expected_refs = []
    device.freed_int = []
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
    device.freed_int.append((0x18, 0x1C))  # stock code the patcher removed
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


def test_lookup_ranges_latest_move_wins():
    lookup = Lookup()
    assert not lookup
    lookup.add(0x9000_0040, 0x240F_2124, 8)
    lookup.add(0x9000_0044, 0x9000_0004, 4)
    assert lookup[0x9000_0040] == 0x240F_2124
    assert lookup[0x9000_0047] == 0x9000_0007
    assert lookup.get(0x9000_0048) is None
    assert 0x9000_003F not in lookup
    with pytest.raises(KeyError):
        lookup[0x9000_0048]


def test_drop_shifts_the_rest_in_aligned_steps(monkeypatch):
    device = make_device({0x0800_0010: 0x9000_00C4}, monkeypatch)
    ext = ExtRegion("external", device.external, 0x20, "test", cursor=0x10)
    device.external[0x00:0x100] = bytes(range(0x100))
    put(device.internal, 0x10, 0x9000_00C4)

    ext.drop(0x40, 0x88)  # 0x48 bytes: the shift grows by 0x40
    ext.keep(0xD0)

    assert ext.shift == -0x40
    assert device.external[0x00:0x40] == bytes(range(0x40))
    # The 8 bytes left of the dropped range are zero padding.
    assert device.external[0x40:0x48] == b"\x00" * 8
    assert device.external[0x48:0x90] == bytes(range(0x88, 0xD0))
    assert device.external[0x90:0xD0] == b"\x00" * 0x40
    assert device.fix_ext_refs() == {"literal": 1}
    assert word(device.internal, 0x10) == 0x9000_0084


def test_pack_maps_each_block_and_shifts_the_rest(monkeypatch):
    device = make_device(
        {0x0800_0010: 0x9000_0060, 0x0800_0014: 0x9000_00C4}, monkeypatch
    )
    ext = ExtRegion("external", device.external, 0x20, "test", cursor=0x10)
    device.external[0x00:0x100] = bytes(range(0x100))
    put(device.internal, 0x10, 0x9000_0060)
    put(device.internal, 0x14, 0x9000_00C4)
    ext.drop(0x20, 0x40)  # shift 0x20 before the packed range

    # 0x50 stock bytes -> 2 blocks of 6 (word aligned to 8) and 4 bytes; the
    # shift grows by 0x40, leaving 0x10 for the 0xC packed bytes.
    ext.pack(0x40, 0x90, [(0x40, b"\xaa" * 6), (0x60, b"\xbb" * 4)])
    ext.keep(0xD0)

    assert ext.shift == -0x60
    assert device.external[0x20:0x30] == b"\xaa" * 6 + b"\x00" * 2 + b"\xbb" * 4 + (
        b"\x00" * 4
    )
    assert device.external[0x30:0x70] == bytes(range(0x90, 0xD0))
    assert device.fix_ext_refs() == {"literal": 2}
    assert word(device.internal, 0x10) == 0x9000_0028
    assert word(device.internal, 0x14) == 0x9000_0064


def test_pack_rejects_blocks_larger_than_the_range(monkeypatch):
    device = make_device({}, monkeypatch)
    ext = ExtRegion("external", device.external, 0x20, "test")
    with pytest.raises(ValueError):
        ext.pack(0x40, 0x48, [(0x40, b"\xaa" * 12)])


def test_drop_after_a_partial_drop_counts_from_the_cursor(monkeypatch):
    device = make_device({}, monkeypatch)
    ext = ExtRegion("external", device.external, 4, "test")
    ext.drop(0x00, 0x0B)  # a block moved out; its last 3 bytes stay
    ext.drop(0x00, 0x20)  # the rest of its stock range
    assert (ext.cursor, ext.shift) == (0x20, -0x20)
