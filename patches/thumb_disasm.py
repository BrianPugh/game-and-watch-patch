"""A minimal Thumb-2 decoder (ARMv7E-M with the FPv5 extension) for
``patches/xrefs.py``.

``decode()`` doesn't produce assembly text. It gives what the recursive
descent needs from one instruction (``Insn``): its size, branch target,
PC-relative load or address, the registers the pc-offset and table-branch
patterns track, and whether it writes PC. The fields follow capstone's
operand model, which the descent used before: ``decode_capstone()`` derives
the same fields from capstone (a dev-only dependency), and the tests compare
the 2 over every 16-bit encoding, random 32-bit ones and the stock firmware.

Mnemonics match capstone's where the descent reads them (branches, ``it``,
``bx``, ``udf``, ``tbb``/``tbh``); elsewhere they're only for reading.
Encodings outside ARMv7E-M/FPv5 (or that it doesn't decode) give None, which
ends the descent like an instruction capstone can't decode.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PC = 15
CONDS = ["eq", "ne", "hs", "lo", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt"]
CONDS += ["gt", "le"]


@dataclass(frozen=True)
class Insn:
    size: int
    mnemonic: str
    # Core register (0-15) of the first operand, if it's one.
    first_reg: int | None = None
    # Target of b, b<c>, bl, cbz, cbnz.
    branch: int | None = None
    # (displacement, load size) of a [pc, #disp] operand.
    pc_load: tuple | None = None
    # adr, or add/addw/sub/subw rd, pc, #imm: offset from Align(pc, 4).
    pc_imm: int | None = None
    # add rX, pc (2 operands): rX.
    add_pc: int | None = None
    # adds rX, #imm (2 operands): (rX, imm).
    adds_imm: tuple | None = None
    # ldr rX, [pc, #k] (2 operands): rX.
    ldr_pc: int | None = None
    # cmp rX, #imm: (rX, imm as a signed 32-bit value).
    cmp_imm: tuple | None = None
    # tbb/tbh: (index register, entry size).
    table: tuple | None = None
    # A load, mov, add, pop or ldm that writes pc.
    writes_pc: bool = False
    # pc is an operand.
    mentions_pc: bool = False
    # capstone's operand text (decode_capstone() only).
    op_str: str = ""


def _sext(value, bits):
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _s32(value):
    return _sext(value & 0xFFFF_FFFF, 32)


def _expand_imm(imm12):
    """ThumbExpandImm."""
    imm8 = imm12 & 0xFF
    if imm12 >> 10 == 0:
        return [
            imm8,
            imm8 * 0x0001_0001,
            imm8 * 0x0100_0100,
            imm8 * 0x0101_0101,
        ][(imm12 >> 8) & 3]
    value, rot = 0x80 | (imm12 & 0x7F), imm12 >> 7
    return ((value >> rot) | (value << (32 - rot))) & 0xFFFF_FFFF


def _low(reglist):
    """Lowest register of a non-empty list (a register-list's first operand)."""
    return (reglist & -reglist).bit_length() - 1 if reglist else None


def decode(mem, offset, addr):
    """The instruction at ``mem[offset:]``, which runs at ``addr``; None if
    there isn't a whole one or it isn't decoded."""
    if offset + 2 > len(mem):
        return None
    hw1 = struct.unpack_from("<H", mem, offset)[0]
    if hw1 >> 11 < 0b11101:
        return _decode16(hw1, addr)
    if offset + 4 > len(mem):
        return None
    hw2 = struct.unpack_from("<H", mem, offset + 2)[0]
    return _decode32(hw1, hw2, addr)


# --- 16-bit -------------------------------------------------------------------


_DP16 = ["ands", "eors", "lsls", "lsrs", "asrs", "adcs", "sbcs", "rors"]
_DP16 += ["tst", "rsbs", "cmp", "cmn", "orrs", "muls", "bics", "mvns"]
_LDST_REG = ["str", "strh", "strb", "ldrsb", "ldr", "ldrh", "ldrb", "ldrsh"]
_HINTS = {0: "nop", 1: "yield", 2: "wfe", 3: "wfi", 4: "sev"}


def _decode16(hw, addr):
    def insn(mnemonic, **kw):
        return Insn(2, mnemonic, **kw)

    top = hw >> 10
    if hw >> 14 == 0:  # shift, add, subtract, move, compare
        op = (hw >> 11) & 7
        rd, rdn = hw & 7, (hw >> 8) & 7
        if op < 3:
            if op == 0 and (hw >> 6) & 0x1F == 0:
                return insn("movs", first_reg=rd)
            return insn(["lsls", "lsrs", "asrs"][op], first_reg=rd)
        if op == 3:
            return insn(["adds", "subs"][(hw >> 9) & 1], first_reg=rd)
        imm8 = hw & 0xFF
        if op == 4:
            return insn("movs", first_reg=rdn)
        if op == 5:
            return insn("cmp", first_reg=rdn, cmp_imm=(rdn, imm8))
        if op == 6:
            return insn("adds", first_reg=rdn, adds_imm=(rdn, imm8))
        return insn("subs", first_reg=rdn)
    if top == 0b010000:
        return insn(_DP16[(hw >> 6) & 0xF], first_reg=hw & 7)
    if top == 0b010001:  # special data instructions, branch and exchange
        op = (hw >> 8) & 3
        rdn = ((hw >> 4) & 8) | (hw & 7)
        rm = (hw >> 3) & 0xF
        if op == 3:
            # Bits 2:0 should be 0; capstone reads bit 2 as ARMv8-M's bxns/blxns.
            mnemonic = ("blx" if hw & 0x80 else "bx") + ("ns" if hw & 4 else "")
            if hw & 0x80 and hw & 3 and not hw & 4:
                return None
            return insn(mnemonic, first_reg=rm, mentions_pc=rm == PC)
        mentions = PC in (rdn, rm)
        if op == 1:
            return insn("cmp", first_reg=rdn, mentions_pc=mentions)
        if op == 2:
            return insn("mov", first_reg=rdn, writes_pc=rdn == PC, mentions_pc=mentions)
        # add rdn, rm; "add rd, sp, rd" when rm is sp (3 operands).
        return insn(
            "add",
            first_reg=rdn,
            add_pc=rdn if rm == PC else None,
            writes_pc=rdn == PC,
            mentions_pc=mentions,
        )
    if top >> 1 == 0b01001:  # ldr rt, [pc, #imm8 * 4]
        rt = (hw >> 8) & 7
        return insn(
            "ldr",
            first_reg=rt,
            pc_load=((hw & 0xFF) * 4, 4),
            ldr_pc=rt,
            mentions_pc=True,
        )
    if top >> 2 == 0b0101:
        return insn(_LDST_REG[(hw >> 9) & 7], first_reg=hw & 7)
    if hw >> 13 == 0b011:
        return insn(["str", "ldr", "strb", "ldrb"][(hw >> 11) & 3], first_reg=hw & 7)
    if hw >> 12 == 0b1000:
        return insn(["strh", "ldrh"][(hw >> 11) & 1], first_reg=hw & 7)
    if hw >> 12 == 0b1001:
        return insn(["str", "ldr"][(hw >> 11) & 1], first_reg=(hw >> 8) & 7)
    if hw >> 11 == 0b10100:
        rd = (hw >> 8) & 7
        return insn("adr", first_reg=rd, pc_imm=(hw & 0xFF) * 4)
    if hw >> 11 == 0b10101:
        return insn("add", first_reg=(hw >> 8) & 7)
    if hw >> 12 == 0b1011:
        return _misc16(hw, addr, insn)
    if hw >> 12 == 0b1100:
        if not hw & 0xFF:
            return None
        return insn(["stm", "ldm"][(hw >> 11) & 1], first_reg=(hw >> 8) & 7)
    if hw >> 12 == 0b1101:
        cond = (hw >> 8) & 0xF
        if cond == 0xE:
            return insn("udf")
        if cond == 0xF:
            return insn("svc")
        target = (addr + 4 + _sext(hw & 0xFF, 8) * 2) & 0xFFFF_FFFF
        return insn("b" + CONDS[cond], branch=target)
    if hw >> 11 == 0b11100:
        target = (addr + 4 + _sext(hw & 0x7FF, 11) * 2) & 0xFFFF_FFFF
        return insn("b", branch=target)
    return None


def _misc16(hw, addr, insn):
    op = (hw >> 5) & 0x7F
    if op >> 2 == 0b00000:
        return insn("add", first_reg=13)
    if op >> 2 == 0b00001:
        return insn("sub", first_reg=13)
    if op >> 3 in (0b0001, 0b0011, 0b1001, 0b1011):
        rn = hw & 7
        imm = (((hw >> 9) & 1) << 6) | (((hw >> 3) & 0x1F) << 1)
        mnemonic = "cbnz" if hw & 0x800 else "cbz"
        return insn(mnemonic, first_reg=rn, branch=(addr + 4 + imm) & 0xFFFF_FFFF)
    if op >> 3 == 0b0010:
        return insn(["sxth", "sxtb", "uxth", "uxtb"][(op >> 1) & 3], first_reg=hw & 7)
    if op >> 4 == 0b010:
        reglist = (hw & 0xFF) | ((hw & 0x100) << 6)
        return insn("push", first_reg=_low(reglist)) if reglist else None
    if op == 0b0110011:
        return None if hw & 8 else insn("cpsid" if hw & 0x10 else "cpsie")
    if op >> 3 == 0b1010 and (op >> 1) & 3 != 2:
        return insn(["rev", "rev16", None, "revsh"][(op >> 1) & 3], first_reg=hw & 7)
    if op >> 4 == 0b110:
        reglist = (hw & 0xFF) | ((hw & 0x100) << 7)
        if not reglist:
            return None
        pc = bool(hw & 0x100)
        return insn("pop", first_reg=_low(reglist), writes_pc=pc, mentions_pc=pc)
    if op >> 3 == 0b1110:
        return insn("bkpt")
    if op >> 3 == 0b1111:
        mask, firstcond = hw & 0xF, (hw >> 4) & 0xF
        if mask == 0:
            return insn(_HINTS.get(firstcond, "hint"))
        n = 4 - ((mask & -mask).bit_length() - 1)
        then = firstcond & 1 if firstcond != 0xF else 0  # capstone: 0xF is al
        letters = "".join(
            "t" if (mask >> (4 - i)) & 1 == then else "e" for i in range(1, n)
        )
        return insn("it" + letters)
    return None


# --- 32-bit -------------------------------------------------------------------


# Data-processing opcodes (modified immediate and shifted register).
_DP32 = {
    0b0000: "and",
    0b0001: "bic",
    0b0010: "orr",
    0b0011: "orn",
    0b0100: "eor",
    0b0110: "pkhbt",
    0b1000: "add",
    0b1010: "adc",
    0b1011: "sbc",
    0b1101: "sub",
    0b1110: "rsb",
}
# Their compare forms (rd = pc with S).
_DP32_TEST = {0b0000: "tst", 0b0100: "teq", 0b1000: "cmn", 0b1101: "cmp"}


def _decode32(hw1, hw2, addr):
    op1 = (hw1 >> 11) & 3
    op2 = (hw1 >> 4) & 0x7F
    if op1 == 1:
        if op2 & 0b1100100 == 0:
            return _ldm_stm(hw1, hw2)
        if op2 & 0b1100100 == 0b0000100:
            return _dual_excl_table(hw1, hw2, addr)
        if op2 & 0b1100000 == 0b0100000:
            return _dp_shifted(hw1, hw2)
        return _coproc(hw1, hw2)
    if op1 == 2:
        if hw2 & 0x8000:
            return _branch_misc(hw1, hw2, addr)
        if op2 & 0b0100000 == 0:
            return _dp_modified_imm(hw1, hw2)
        return _dp_plain_imm(hw1, hw2)
    if op2 & 0b1000000:
        return _coproc(hw1, hw2)
    if op2 & 0b1110001 == 0b0000000:
        return _store_single(hw1, hw2)
    if op2 & 0b1100111 == 0b0000001:
        return _load_byte_half(hw1, hw2, 1)
    if op2 & 0b1100111 == 0b0000011:
        return _load_byte_half(hw1, hw2, 2)
    if op2 & 0b1100111 == 0b0000101:
        return _load_word(hw1, hw2)
    if op2 & 0b1110000 == 0b0100000:
        return _dp_register(hw1, hw2)
    if op2 & 0b1111000 == 0b0110000:
        return _multiply(hw1, hw2)
    if op2 & 0b1111000 == 0b0111000:
        return _long_multiply(hw1, hw2)
    return None


def _insn(mnemonic, **kw):
    return Insn(4, mnemonic, **kw)


def _ldm_stm(hw1, hw2):
    op, load, wback, rn = (hw1 >> 7) & 3, hw1 & 0x10, hw1 & 0x20, hw1 & 0xF
    if op in (0, 3):
        return None  # SRS/RFE (not M-profile)
    pc = bool(hw2 & 0x8000)
    if op == 1:
        if not load:
            return _insn("stm.w", first_reg=rn, mentions_pc=pc or rn == PC)
        if wback and rn == 13:
            return _insn("pop.w", first_reg=_low(hw2), writes_pc=pc, mentions_pc=pc)
        mentions = pc or rn == PC
        return _insn("ldm.w", first_reg=rn, writes_pc=mentions, mentions_pc=mentions)
    if not load:
        if wback and rn == 13:
            return _insn("push.w", first_reg=_low(hw2), mentions_pc=pc)
        return _insn("stmdb", first_reg=rn, mentions_pc=pc or rn == PC)
    return _insn("ldmdb", first_reg=rn, mentions_pc=pc or rn == PC)


def _dual_excl_table(hw1, hw2, addr):
    op1, op2, op3 = (hw1 >> 7) & 3, (hw1 >> 4) & 3, (hw2 >> 4) & 0xF
    rn, rt, rd = hw1 & 0xF, hw2 >> 12, (hw2 >> 8) & 0xF
    mentions = PC in (rn, rt, rd)
    # capstone takes an exclusive's [pc, #imm] as a word load.
    excl = ((hw2 & 0xFF) * 4 if op2 < 2 else 0, 4) if rn == PC else None
    if op1 == 0 and op2 == 0:
        return _insn("strex", first_reg=rd, pc_load=excl, mentions_pc=mentions)
    if op1 == 0 and op2 == 1:
        return _insn(
            "ldrex",
            first_reg=rt,
            pc_load=excl,
            writes_pc=rt == PC,
            mentions_pc=PC in (rn, rt),
        )
    if (op1 >> 1 == 0 and op2 >= 2) or op1 >> 1 == 1:
        ldrd = op2 & 1
        load = None
        if rn == PC:  # capstone sizes a strd's [pc, #imm] like a word load
            disp = (hw2 & 0xFF) * 4 if hw1 & 0x100 else 0  # 0 when post-indexed
            load = (disp if hw1 & 0x80 else -disp, 8 if ldrd else 4)
        return _insn(
            "ldrd" if ldrd else "strd",
            first_reg=rt,
            pc_load=load,
            writes_pc=bool(ldrd) and rt == PC,
            mentions_pc=mentions,
        )
    if op1 == 1 and op2 == 0 and op3 in (4, 5):
        return _insn(
            "strexb" if op3 == 4 else "strexh",
            first_reg=hw2 & 0xF,
            pc_load=excl,
            mentions_pc=PC in (rn, rt, hw2 & 0xF),
        )
    if op1 == 1 and op2 == 1:
        rm = hw2 & 0xF
        if op3 in (0, 1):
            return _insn(
                "tbh" if op3 else "tbb",
                table=(rm, 2 if op3 else 1),
                mentions_pc=PC in (rn, rm),
            )
        if op3 in (4, 5):
            return _insn(
                "ldrexb" if op3 == 4 else "ldrexh",
                first_reg=rt,
                pc_load=excl,
                writes_pc=rt == PC,
                mentions_pc=PC in (rn, rt),
            )
    return None


def _dp_result(op, s, rn, rd, operands_pc, base=None):
    """An Insn for a data-processing opcode; ``base`` names a move form."""
    if rd == PC and s and op in _DP32_TEST:
        return _insn(
            _DP32_TEST[op] + ".w", first_reg=rn, mentions_pc=rn == PC or operands_pc
        )
    if base is None:
        base = _DP32.get(op)
        if base is None:
            return None
    mnemonic = base + ("s" if s else "") + ".w"
    uses_rn = base not in ("mov", "mvn") and not base.startswith(("lsl", "lsr"))
    uses_rn = uses_rn and not base.startswith(("asr", "ror", "rrx"))
    return _insn(
        mnemonic,
        first_reg=rd,
        writes_pc=rd == PC and base in ("mov", "add") and not s,
        mentions_pc=rd == PC or (uses_rn and rn == PC) or operands_pc,
    )


def _dp_shifted(hw1, hw2):
    op, s, rn = (hw1 >> 5) & 0xF, bool(hw1 & 0x10), hw1 & 0xF
    rd, rm = (hw2 >> 8) & 0xF, hw2 & 0xF
    base = None
    if op == 0b0010 and rn == PC:
        shift = (hw2 >> 4) & 3
        imm5 = ((hw2 >> 10) & 0x1C) | ((hw2 >> 6) & 3)
        if shift == 0 and imm5 == 0:
            base = "mov"
        elif shift == 3 and imm5 == 0:
            base = "rrx"
        else:
            base = ["lsl", "lsr", "asr", "ror"][shift]
    elif op == 0b0011 and rn == PC:
        base = "mvn"
    elif op == 0b0110 and s:
        return None
    return _dp_result(op, s, rn, rd, rm == PC, base)


def _dp_modified_imm(hw1, hw2):
    op, s, rn = (hw1 >> 5) & 0xF, bool(hw1 & 0x10), hw1 & 0xF
    rd = (hw2 >> 8) & 0xF
    imm = _expand_imm(((hw1 >> 10) & 1) << 11 | ((hw2 >> 4) & 0x700) | (hw2 & 0xFF))
    if op == 0b0110:
        return None
    if rd == PC and s and op == 0b1101:
        return _insn(
            "cmp.w", first_reg=rn, cmp_imm=(rn, _s32(imm)), mentions_pc=rn == PC
        )
    base = None
    if op == 0b0010 and rn == PC:
        base = "mov"
    elif op == 0b0011 and rn == PC:
        base = "mvn"
    out = _dp_result(op, s, rn, rd, False, base)
    if out is not None and rn == PC and op in (0b1000, 0b1101) and not s:
        # add.w/sub.w rd, pc, #imm: an address, like adr (capstone's immediate
        # is signed).
        return Insn(
            4,
            out.mnemonic,
            first_reg=rd,
            pc_imm=-_s32(imm) if op == 0b1101 else _s32(imm),
            writes_pc=out.writes_pc,
            mentions_pc=True,
        )
    return out


def _dp_plain_imm(hw1, hw2):
    op, rn, rd = (hw1 >> 4) & 0x1F, hw1 & 0xF, (hw2 >> 8) & 0xF
    imm12 = ((hw1 >> 10) & 1) << 11 | ((hw2 >> 4) & 0x700) | (hw2 & 0xFF)
    if op in (0b00000, 0b01010):
        mnemonic = "addw" if op == 0 else "subw"
        if rn == PC:
            return _insn(
                mnemonic,
                first_reg=rd,
                pc_imm=imm12 if op == 0 else -imm12,
                mentions_pc=True,
            )
        return _insn(mnemonic, first_reg=rd, mentions_pc=rd == PC)
    names = {
        0b00100: "movw",
        0b01100: "movt",
        0b10000: "ssat",
        0b10010: "ssat",
        0b10100: "sbfx",
        0b10110: "bfi",
        0b11000: "usat",
        0b11010: "usat",
        0b11100: "ubfx",
    }
    name = names.get(op)
    if name is None:
        return None
    if name == "bfi" and rn == PC:
        name = "bfc"
    uses_rn = name not in ("movw", "movt", "bfc")
    return _insn(name, first_reg=rd, mentions_pc=rd == PC or (uses_rn and rn == PC))


def _branch_misc(hw1, hw2, addr):
    op1 = (hw2 >> 12) & 7
    op = (hw1 >> 4) & 0x7F
    s = (hw1 >> 10) & 1
    if op1 == 0b010 and op == 0b1111111:
        return _insn("udf.w")
    if op1 & 0b101 == 0:
        if op & 0b0111000 != 0b0111000:  # b<c>.w
            cond = (hw1 >> 6) & 0xF
            j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
            imm = (s << 20) | (j2 << 19) | (j1 << 18)
            imm |= ((hw1 & 0x3F) << 12) | ((hw2 & 0x7FF) << 1)
            target = (addr + 4 + _sext(imm, 21)) & 0xFFFF_FFFF
            return _insn("b" + CONDS[cond] + ".w", branch=target)
        if op >> 1 == 0b011100:
            return _insn("msr", mentions_pc=hw1 & 0xF == PC)
        if op == 0b0111010:
            if hw2 & 0x700:
                return None
            return _insn(_HINTS.get(hw2 & 0xFF, "hint") + ".w")
        if op == 0b0111011:
            name = {2: "clrex", 4: "dsb", 5: "dmb", 6: "isb"}.get((hw2 >> 4) & 0xF)
            return _insn(name) if name else None
        if op >> 1 == 0b011111:
            rd = (hw2 >> 8) & 0xF
            return _insn("mrs", first_reg=rd, mentions_pc=rd == PC)
        return None
    if op1 & 0b101 == 0b100:
        return None  # blx to ARM state (not M-profile)
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
    imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12)
    imm |= (hw2 & 0x7FF) << 1
    target = (addr + 4 + _sext(imm, 25)) & 0xFFFF_FFFF
    return _insn("b.w" if op1 & 0b101 == 0b001 else "bl", branch=target)


