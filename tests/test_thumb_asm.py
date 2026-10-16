"""Tests for the minimal Thumb-2 assembler in ``patches.thumb_asm``.

capstone (a runtime dependency) disassembles every form over its operand range,
so these always run. keystone-engine, when installed, is the byte-for-byte
reference: those tests skip without it.
"""

import capstone
import pytest

from patches.thumb_asm import (
    ThumbAssemblyError,
    assemble,
    encode_modified_immediate,
    iter_modified_immediates,
)

_md = capstone.Cs(
    capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS
)
_md.syntax = capstone.CS_OPT_SYNTAX_NOREGNAME  # r12, not ip


def disasm(code, addr=0):
    """(mnemonic, op_str) of each instruction capstone decodes from ``code``."""
    data = bytes(assemble(code, addr))
    insns = list(_md.disasm(data, addr))
    assert sum(i.size for i in insns) == len(data), code
    return [(i.mnemonic, i.op_str) for i in insns]


def imm(op_str):
    """The trailing ``#imm`` of a capstone operand string (printed signed)."""
    return int(op_str.rsplit("#", 1)[1], 0) & 0xFFFFFFFF


@pytest.fixture(scope="module")
def keystone():
    ks = pytest.importorskip("keystone")
    return ks.Ks(ks.KS_ARCH_ARM, ks.KS_MODE_THUMB)


def ks_asm(keystone, code, addr=0):
    """Assemble with keystone, returning bytes or ``None`` if it rejects the input."""
    from keystone import KsError

    try:
        encoding, _ = keystone.asm(code, addr)
    except KsError:
        return None
    return encoding


def assert_match(keystone, code, addr=0):
    expected = ks_asm(keystone, code, addr)
    if expected is None:
        with pytest.raises(ThumbAssemblyError):
            assemble(code, addr)
    else:
        assert assemble(code, addr) == expected, code


# The exact instruction strings the Mario/Zelda patches emit, including their
# irregular whitespace. Dynamic immediates use representative values. Bytes are
# from the old keystone cache (``patches/keystone_cache.json``) or keystone.
PATCHER_INSTRUCTIONS = [
    ("mov.w r1, #0x00000", 0, [79, 240, 0, 1]),
    ("movw r1, #5000", 0, [65, 242, 136, 49]),  # --hard-reset-time
    ("movw r2, #1500", 0, [64, 242, 220, 82]),  # --sleep-time
    ("movs r0, #0; nop", 0, [0, 32, 0, 191]),
    ("cmp.w r0, #1080", 0, [176, 245, 135, 111]),  # --mario-song-time
    ("b 0x1c", 0, [12, 224]),
    ("mov.w r2, #45056", 0, [79, 244, 48, 66]),
    ("mov.w r3, #45056", 0, [79, 244, 48, 67]),
    (
        "ite ne; movne.w r4, #0xd2000; moveq.w r4, #0xd1000",
        0,
        [20, 191, 79, 244, 82, 36, 79, 244, 81, 36],
    ),
    ("b.w #0x801b504", 0x800F430, [12, 240, 104, 184]),
    ("add.w r2,r1,#0x10", 0, [1, 241, 16, 2]),
    ("add.w r7,r0,#0x10", 0, [0, 241, 16, 7]),
    ("sub.w r1,r8,#0x10", 0, [168, 241, 16, 1]),
    ("sub.w r6,r2,#0x10", 0, [162, 241, 16, 6]),
    ("mov r1,r2", 0, [17, 70]),
    ("mov   r5,r1", 0, [13, 70]),
    ("sub   sp,#0x10", 0, [132, 176]),
    ("mov   r1,r6", 0, [49, 70]),
    ("mov   r0,r7", 0, [56, 70]),
    ("mov   r2,r7", 0, [58, 70]),
    ("mov   r0,r5", 0, [40, 70]),
    ("mov r7,r0", 0, [7, 70]),
]


@pytest.mark.parametrize(
    "code,addr,expected",
    PATCHER_INSTRUCTIONS,
    ids=[c for c, *_ in PATCHER_INSTRUCTIONS],
)
def test_patcher_instruction_strings(code, addr, expected):
    assert assemble(code, addr) == expected


