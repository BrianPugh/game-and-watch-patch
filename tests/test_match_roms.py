import importlib.util
import struct
from pathlib import Path

from patches.relocate import _encode_branch
from patches.xrefs import Code

_spec = importlib.util.spec_from_file_location(
    "match_roms", Path(__file__).parents[1] / "tools" / "re" / "match_roms.py"
)
match_roms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(match_roms)

# push {r4, lr}; movs r0, #1; adds r0, #2..#5; pop {r4, pc}
CALLEE = [0xB510, 0x2001, 0x3002, 0x3003, 0x3004, 0x3005, 0xBD10]


def _caller(addr, callee, literal_index):
    """push; ldr r0, [pc, #k]; bl callee; movs r1, #7; adds r1, #1; adds r1, #2; pop."""
    ldr_pc = addr + 2
    k = (literal_index - ((ldr_pc + 4) & ~3)) // 4
    hw1, hw2 = _encode_branch(0xF000, 0xF800, callee - (addr + 4 + 4))
    return [0xB510, 0x4800 | k, hw1, hw2, 0x2107, 0x3101, 0x3102, 0xBD10]


def _code(base, layout, literals, size=0x80):
    mem = bytearray(size)
    for off, halfwords in layout.items():
        struct.pack_into(f"<{len(halfwords)}H", mem, off, *halfwords)
    for off, value in literals.items():
        struct.pack_into("<I", mem, off, value)
    code = Code(bytes(mem), base, base, base + size)
    code.descend({base + off for off in layout})
    return code


def test_match_pairs_identical_functions_at_different_addresses():
    # ROM a: caller at 0, callee at 0x20, literal (a RAM address) at 0x14.
    a = _code(
        0x0800_0000,
        {0x00: _caller(0x0800_0000, 0x0800_0020, 0x0800_0014), 0x20: CALLEE},
        {0x14: 0x2000_1000},
    )
    # ROM b: two copies of the callee (so its signature isn't unique), the caller
    # calls the first one, and its literal is a different RAM address.
    b = _code(
        0x0810_0000,
        {
            0x10: CALLEE,
            0x30: CALLEE,
            0x40: _caller(0x0810_0040, 0x0810_0010, 0x0810_0054),
        },
        {0x54: 0x2000_A000},
    )
    pairs = match_roms.match(
        match_roms.Rom({"internal": a}), match_roms.Rom({"internal": b})
    )
    # The caller pairs by signature; the duplicated callee only through the call.
    assert pairs == {0x0800_0000: 0x0810_0040, 0x0800_0020: 0x0810_0010}


def test_match_keeps_peripheral_constants():
    def rom(peripheral):
        return match_roms.Rom(
            {
                "internal": _code(
                    0x0800_0000,
                    {
                        0x00: _caller(0x0800_0000, 0x0800_0020, 0x0800_0014),
                        0x20: CALLEE,
                    },
                    {0x14: peripheral},
                )
            }
        )

    pairs = match_roms.match(rom(0x5802_4400), rom(0x5802_4800))
    # A different peripheral register makes it a different function.
    assert 0x0800_0000 not in pairs
    assert pairs[0x0800_0020] == 0x0800_0020


def test_tiny_functions_are_not_matched():
    tiny = [0x2001, 0x4770]  # movs r0, #1; bx lr
    rom = match_roms.Rom({"internal": _code(0x0800_0000, {0x00: tiny}, {})})
    assert match_roms.match(rom, rom) == {}


def test_descend_follows_branches_in_external_flash():
    # capstone reports targets >= 0x80000000 as negative numbers.
    # b.n +6; ...; bx lr
    code = _code(0x9000_0000, {0x00: [0xE001], 0x06: [0x4770]}, {})
    assert code.branches[0][1] == 0x9000_0006
    assert 0x9000_0006 in code.insns