def _store_single(hw1, hw2):
    op1, rn, rt = (hw1 >> 5) & 7, hw1 & 0xF, hw2 >> 12
    if op1 == 0b111 or op1 == 0b011:
        return None
    size = ["strb", "strh", "str"][op1 & 3]
    if rn == PC:  # capstone takes [pc, #imm] (UNDEFINED) as a word load
        if not op1 & 4:
            return None
        disp = hw2 & 0xFFF
        return _insn(size + ".w", first_reg=rt, pc_load=(disp, 4), mentions_pc=True)
    if op1 & 4:  # imm12
        mnemonic = size + ".w"
    elif hw2 & 0x800:  # imm8 with P, U, W
        if hw2 & 0x500 == 0:
            return None
        mnemonic = size + ("t" if hw2 & 0x700 == 0x600 else "")
    elif hw2 & 0xFC0 == 0:  # register
        mnemonic = size + ".w"
        if hw2 & 0xF == PC:
            return _insn(mnemonic, first_reg=rt, mentions_pc=True)
    else:
        return None
    return _insn(mnemonic, first_reg=rt, mentions_pc=rt == PC)


def _load_byte_half(hw1, hw2, size):
    op1, rn, rt = (hw1 >> 7) & 3, hw1 & 0xF, hw2 >> 12
    signed = op1 & 2
    name = ("ldrs" if signed else "ldr") + ("b" if size == 1 else "h")
    if rn == PC:
        disp = hw2 & 0xFFF
        disp = disp if hw1 & 0x80 else -disp
        if rt == PC:  # pld/pli [pc, #imm]
            return _insn(
                "pli" if signed else "pld", pc_load=(disp, 4), mentions_pc=True
            )
        return _insn(name + ".w", first_reg=rt, pc_load=(disp, size), mentions_pc=True)
    hint = "pli" if signed else "pld"
    if op1 & 1:
        if rt == PC:
            return _insn(hint) if size == 1 else None
        return _insn(name + ".w", first_reg=rt)
    if hw2 & 0x800:
        puw = (hw2 >> 8) & 7
        if puw & 5 == 0:
            return None
        if rt == PC and puw == 0b100:
            return _insn(hint) if size == 1 else None
        unpriv = puw == 0b110
        # A load into pc is UNPREDICTABLE; capstone takes it as writing pc.
        return _insn(
            name + ("t" if unpriv else ""),
            first_reg=rt,
            writes_pc=rt == PC,
            mentions_pc=rt == PC,
        )
    if hw2 & 0xFC0 == 0:
        if rt == PC:
            return _insn(hint, mentions_pc=hw2 & 0xF == PC) if size == 1 else None
        return _insn(name + ".w", first_reg=rt, mentions_pc=hw2 & 0xF == PC)
    return None


