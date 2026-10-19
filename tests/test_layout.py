import pytest

from patches.exception import NotEnoughSpaceError
from patches.firmware import Device
from patches.layout import Layout, Region


def test_free_int_fills_udf_and_word_aligns():
    device = Device.__new__(Device)
    device.name = "mario"
    device.internal = bytearray(0x1000)
    device.int_space = Region("internal", 0x0800_0000, 0x1000)
    device.freed_int = []
    device.relocation = None
    device.free_int(0x102, 0x20A)
    assert device.int_space.holes == [[0x104, 0x208]]
    assert device.freed_int == [(0x104, 0x208)]
    assert device.internal[0x104:0x208] == b"\xfe\xde" * ((0x208 - 0x104) // 2)
    assert device.internal[0x100:0x104] == b"\x00" * 4
    assert device.int_space.hole_space == 0x104
    assert device.is_freed_int(0x104) and not device.is_freed_int(0x208)


def test_alloc_takes_the_smallest_hole_that_fits():
    region = Region("internal", 0, 0x1000, pos=0x800)
    region.free(0x100, 0x200)  # 256 bytes
    region.free(0x400, 0x440)  # 64 bytes
    assert region.alloc(50, "mario") == 0x400  # Smallest hole that fits; uses 52.
    assert region.alloc(10, "mario") == 0x434  # Rounds to 12, exactly what's left.
    assert region.alloc(13, "mario") == 0x100  # Small hole is now empty.
    assert region.holes == [[0x110, 0x200], [0x440, 0x440]]
    assert region.alloc(0x200, "mario", "big") == 0x800  # No hole fits: the cursor.
    assert region.pos == 0xA00
    assert [a.label for a in region.allocations] == ["", "", "", "big"]


def test_alloc_leaves_room_for_pending_data_and_slack():
    region = Region("internal", 0, 0x100, pos=0x80)
    region.pending.append(lambda: 0x40)
    region.slack = 0x10
    assert region.free_space == 0x30
    with pytest.raises(NotEnoughSpaceError):
        region.alloc(0x31, "mario")
    assert region.alloc(0x30, "mario") == 0x80


def test_append_and_reserve():
    region = Region("bank2", 0x0810_0000, 0x100)
    assert region.append(7, "zelda", "rwdata") == 0  # unaligned, at the cursor
    assert region.pos == 7
    assert region.reserve(0x40, 0x20, "mario", "stock code") == 0x40
    with pytest.raises(ValueError):
        region.reserve(0x50, 0x20, "zelda")
    with pytest.raises(NotEnoughSpaceError):
        region.reserve(0xF0, 0x20, "zelda")


def test_layout_shares_bank2_and_maps_every_region():
    class Fake:
        name = "mario"

        pending = []
        ext_placed = []

        def make_regions(self, int_space=None):
            self.int_space = Region("mario internal", 0x0800_0000, 0x100, pos=0x80)
            return [self.int_space, Region("mario sram3", 0x240F_2124, 0)]

        def __call__(self):
            self.int_space.alloc(6, self.name, "ext 0x000010")
            self.int_space.free(0x10, 0x20)
            self.bank2_space.reserve(0x20000, 0x40, self.name, "stock code")

    layout = Layout()
    device = Fake()
    layout.add(device)
    assert device.bank2 is layout.bank2
    layout.patch()
    assert layout.map_lines() == [
        "bank2 0x08100000-0x08200000: 64 bytes used, 0 in holes",
        "  0x08120000        64  mario  stock code (reserved)",
        "mario internal 0x08000000-0x08000100: 120 bytes used, 16 in holes",
        "  0x08000010        16         free",
        "  0x08000080         6  mario  ext 0x000010",
    ]


def test_the_cursor_stops_at_a_reserved_range():
    region = Region("internal", 0x0800_0000, 0x400, pos=0x100)
    region.reserve(0x200, 0x100, "zelda", "relocated stock code")
    assert region.limit == 0x200 and region.free_space == 0x100
    with pytest.raises(NotEnoughSpaceError):
        region.alloc(0x104, "zelda")
    assert region.alloc(0x100, "zelda") == 0x100
    assert region.used == 0x300  # up to the cursor (0x200) + the reserved 0x100


def test_free_int_of_code_relocated_in_the_image_frees_the_new_place():
    device = Device.__new__(Device)
    device.name = "mario"
    device.internal = bytearray(0x1000)
    device.int_space = Region("internal", 0x0800_0000, 0x1000)
    device.freed_int = []
    device.relocation = type("Reloc", (), {"int_delta": lambda self, off: 0x800})()
    device.free_int(0x100, 0x110)
    assert device.int_space.holes == [[0x900, 0x910]]
    assert device.is_freed_int(0x100) and not device.is_freed_int(0x900)
    assert (
        device.internal[0x100:0x110] == device.internal[0x900:0x910] == b"\xfe\xde" * 8
    )


def test_free_clips_other_owners():
    region = Region("bank1", 0x0800_0000, 0x1000)
    region.note(0, 0x100, "mario", "stock code")
    region.note(0x200, 0x100, "zelda", "stock code")
    # Zelda frees code that lies under Mario's in the image.
    region.free(0x80, 0x180, "zelda")
    assert region.holes == [[0x100, 0x180]]
    # Its own range is freed whole.
    region.free(0x200, 0x240, "zelda")
    assert region.holes[-1] == [0x200, 0x240]


def test_free_keeps_linked_code():
    region = Region("bank1", 0x0800_0000, 0x1000)
    region.linked = [(0x120, 0x140)]
    region.free(0x100, 0x200, "mario")
    assert region.holes == [[0x100, 0x120], [0x140, 0x200]]


def test_pending_in_holes_keeps_room_for_each_blob():
    region = Region("bank1", 0, 0x1000, pos=0xFF0)  # 16 bytes at the cursor
    region.pending_in_holes = True
    region.free(0x100, 0x180)  # 128 bytes
    region.free(0x400, 0x440)  # 64 bytes
    region.pending.append(lambda: [0x60, 0x40])
    assert region.pending_fits()
    # Either hole would then be too small for the blob it has to take.
    with pytest.raises(NotEnoughSpaceError):
        region.alloc(0x30, "zelda")
    # The smallest hole can't spare 16 bytes (the 0x40 blob needs all of
    # it), the larger one can.
    assert region.alloc(0x10, "zelda") == 0x100
    assert region.alloc(0x10, "zelda") == 0x110
    # Now the blobs need both holes whole; the cursor still has room.
    assert region.alloc(0x10, "zelda") == 0xFF0
    with pytest.raises(NotEnoughSpaceError):
        region.alloc(4, "zelda")


def test_pending_fits_grows_one_blob():
    region = Region("bank1", 0, 0x1000, pos=0x1000)
    region.pending_in_holes = True
    region.free(0x100, 0x140)
    sram3 = lambda: 0x20  # noqa: E731
    region.pending.append(sram3)
    assert region.pending_fits(sram3, 0x20)
    assert not region.pending_fits(sram3, 0x24)


def test_pending_fits_without_holes_is_the_cursor_room():
    region = Region("internal", 0, 0x100, pos=0x80)
    region.pending.append(lambda: [0x20, 0x10])
    region.slack = 0x10
    assert region.free_space == 0x40
    assert region.pending_fits(None, 0x40)
    assert not region.pending_fits(None, 0x41)