@pytest.mark.parametrize(
    "code,addr,expected",
    PATCHER_INSTRUCTIONS,
    ids=[c for c, *_ in PATCHER_INSTRUCTIONS],
)
def test_patcher_instruction_strings_keystone(keystone, code, addr, expected):
    assert ks_asm(keystone, code, addr) == expected


# --------------------------------------------------------------------------- #
# capstone round-trips over each form's operand range
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rd", ["r0", "r1", "r2", "r12"])
def test_movw_full_range_capstone(rd):
    for value in range(0x10000):
        ((mnemonic, op_str),) = disasm(f"movw {rd}, #{value}")
        assert mnemonic == "movw" and op_str.startswith(rd + ",")
        assert imm(op_str) == value


@pytest.mark.parametrize("mnemonic", ["mov.w", "cmp.w", "add.w", "sub.w"])
def test_modified_immediates_capstone(mnemonic):
    operands = {
        "mov.w": "r4, ",
        "cmp.w": "r0, ",
        "add.w": "r3, r8, ",
        "sub.w": "r3, r8, ",
    }
    for value in iter_modified_immediates():
        ((got, op_str),) = disasm(f"{mnemonic} {operands[mnemonic]}#{value}")
        assert got == mnemonic, value
        assert op_str.startswith(operands[mnemonic].strip())
        assert imm(op_str) == value


@pytest.mark.parametrize("mnemonic", ["mov.w", "cmp.w", "add.w", "sub.w"])
def test_modified_immediates_reject(mnemonic):
    operands = {
        "mov.w": "r2, ",
        "cmp.w": "r0, ",
        "add.w": "r3, r1, ",
        "sub.w": "r3, r1, ",
    }
    # Odd --mario-song-time seconds (e.g. 19 s = 1140 frames) aren't encodable.
    for value in [1140, 7000, 0x101, 0x12345, 0xABCD, 0x80000001]:
        with pytest.raises(ThumbAssemblyError):
            assemble(f"{mnemonic} {operands[mnemonic]}#{value}")


def test_page_multiples_encodable():
    # mov.w r2/r3, #compressed_len and the NVRAM addresses are page multiples.
    for value in range(0, 0x100000, 0x1000):
        encode_modified_immediate(value)


def test_nvram_ite_capstone():
    code = "ite ne; movne.w r4, #0xff000; moveq.w r4, #0xfe000"
    assert disasm(code) == [
        ("ite", "ne"),
        ("movne.w", "r4, #0xff000"),
        ("moveq.w", "r4, #0xfe000"),
    ]


def test_movs_capstone():
    for rd in range(8):
        for value in range(0x100):
            ((mnemonic, op_str),) = disasm(f"movs r{rd}, #{value}")
            assert (mnemonic, op_str.split(",")[0]) == ("movs", f"r{rd}")
            assert imm(op_str) == value


def test_mov_register_capstone():
    regs = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]
    for rd in regs:
        for rm in regs:
            ((mnemonic, op_str),) = disasm(f"mov {rd}, {rm}")
            assert (mnemonic, op_str) == ("mov", f"{rd}, {rm}")


def test_sub_sp_capstone():
    for value in range(0, 0x200, 4):
        ((mnemonic, op_str),) = disasm(f"sub sp, #{value}")
        assert mnemonic == "sub" and imm(op_str) == value


def test_b_narrow_capstone():
    addr = 0x6FC8
    for target in range(addr + 4 - 0x800, addr + 4 + 0x800, 2):
        ((mnemonic, op_str),) = disasm(f"b #{target}", addr)
        assert mnemonic == "b" and imm(op_str) == target
    for target in (addr + 4 - 0x802, addr + 4 + 0x800):
        with pytest.raises(ThumbAssemblyError):
            assemble(f"b #{target}", addr)


