"""patches/amalgam.py's merge of 2 devices' internal buffers into one image."""

from types import SimpleNamespace

import pytest

from patches.amalgam import SHARED, AmalgamError, merge_internal
from patches.layout import Region

SIZE = 0x100
STOCK_END = 0x40  # Both stock images end here; our code follows.
RELOCATE_END = 0x38  # The relocated device's stock code ends here.


class Buf(bytearray):
    STOCK_ROM_END = STOCK_END


def device(name, stock, relocates, holes=()):
    internal = Buf(SIZE)
    internal[:STOCK_END] = stock * STOCK_END
    internal[STOCK_END : STOCK_END + 0x10] = b"C" * 0x10  # our code
    return SimpleNamespace(
        name=name,
        internal=internal,
        internal_orig=bytes(internal),
        relocates_in_amalgam=relocates,
        AMALGAM_RELOCATE_END=0x0800_0000 + RELOCATE_END,
        freed_int_ranges=lambda: list(holes),
    )


@pytest.fixture
def setup():
    mario = device("mario", b"M", relocates=False)
    zelda = device("zelda", b"Z", relocates=True, holes=[(0xD0, 0xE0)])
    region = Region("bank1", 0x0800_0000, SIZE, pos=0x50)
    region.note(0, STOCK_END, "mario", "stock code")
    region.note(STOCK_END, 0x10, SHARED, "our code")
    region.reserve(0xC0, 0x40, "zelda", "relocated stock code")
    # Zelda's stock code, relocated in its own buffer.
    zelda.internal[0xC0:] = b"z" * 0x40
    return region, mario, zelda


def test_owners_and_shared_changes(setup):
    region, mario, zelda = setup
    off = region.alloc(8, "zelda", "data")
    zelda.internal[off : off + 8] = b"D" * 8
    mario.internal[0x10] = ord("m")  # A Mario patch to its stock code.
    zelda.internal[0x10] = ord("x")  # Zelda's stock image: not in the merge.
    zelda.internal[STOCK_END] = ord("s")  # Our code, patched by Zelda only.
    out = merge_internal(region, [zelda, mario])
    assert out[0x10] == ord("m") and out[0x11] == ord("M")
    assert out[STOCK_END : STOCK_END + 2] == b"sC"
    assert out[off : off + 8] == b"D" * 8
    assert out[0xC0:] == b"z" * 0x40


def test_data_in_a_hole_of_the_other_device(setup):
    region, mario, zelda = setup
    region.free(0xD0, 0xE0)  # A freed range of Zelda's relocated code.
    zelda.internal[0xD0:0xE0] = b"\xfe\xde" * 8  # udf
    off = region.alloc(8, "mario", "data")
    assert off == 0xD0
    mario.internal[off : off + 8] = b"D" * 8
    out = merge_internal(region, [zelda, mario])
    assert out[0xD0:0xD8] == b"D" * 8 and out[0xD8:0xE0] == b"\xfe\xde" * 4


def test_writing_into_the_other_device_fails(setup):
    region, mario, zelda = setup
    mario.internal[0xC4] = 1
    with pytest.raises(AmalgamError, match="0x080000C4"):
        merge_internal(region, [zelda, mario])


def test_both_changing_a_shared_byte_differently_fails(setup):
    region, mario, zelda = setup
    mario.internal[STOCK_END + 1] = 1
    zelda.internal[STOCK_END + 1] = 2
    with pytest.raises(AmalgamError, match="same bytes"):
        merge_internal(region, [zelda, mario])
    zelda.internal[STOCK_END + 1] = 1  # The same change is fine.
    assert merge_internal(region, [zelda, mario])[STOCK_END + 1] == 1


def test_data_over_own_relocated_code_fails(setup):
    region, mario, zelda = setup
    # Free in the image, but still the relocating device's stock code in its
    # buffer, which its relocation copied out.
    region.free(0x20, 0x28)
    off = region.alloc(8, "zelda", "data")
    assert off == 0x20
    zelda.internal[off : off + 8] = b"D" * 8
    with pytest.raises(AmalgamError, match="over its own relocated stock code"):
        merge_internal(region, [zelda, mario])


def _link(region, devices, start, data):
    """Our code linked at [start, start + len(data)): the baseline both
    buffers are compared with (patches/amalgam.py load)."""
    region.linked.append((start, start + len(data)))
    for d in devices:
        orig = bytearray(d.internal_orig)
        orig[start : start + len(data)] = data
        d.internal_orig = bytes(orig)


def test_linked_code_in_a_freed_hole():
    mario = device("mario", b"M", relocates=False, holes=[(0x10, 0x20)])
    zelda = device("zelda", b"Z", relocates=True)
    region = Region("bank1", 0x0800_0000, SIZE, pos=0x50)
    region.note(0, STOCK_END, "mario", "stock code")
    _link(region, [mario, zelda], 0x10, b"L" * 8)
    # free_int: udf, then our code back; a Mario patch to it.
    mario.internal[0x10:0x20] = b"\xfe\xde" * 8
    mario.internal[0x10:0x18] = b"L" * 8
    mario.internal[0x11] = ord("p")
    out = merge_internal(region, [zelda, mario])
    assert out[0x10:0x18] == b"LpLLLLLL"


def test_linked_code_must_be_freed():
    mario = device("mario", b"M", relocates=False)
    zelda = device("zelda", b"Z", relocates=True)
    region = Region("bank1", 0x0800_0000, SIZE, pos=0x50)
    region.note(0, STOCK_END, "mario", "stock code")
    _link(region, [mario, zelda], 0x10, b"L" * 8)
    with pytest.raises(AmalgamError, match="wasn't freed.*0x08000010"):
        merge_internal(region, [zelda, mario])
    # Zelda's free doesn't count: Mario's code is there.
    zelda.freed_int_ranges = lambda: [(0x10, 0x20)]
    with pytest.raises(AmalgamError, match="wasn't freed"):
        merge_internal(region, [zelda, mario])