def _load_word(hw1, hw2):
    op1, rn, rt = (hw1 >> 7) & 3, hw1 & 0xF, hw2 >> 12
    writes = rt == PC
    if op1 & 2:
        return None
    if rn == PC:
        disp = hw2 & 0xFFF
        return _insn(
            "ldr.w",
            first_reg=rt,
            pc_load=(disp if hw1 & 0x80 else -disp, 4),
            ldr_pc=rt,
            writes_pc=writes,
            mentions_pc=True,
        )
    if op1 == 1:
        return _insn("ldr.w", first_reg=rt, writes_pc=writes, mentions_pc=writes)
    if hw2 & 0x800:
        if hw2 & 0x500 == 0:
            return None
        unpriv = hw2 & 0x700 == 0x600
        return _insn(
            "ldrt" if unpriv else "ldr",
            first_reg=rt,
            writes_pc=writes,
            mentions_pc=writes,
        )
    if hw2 & 0xFC0 == 0:
        rm = hw2 & 0xF
        return _insn(
            "ldr.w", first_reg=rt, writes_pc=writes, mentions_pc=PC in (rt, rm)
        )
    return None


def _dp_register(hw1, hw2):
    if hw2 & 0xF000 != 0xF000:
        return None
    op1, rn, rd, rm = (hw1 >> 4) & 0xF, hw1 & 0xF, (hw2 >> 8) & 0xF, hw2 & 0xF
    regs = [rd, rn, rm]
    if op1 < 6 and hw2 & 0x80 and rn == PC:
        regs = [rd, rm]  # sxth/uxtb/...: rn = pc means no add
    return _insn("dp", first_reg=rd, mentions_pc=PC in regs)