def test_b_wide_capstone():
    addr = 0x0801_0938
    deltas = list(range(-0x2000, 0x2000, 2)) + [
        -(1 << 24),
        -0x80_0000,
        -0x10_0000,
        0x10_0000,
        0x7F_FFFE,
        (1 << 24) - 2,
    ]
    for delta in deltas:
        target = addr + 4 + delta
        ((mnemonic, op_str),) = disasm(f"b.w #{target:#x}", addr)
        assert mnemonic == "b.w" and imm(op_str) == target, hex(delta)
    with pytest.raises(ThumbAssemblyError):
        assemble(f"b.w #{addr + 4 + (1 << 24):#x}", addr)


@pytest.mark.parametrize("pattern", ["", "t", "e", "tt", "te", "et", "ee", "ete"])
@pytest.mark.parametrize("cond", ["eq", "ne", "cs", "mi", "ge", "lt", "gt", "le"])
def test_it_blocks_capstone(pattern, cond):
    mnemonics = {"t": "mov", "e": "mov"}
    inverse = {"eq": "ne", "ne": "eq", "cs": "cc", "mi": "pl"}
    inverse.update({"ge": "lt", "lt": "ge", "gt": "le", "le": "gt"})
    # capstone needs the conditional instructions to accept the IT block.
    body = [f"mov{cond}.w r0, #1"]
    for ch in pattern:
        body.append(f"{mnemonics[ch]}{cond if ch == 't' else inverse[cond]}.w r0, #1")
    insns = disasm(f"it{pattern} {cond}; " + "; ".join(body))
    assert insns[0] == (f"it{pattern}", "hs" if cond == "cs" else cond)
    assert len(insns) == 2 + len(pattern)


# --------------------------------------------------------------------------- #
# keystone reference comparisons (skipped without keystone)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rd", ["r0", "r2", "r3", "r7", "r12"])
def test_movw_full_range(keystone, rd):
    for value in range(0, 0x10000):
        code = f"movw {rd}, #{value}"
        assert assemble(code) == keystone.asm(code)[0], code


@pytest.mark.parametrize("mnemonic", ["mov.w r4,", "cmp.w r0,", "cmp.w r8,"])
def test_modified_immediates(keystone, mnemonic):
    for value in iter_modified_immediates():
        code = f"{mnemonic} #{value}"
        assert assemble(code) == keystone.asm(code)[0], hex(value)


def test_mov_w_rejects_unrepresentable(keystone):
    for value in [1140, 7000, 0x1B58, 0x12345, 0x101, 0xABCD, 0x80000001]:
        for code in (f"mov.w r2, #{value}", f"cmp.w r0, #{value}"):
            assert ks_asm(keystone, code) is None, code
            with pytest.raises(ThumbAssemblyError):
                assemble(code)


@pytest.mark.parametrize("cond", ["eq", "ne", "cs", "mi", "ge", "le"])
def test_conditional_mov_w(keystone, cond):
    # Conditional ``mov.w`` is only legal inside an IT block.
    for value in [0, 0x1000, 0x30000, 0x87000, 0xFF000, 45056]:
        assert_match(keystone, f"it {cond}; mov{cond}.w r4, #{value}")


@pytest.mark.parametrize("mnemonic", ["add.w", "sub.w"])
@pytest.mark.parametrize("rn", ["r0", "r1", "r2", "r8", "sp"])
def test_add_sub_w(keystone, mnemonic, rn):
    for value in [0, 0x10, 0xFF, 0x100, 0x1000, 45056, 0xFF000]:
        assert_match(keystone, f"{mnemonic} r3, {rn}, #{value}")


@pytest.mark.parametrize("mnemonic", ["add", "sub"])
@pytest.mark.parametrize("cond", ["eq", "ne", "cs", "mi", "ge", "le"])
def test_conditional_add_sub_w(keystone, mnemonic, cond):
    for value in [0, 0x10, 0xFF, 0x1000, 0xFF000]:
        assert_match(keystone, f"it {cond}; {mnemonic}{cond}.w r4, r4, #{value}")


@pytest.mark.parametrize("rt", ["r0", "r4", "r12", "sp", "lr", "pc"])
def test_ldr_w_pc_literal(keystone, rt):
    for value in [0, 4, 0x84, 100, 0xFFF, -4, -460, -568, -0xFFF]:
        assert_match(keystone, f"ldr.w {rt}, [pc, #{value}]")


