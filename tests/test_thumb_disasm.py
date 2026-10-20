"""patches/thumb_disasm.py against capstone (an optional dev dependency).

``decode()`` must give capstone's fields (``decode_capstone()``) for every
16-bit encoding and for random 32-bit ones, and ``xrefs.analyze()`` must map the
stock firmware identically with either decoder. The encodings here are
synthesized; none come from the stock firmware.
"""

import random
import struct
from pathlib import Path

import pytest

from patches import xrefs
from patches.thumb_disasm import decode, decode_capstone

REPO = Path(__file__).resolve().parents[1]
ADDR = 0x0800_1000
FIELDS = [
    "size",
    "first_reg",
    "branch",
    "pc_load",
    "pc_imm",
    "add_pc",
    "adds_imm",
    "ldr_pc",
    "cmp_imm",
    "table",
    "writes_pc",
    "mentions_pc",
]
# Mnemonics the descent reads (thumb_disasm's docstring); compared exactly.
READ_MNEMONICS = ("b", "it", "cb", "udf", "tb")

# capstone (M-class mode) decodes these; ARMv7E-M with FPv5 has none of them
# (A-profile, ARMv8-M, the MP extension).
NOT_V7M = {"hvc.w", "smc", "pldw"}
NOT_V7M_PREFIXES = ("tt", "stl", "lda")


def _fields(ins, ref):
    out = {f: getattr(ins, f) for f in FIELDS}
    m = ref.mnemonic
    if m.startswith(READ_MNEMONICS) and not m.startswith(("bkpt", "bic", "bfi", "bfc")):
        out["mnemonic"] = ins.mnemonic
    return out


def _outside_v7m(hw1, hw2, ref):
    """capstone decoded an encoding outside ARMv7E-M/FPv5 (or that ARMv7-M
    marks UNPREDICTABLE in a way capstone reads differently)."""
    m = ref.mnemonic
    if m in NOT_V7M or m.startswith(NOT_V7M_PREFIXES) or "_ns" in ref.op_str:
        return True
    if m.startswith(("rev", "rbit", "clz")) and hw1 & 0xF != hw2 & 0xF:
        return True  # the 2 rm fields differ
    if m == "udf.w" and hw2 >> 12 != 0b1010:
        return True  # capstone takes any second halfword
    if hw1 & 0xEC00 == 0xEC00:  # coprocessor space
        coproc = (hw2 >> 8) & 0xF
        neon = hw1 >> 8 in (0xEF, 0xFF, 0xF9, 0xFC, 0xFD)
        return coproc not in (10, 11) or neon or "16" in m.split(".", 1)[-1]
    return hw1 >> 8 == 0xF9 and m.startswith(("vld", "vst"))  # NEON loads


def test_known_encodings():
    def d(*hws, addr=ADDR):
        return decode(struct.pack(f"<{len(hws)}H", *hws), 0, addr)

    assert d(0xE7FE).branch == ADDR  # b .
    assert d(0xD0FE).mnemonic == "beq"
    ins = d(0xF000, 0xF800)  # bl .+4
    assert (ins.mnemonic, ins.branch) == ("bl", ADDR + 4)
    assert d(0xF43F, 0xAFFE).mnemonic == "beq.w"
    ins = d(0x4801)  # ldr r0, [pc, #4]
    assert (ins.pc_load, ins.ldr_pc, ins.first_reg) == ((4, 4), 0, 0)
    assert d(0xF85F, 0x0004).pc_load == (-4, 4)  # ldr.w r0, [pc, #-4]
    assert d(0xED9F, 0x0B01).pc_load == (4, 8)  # vldr d0, [pc, #4]
    assert d(0xF2AF, 0x0004).pc_imm == -4  # subw r0, pc, #4
    assert d(0x4478).add_pc == 0  # add r0, pc
    assert d(0x3001).adds_imm == (0, 1)  # adds r0, #1
    assert d(0xF1B0, 0x4F7F).cmp_imm == (0, -0x0100_0000)  # cmp.w r0, #0xff000000
    assert d(0xE8DF, 0xF001).table == (1, 1)  # tbb [pc, r1]
    assert d(0xBF0C).mnemonic == "ite"
    assert d(0xBD10).writes_pc  # pop {r4, pc}
    assert d(0xF8DF, 0xF000).writes_pc  # ldr.w pc, [pc]
    assert not d(0xBC10).writes_pc  # pop {r4}
    # A branch into external flash (capstone reports a negative target).
    assert d(0xE001, addr=0x9000_0000).branch == 0x9000_0006
    assert d(0xF7F0, 0xA000).mnemonic == "udf.w"
    assert d(0xFB00) is None  # half of a 32-bit instruction


@pytest.fixture(scope="module")
def capstone():
    return pytest.importorskip("capstone")


def _compare(capstone, encodings):
    missing, wrong = [], []
    for hw1, hw2, enc in encodings:
        ref = decode_capstone(enc, 0, ADDR)
        if ref is None:
            continue
        ins = decode(enc, 0, ADDR)
        if ins is None:
            if hw2 is None or not _outside_v7m(hw1, hw2, ref):
                missing.append((enc.hex(), ref.mnemonic, ref.op_str))
            continue
        if _fields(ins, ref) != _fields(ref, ref):
            if hw2 is None or not _outside_v7m(hw1, hw2, ref):
                wrong.append((enc.hex(), ref.mnemonic, ref.op_str))
    assert not missing, missing[:20]
    assert not wrong, wrong[:20]


def test_every_16_bit_encoding(capstone):
    _compare(
        capstone,
        [
            (hw, None, struct.pack("<HH", hw, 0))
            for hw in range(0x10000)
            if hw >> 11 < 0b11101
        ],
    )


def test_random_32_bit_encodings(capstone):
    rnd = random.Random(0)
    encodings = []
    for _ in range(100_000):
        hw1, hw2 = rnd.randrange(0xE800, 0x10000), rnd.randrange(0x10000)
        if hw1 >> 11 >= 0b11101:
            encodings.append((hw1, hw2, struct.pack("<HH", hw1, hw2)))
    _compare(capstone, encodings)


def _summary(code):
    return (
        {a: tuple(getattr(ins, f) for f in FIELDS) for a, ins in code.insns.items()},
        sorted(code.branches),
        sorted(code.pcrel),
        sorted(code.tables),
        sorted(code.entries),
        sorted(code.unbounded_tables),
        sorted(code.pc_offsets),
    )


@pytest.mark.parametrize("device", ["mario", "zelda"])
def test_stock_map_matches_capstone(capstone, device):
    for name in (f"internal_flash_backup_{device}.bin", f"flash_backup_{device}.bin"):
        if not (REPO / name).exists():
            pytest.skip(f"missing {name}")
    ours = xrefs.analyze(device)
    ref = xrefs.analyze(device, decode=decode_capstone)
    assert _summary(ours.code) == _summary(ref.code)
    for name in ref.other:
        assert _summary(ours.other[name]) == _summary(ref.other[name]), name
    assert ours.refs == ref.refs
    assert bytes(ours.cls) == bytes(ref.cls)
    assert ours.unreferenced == ref.unreferenced
    assert xrefs.units(ours.code) == xrefs.units(ref.code)
    assert xrefs.ext_refs(ours) == xrefs.ext_refs(ref)
