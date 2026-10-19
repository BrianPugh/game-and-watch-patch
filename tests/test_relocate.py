import struct

import capstone
import pytest

from patches.relocate import (
    _decode_branch,
    _encode_branch,
    _encode_pcrel,
    compact_runs,
    final_address,
)

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


def test_compact_runs_close_the_holes():
    runs = compact_runs(0x100, 0x200, 0x1000, [(0x140, 0x150), (0x180, 0x1A0)])
    assert runs == [
        (0x100, 0x140, 0x1000),
        (0x150, 0x180, 0x1040),
        (0x1A0, 0x200, 0x1070),
    ]
    assert final_address(runs, 0x13C) == 0x103C
    assert final_address(runs, 0x150) == 0x1040
    assert final_address(runs, 0x1FE) == 0x10CE
    # In a hole: where it was closed.
    assert final_address(runs, 0x144) == 0x1040


def _pcrel_disp(code, addr=0x0800_0000):
    """Offset from Align(PC, 4) that capstone decodes from a PC-relative load
    or adr/addw/subw rX, pc."""
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    ins = next(md.disasm(bytes(code), addr))
    for op in ins.operands:
        if (
            op.type == capstone.arm.ARM_OP_MEM
            and op.mem.base == capstone.arm.ARM_REG_PC
        ):
            return op.mem.disp
    imm = ins.operands[-1].imm
    return -imm if ins.mnemonic.startswith("sub") else imm


@pytest.mark.parametrize(
    "words, offsets",
    [
        ([0x4B01], [0, 1020]),  # ldr r3, [pc, #4]
        ([0xA002], [0, 1020]),  # adr r0, #8
        ([0xF8DF, 0x3480], [-4095, -100, 4000]),  # ldr.w r3, [pc, #1152]
        ([0xF8BF, 0x1010], [-8, 4095]),  # ldrh.w r1, [pc, #16]
        ([0xF99F, 0x2004], [-12, 12]),  # ldrsb.w r2, [pc, #4]
        ([0xE9DF, 0x0102], [-16, 1020]),  # ldrd r0, r1, [pc, #8]
        ([0xED9F, 0x0B02], [-8, 1020]),  # vldr d0, [pc, #8]
        ([0xF20F, 0x1023], [-0x456, 0xFFF]),  # addw r0, pc, #0x123
    ],
)
def test_encode_pcrel(words, offsets):
    for offset in offsets:
        buf = bytearray(struct.pack(f"<{len(words)}H", *words))
        _encode_pcrel(buf, 0, offset)
        assert _pcrel_disp(buf) == offset


def test_encode_pcrel_16bit_backwards_fails():
    with pytest.raises(NotImplementedError):
        _encode_pcrel(bytearray(struct.pack("<H", 0x4B01)), 0, -4)