def test_movs(keystone):
    for rd in range(8):
        for value in range(0x100):
            assert_match(keystone, f"movs r{rd}, #{value}")


def test_mov_register_all_pairs(keystone):
    regs = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]
    for rd in regs:
        for rm in regs:
            assert_match(keystone, f"mov {rd}, {rm}")


def test_sub_sp(keystone):
    for value in range(0, 0x200, 4):
        assert_match(keystone, f"sub sp, #{value}")


def test_b_narrow_range(keystone):
    addr = 0x1000
    for target in range(addr - 0x420, addr + 0x420, 2):
        assert_match(keystone, f"b #{target}", addr)


def test_b_wide_range(keystone):
    addr = 0x0800_0000
    for delta in list(range(-0x2000, 0x2000, 2)) + [-0x80_0000, 0x10_0000, 0x7F_FFFE]:
        assert_match(keystone, f"b.w #{addr + delta}", addr)


@pytest.mark.parametrize(
    "mnemonic", ["it", "itt", "ite", "ittt", "itte", "itet", "itee", "itete"]
)
@pytest.mark.parametrize("cond", ["eq", "ne", "cs", "mi", "ge", "lt", "gt", "le"])
def test_it_blocks(keystone, mnemonic, cond):
    inverse = {"eq": "ne", "ne": "eq", "cs": "cc", "mi": "pl"}
    inverse.update({"ge": "lt", "lt": "ge", "gt": "le", "le": "gt"})
    body = [f"mov{cond}.w r0, #1"]
    body += [
        f"mov{cond if ch == 't' else inverse[cond]}.w r0, #1" for ch in mnemonic[2:]
    ]
    assert_match(keystone, f"{mnemonic} {cond}; " + "; ".join(body))


@pytest.mark.parametrize(
    "code",
    [
        "movne.w r4, #1",  # no IT block
        "addeq.w r0, r0, #1",
        "it eq; movne.w r4, #1",  # wrong slot
        "ite ne; movne.w r4, #1; movne.w r4, #2",
        "it ne; mov.w r4, #1",  # unpredicated instruction in the block
        "it ne",  # block missing instructions
        "itt ne; movne.w r4, #1",
        "it ne; it ne; movne.w r4, #1",
        "ite al; mov.w r4, #1; mov.w r4, #2",
    ],
)
def test_it_predicates_rejected(code):
    with pytest.raises(ThumbAssemblyError):
        assemble(code)


def test_it_condition_aliases():
    assert assemble("it hs; movcs.w r4, #1") == assemble("it cs; movhs.w r4, #1")


def test_nop(keystone):
    assert_match(keystone, "nop")


@pytest.mark.parametrize(
    "code",
    [
        "movw sp, #1",
        "movw pc, #1",
        "mov.w sp, #1",
        "mov.w pc, #1",
        "add.w pc, r0, #1",
        "add.w r0, pc, #1",
        "sub.w pc, r0, #1",
        "sub.w r0, pc, #1",
        "cmp.w pc, #1",
    ],
)
def test_rejects_disallowed_registers(keystone, code):
    assert ks_asm(keystone, code) is None, f"keystone unexpectedly accepted {code}"
    with pytest.raises(ThumbAssemblyError):
        assemble(code)


# --------------------------------------------------------------------------- #
# unit checks
# --------------------------------------------------------------------------- #
def test_encode_modified_immediate_examples():
    assert encode_modified_immediate(0) == 0x000
    assert encode_modified_immediate(0xFF) == 0x0FF
    assert encode_modified_immediate(0x00FF00FF) == 0x1FF
    assert encode_modified_immediate(0xFF00FF00) == 0x2FF
    assert encode_modified_immediate(0xFFFFFFFF) == 0x3FF
    assert encode_modified_immediate(0xB000) == 0xC30  # 45056, rotation form


@pytest.mark.parametrize(
    "code",
    [
        "push {r0}",
        "nop r0",
        "movs r8, #1",  # keystone widens this to movs.w; the patches want 2 bytes
        "movs r0, #256",
        "movw r0, #65536",
        "sub sp, #2",
    ],
)
def test_unsupported(code):
    with pytest.raises(ThumbAssemblyError):
        assemble(code)
