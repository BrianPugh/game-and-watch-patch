import struct

import capstone
import pytest

from patches.relocate import _decode_branch, _encode_branch

_md = capstone.Cs(
    capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS
)
_md.detail = True

# (encoding with offset 0, mnemonic) of each 32-bit branch kind.
BRANCHES = [
    ((0xF000, 0xF800), "bl"),
    ((0xF000, 0xB800), "b.w"),
    ((0xF000, 0x8000), "bcc.w"),  # beq.w
]


def _target(hw1, hw2, pc):
    ins = next(_md.disasm(struct.pack("<HH", hw1, hw2), pc))
    return ins.mnemonic, ins.operands[-1].imm


@pytest.mark.parametrize("encoding, kind", BRANCHES)
@pytest.mark.parametrize("offset", [0, 4, -4, 0x1234, -0x5678, 0xFFFFE, -0x100000])
def test_encode_branch_round_trip(encoding, kind, offset):
    hw1, hw2 = _encode_branch(*encoding, offset)
    assert _decode_branch(hw1, hw2) == (kind, offset)
    mnemonic, target = _target(hw1, hw2, 0x0810_0000)
    assert target == 0x0810_0000 + 4 + offset
    assert mnemonic.startswith(kind.split(".")[0] if kind != "bcc.w" else "beq")


@pytest.mark.parametrize("offset", [0x120000, -0x7FFFFE, 0xFFFFFE])
def test_encode_bl_long(offset):
    hw1, hw2 = _encode_branch(0xF000, 0xF800, offset)
    assert _target(hw1, hw2, 0x0800_0000)[1] == 0x0800_0004 + offset


def test_bcc_out_of_range():
    with pytest.raises(ValueError):
        _encode_branch(0xF000, 0x8000, 0x120000)


def test_decode_non_branch():
    assert _decode_branch(0xF3AF, 0x8000) is None  # nop.w