def _multiply(hw1, hw2):
    op1, op2 = (hw1 >> 4) & 7, (hw2 >> 4) & 3
    if hw2 & 0xC0 or (op1 in (0, 7) and op2 > 1) or (op1 > 1 and op2 > 1):
        return None
    if op1 == 7 and op2:
        return None
    rd, ra = (hw2 >> 8) & 0xF, hw2 >> 12
    regs = [rd, hw1 & 0xF, hw2 & 0xF]
    if ra != PC or (op1 == 0 and op2 == 1) or op1 == 6:
        regs.append(ra)  # ra = pc means no accumulate, except in mls/smmls
    return _insn("mul", first_reg=rd, mentions_pc=PC in regs)


def _long_multiply(hw1, hw2):
    op1, op2 = (hw1 >> 4) & 7, (hw2 >> 4) & 0xF
    rn, rd, rm = hw1 & 0xF, (hw2 >> 8) & 0xF, hw2 & 0xF
    if op1 in (1, 3):
        if op2 != 0xF:
            return None
        return _insn(
            "udiv" if op1 == 3 else "sdiv", first_reg=rd, mentions_pc=PC in (rd, rn, rm)
        )
    valid = {0: {0}, 2: {0}, 4: {0, 8, 9, 10, 11, 12, 13}, 5: {12, 13}, 6: {0, 6}}
    if op2 not in valid.get(op1, ()):
        return None
    rdlo = hw2 >> 12
    return _insn("mull", first_reg=rdlo, mentions_pc=PC in (rdlo, rd, rn, rm))


