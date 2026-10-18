"""scripts/amalgam_holes.py: packing our code into freed stock code."""

from patches.amalgam import SD_PRELOADED
from scripts.amalgam_holes import Item, assign, linker_script, pack

# A hole in Zelda's relocation source range (Mario's code only), one below
# SD_PRELOADED and 2 past it.
MARIO_ONLY = (0x0800_1000, 0x0800_1100, False)
LOW = (SD_PRELOADED - 0x1000, SD_PRELOADED - 0xF00, True)
SMALL = (SD_PRELOADED + 0x100, SD_PRELOADED + 0x140, True)
BIG = (SD_PRELOADED + 0x1000, SD_PRELOADED + 0x1400, True)


def item(obj, name, size, align=4):
    return Item(f"build/{obj}", name, size, align)


def test_item_room_and_constraints():
    assert item("mario.o", ".text.f", 6).room == 8
    assert item("mario.o", ".rodata.s", 5, align=1).room == 5
    zelda = item("game_select_zelda.o", ".text.f", 4)
    assert zelda.zelda and not zelda.fits(MARIO_ONLY) and zelda.fits(LOW)
    assert item("main.o", ".text.read_buttons", 4).zelda
    data = item("mario.o", ".rodata.SMB1_ROM", 4)
    assert data.patcher_data and not data.fits(LOW) and data.fits(SMALL)
    assert item("LzmaDec.o", ".text.LzmaDecode", 4).fits(MARIO_ONLY)


def test_pack_is_best_fit_largest_first():
    items = [
        item("LzmaDec.o", ".text.a", 0x10),
        item("LzmaDec.o", ".text.b", 0xF0),
        item("zelda.o", ".text.c", 0x20),
    ]
    placed, left = pack(items, [MARIO_ONLY, SMALL])
    assert not left
    # b first; a then takes what b left over, c can't go in MARIO_ONLY.
    assert [i.name for i in placed[MARIO_ONLY]] == [".text.b", ".text.a"]
    assert [i.name for i in placed[SMALL]] == [".text.c"]
    placed, left = pack(items, [SMALL])
    assert [i.name for i in left] == [".text.b"]


def test_assign_keeps_the_largest_holes_empty():
    items = [item("LzmaDec.o", ".text.a", 0x40), item("zelda.o", ".text.b", 0x80)]
    placed, left = assign(items, [MARIO_ONLY, LOW, SMALL, BIG])
    assert not left
    assert not placed.get(BIG)
    assert sum(len(v) for v in placed.values()) == 2


def test_linker_script_lists_each_hole_by_alignment():
    a = item("mario.o", ".rodata.s", 3, align=1)
    b = item("mario.o", ".text.f", 8)
    ld = linker_script({MARIO_ONLY: [a, b], SMALL: []})
    hole = f"HOLE_{MARIO_ONLY[0]:08X}"
    assert f">{hole}" in ld
    assert ld.index("*/mario.o(.text.f)") < ld.index("*/mario.o(.rodata.s)")
    assert f"HOLE_{SMALL[0]:08X}" not in ld
    assert "INCLUDE STM32H7B0VBTx_FLASH_text.ld" in ld