def _coproc(hw1, hw2):
    """The FP (cp10/cp11) part of the coprocessor space."""
    coproc = (hw2 >> 8) & 0xF
    if coproc not in (10, 11):
        return None
    top = hw1 >> 8  # 0xEC/0xED/0xEE, or 0xFE (FPv5's vsel, vrint, ...)
    if top == 0xFE:
        return _insn("vfp") if not hw2 & 0x10 else None
    if top not in (0xEC, 0xED, 0xEE):
        return None
    op1 = (hw1 >> 4) & 0x3F
    rn, rt = hw1 & 0xF, hw2 >> 12
    if op1 >> 1 == 0b00010:  # vmov between 2 core registers and d or 2 s
        mentions = PC in (rt, rn)
        if hw1 & 0x10:  # to core registers
            return _insn("vmov", first_reg=rt, mentions_pc=mentions)
        return _insn("vmov", mentions_pc=mentions)
    if op1 >> 5 == 0 and op1 & 0b111010 != 0:  # extension register load/store
        p, u, w, load = hw1 & 0x100, hw1 & 0x80, hw1 & 0x20, hw1 & 0x10
        if p and not w:  # vldr/vstr
            if rn == PC and load:
                disp = (hw2 & 0xFF) * 4
                return _insn(
                    "vldr",
                    pc_load=(disp if u else -disp, 8 if coproc == 11 else 4),
                    mentions_pc=True,
                )
            if rn == PC:  # capstone takes vstr's [pc, #imm] as a word load
                disp = (hw2 & 0xFF) * 4
                return _insn(
                    "vstr", pc_load=(disp if u else -disp, 4), mentions_pc=True
                )
            return _insn("vldr" if load else "vstr")
        fldmx = coproc == 11 and hw2 & 1  # fldmx/fstmx: no vpush/vpop form
        pushpop = (p and not u and not load) or (not p and u and load)
        if rn == 13 and w and pushpop and not fldmx:
            return _insn("vpop" if load else "vpush")
        return _insn("vldm" if load else "vstm", first_reg=rn, mentions_pc=rn == PC)
    if op1 >> 4 == 0b10:
        if not hw2 & 0x10:
            return _insn("vfp")  # data processing
        if hw1 & 0x10:  # to a core register: vmov rt, sn / vmov rt, dn[x] / vmrs
            if hw1 & 0xEF == 0xE1 and rt == PC:
                return _insn("vmrs")  # vmrs apsr_nzcv, fpscr
            return _insn("vmov", first_reg=rt, mentions_pc=rt == PC)
        return _insn("vmov", mentions_pc=rt == PC)
    return None


# --- capstone, the reference ---------------------------------------------------

_md = None


def _load_size(ins):
    m = ins.mnemonic.split(".")[0]
    if m.startswith("ldrd"):
        return 8
    if m.startswith(("ldrh", "ldrsh")):
        return 2
    if m.startswith(("ldrb", "ldrsb")):
        return 1
    if m == "vldr":
        from capstone import arm as A

        return 8 if A.ARM_REG_D0 <= ins.operands[0].reg <= A.ARM_REG_D31 else 4
    return 4


def decode_capstone(mem, offset, addr):
    """``decode()``'s fields from capstone (needs the capstone package)."""
    global _md
    import capstone
    from capstone import arm as A

    if _md is None:
        _md = capstone.Cs(
            capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS
        )
        _md.detail = True
    ins = next(_md.disasm(bytes(mem[offset : offset + 4]), addr, 1), None)
    if ins is None:
        return None
    core = {A.ARM_REG_R0 + i: i for i in range(13)}
    core.update({A.ARM_REG_SP: 13, A.ARM_REG_LR: 14, A.ARM_REG_PC: 15})
    ops = ins.operands
    mn = ins.mnemonic.split(".")[0]

    def is_reg(op, reg=None):
        return op.type == A.ARM_OP_REG and (reg is None or op.reg == reg)

    def is_imm(op):
        return op.type == A.ARM_OP_IMM

    def pc_mem(op):
        return op.type == A.ARM_OP_MEM and op.mem.base == A.ARM_REG_PC

    fields = {}
    if ops and is_reg(ops[0]):
        fields["first_reg"] = core.get(ops[0].reg)
    for op in ops:
        if pc_mem(op) and op.mem.index == 0:
            fields["pc_load"] = (op.mem.disp, _load_size(ins))
    if mn == "adr":
        fields["pc_imm"] = ops[-1].imm
    elif (
        mn in ("add", "addw", "sub", "subw")
        and len(ops) == 3
        and is_reg(ops[1], A.ARM_REG_PC)
        and is_imm(ops[2])
    ):
        fields["pc_imm"] = -ops[2].imm if mn.startswith("sub") else ops[2].imm
    if mn == "add" and len(ops) == 2 and is_reg(ops[1], A.ARM_REG_PC):
        fields["add_pc"] = core.get(ops[0].reg)
    if mn == "adds" and len(ops) == 2 and is_imm(ops[1]):
        fields["adds_imm"] = (core.get(ops[0].reg), ops[1].imm)
    if mn == "ldr" and len(ops) == 2 and pc_mem(ops[1]):
        fields["ldr_pc"] = core.get(ops[0].reg)
    if mn == "cmp" and len(ops) == 2 and is_imm(ops[1]):
        fields["cmp_imm"] = (core.get(ops[0].reg), ops[1].imm)
    if mn in ("tbb", "tbh"):
        fields["table"] = (core.get(ops[0].mem.index), 1 if mn == "tbb" else 2)
    bmn = "bcc" if mn[0] == "b" and mn[1:] in CONDS else mn
    if bmn in ("b", "bcc", "bl", "cbz", "cbnz") and ops and is_imm(ops[-1]):
        fields["branch"] = ops[-1].imm & 0xFFFF_FFFF
    writes_pc = bool(ops) and is_reg(ops[0], A.ARM_REG_PC)
    writes_pc = writes_pc and (mn.startswith("ldr") or mn in ("mov", "add"))
    writes_pc = writes_pc or (
        mn in ("pop", "ldm", "ldmia", "ldmfd") and "pc" in ins.op_str
    )
    return Insn(
        ins.size,
        ins.mnemonic,
        writes_pc=writes_pc,
        mentions_pc="pc" in ins.op_str,
        op_str=ins.op_str,
        **fields,
    )
