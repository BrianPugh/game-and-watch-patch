"""Minimal ARM Thumb-2 assembler for the gnw_patch firmware patcher.

This is a purpose-built, dependency-free replacement for keystone-engine. It
supports only the handful of Thumb-2 instruction forms emitted by the
Mario/Zelda firmware patches (ported from gnwmanager, which carries a copy of
this patcher):

* ``movw   Rd, #imm16``                      -- MOV (immediate), T3
* ``mov.w  Rd, #const`` (+ conditional, e.g. ``movne.w``/``moveq.w``)
                                             -- MOV (immediate), T2 (modified immediate)
* ``mov    Rd, Rm``                          -- MOV (register), T1
* ``movs   Rd, #imm8``                       -- MOV (immediate), T1
* ``adds   Rdn, #imm8``                      -- ADD (immediate), T2
* ``cmp    Rn, Rm``                          -- CMP (register), T1
* ``cmp.w  Rn, #const``                      -- CMP (immediate), T2 (modified immediate)
* ``add.w  Rd, Rn, #const`` (+ conditional)  -- ADD (immediate), T3 (modified immediate)
* ``sub.w  Rd, Rn, #const`` (+ conditional)  -- SUB (immediate), T3 (modified immediate)
* ``sub    sp, #imm``                        -- SUB (SP minus immediate), T2
* ``ldr.w  Rt, [pc, #imm]``                  -- LDR (literal), T2 (signed offset)
* ``ldr.w  Rt, [Rn, #imm12]``                -- LDR (immediate), T3
* ``strb   Rt, [Rn, #imm5]``                 -- STRB (immediate), T1
* ``lsls   Rd, Rm, #imm5``                   -- LSL (immediate), T1
* ``cbz``/``cbnz Rn, <target>``              -- CBZ/CBNZ, T1
* ``b      <target>``                        -- B, T2 (narrow)
* ``b<cond> <target>``                       -- B, T1 (narrow conditional, e.g. ``blt``)
* ``b.w    #<target>``                       -- B, T4 (wide)
* ``bl     #<target>``                       -- BL, T1
* ``it``/``itt``/``ite``/...                 -- IT block
* ``nop``                                    -- NOP, T1

Encodings follow the ARMv7-M Architecture Reference Manual and are verified
byte-for-byte against keystone-engine (when installed) and by capstone round-trips
in ``tests/test_thumb_asm.py``.

The public entry point :func:`assemble` mirrors the slice of keystone's
``Ks.asm`` API that the patcher relies on: it accepts an assembly string
(optionally several instructions separated by ``;``) plus the address of the
first instruction, and returns a ``list[int]`` of little-endian machine-code
bytes. Like keystone, it raises on operands that cannot be encoded.
"""

import re
from typing import List

__all__ = [
    "ThumbAssemblyError",
    "assemble",
    "encode_modified_immediate",
    "iter_modified_immediates",
]


class ThumbAssemblyError(ValueError):
    """Raised when an instruction or operand cannot be assembled."""


_CONDITIONS = {
    "eq": 0, "ne": 1, "cs": 2, "hs": 2, "cc": 3, "lo": 3,
    "mi": 4, "pl": 5, "vs": 6, "vc": 7, "hi": 8, "ls": 9,
    "ge": 10, "lt": 11, "gt": 12, "le": 13, "al": 14,
}  # fmt: skip

_CONDITION_NAMES = {}
for _name, _code in _CONDITIONS.items():
    _CONDITION_NAMES.setdefault(_code, _name)

_REGISTERS = {f"r{i}": i for i in range(16)}
_REGISTERS.update({"sp": 13, "lr": 14, "pc": 15})

_COND_RE = "|".join(_CONDITIONS)
# The optional condition comes from an enclosing IT block and does not change
# the encoding, so e.g. ``addne.w`` encodes identically to ``add.w``.
# :func:`assemble` checks each one against its IT slot.
_MOV_W_RE = re.compile(rf"^mov(?:{_COND_RE})?\.w$")
_ADD_W_RE = re.compile(rf"^add(?:{_COND_RE})?\.w$")
_SUB_W_RE = re.compile(rf"^sub(?:{_COND_RE})?\.w$")
_PREDICATED_RE = re.compile(rf"^(?:mov|add|sub)({_COND_RE})\.w$")
_B_COND_RE = re.compile(rf"^b({_COND_RE})$")
# ``ldr.w Rt, [pc, #imm]`` -- PC-relative literal load with a signed byte offset.
_LDR_W_PC_RE = re.compile(
    r"^ldr\.w\s+(\w+)\s*,\s*\[\s*pc\s*,\s*#\s*(-?(?:0x)?[0-9a-f]+)\s*\]$"
)
# ``ldr.w``/``strb Rt, [Rn{, #imm}]`` -- immediate-offset load/store.
_MEM_IMM_RE = re.compile(
    r"^(ldr\.w|strb)\s+(\w+)\s*,\s*\[\s*(\w+)\s*(?:,\s*#\s*((?:0x)?[0-9a-f]+))?\s*\]$"
)


def _reg(token: str) -> int:
    try:
        return _REGISTERS[token]
    except KeyError:
        raise ThumbAssemblyError(f"Invalid register {token!r}") from None


def _imm(token: str) -> int:
    if not token.startswith("#"):
        raise ThumbAssemblyError(f"Expected '#'-prefixed immediate, got {token!r}")
    try:
        return int(token[1:], 0)
    except ValueError:
        raise ThumbAssemblyError(f"Invalid immediate {token!r}") from None


def _branch_target(token: str) -> int:
    # Narrow ``b`` is written ``b 0x1c``; wide ``b.w`` is written ``b.w #0x...``.
    token = token[1:] if token.startswith("#") else token
    try:
        return int(token, 0)
    except ValueError:
        raise ThumbAssemblyError(f"Invalid branch target {token!r}") from None


def _emit16(hw: int) -> List[int]:
    return [hw & 0xFF, (hw >> 8) & 0xFF]


def _emit32(hw1: int, hw2: int) -> List[int]:
    return [hw1 & 0xFF, (hw1 >> 8) & 0xFF, hw2 & 0xFF, (hw2 >> 8) & 0xFF]


def _ror32(value: int, amount: int) -> int:
    amount &= 31
    return ((value >> amount) | (value << (32 - amount))) & 0xFFFFFFFF


def encode_modified_immediate(value: int) -> int:
    """Encode a 32-bit constant as a 12-bit Thumb "modified immediate".

    Parameters
    ----------
    value : int
        The constant to encode (masked to 32 bits).

    Returns
    -------
    int
        The 12-bit ``i:imm3:imm8`` field consumed by the T2 ``MOV``/``ADD``/
        ``SUB`` (immediate) encodings.

    Raises
    ------
    ThumbAssemblyError
        If ``value`` is not representable as a modified immediate (matching
        keystone, which rejects such operands rather than widening).
    """
    value &= 0xFFFFFFFF

    # 0b0000_0000_<abcdefgh>: an 8-bit value, zero-extended.
    if value <= 0xFF:
        return value

    b0 = value & 0xFF
    b1 = (value >> 8) & 0xFF
    b2 = (value >> 16) & 0xFF
    b3 = (value >> 24) & 0xFF

    # 0b0001: 0x00XY00XY
    if b1 == 0 and b3 == 0 and b0 == b2:
        return (0b0001 << 8) | b0
    # 0b0010: 0xXY00XY00
    if b0 == 0 and b2 == 0 and b1 == b3:
        return (0b0010 << 8) | b1
    # 0b0011: 0xXYXYXYXY
    if b0 == b1 == b2 == b3:
        return (0b0011 << 8) | b0

    # Rotation form: an 8-bit value 1bcdefgh rotated right by 8..31.
    for rotation in range(8, 32):
        unrotated = _ror32(value, -rotation % 32)  # undo the right-rotate
        if unrotated <= 0xFF and unrotated & 0x80:
            return (rotation << 7) | (unrotated & 0x7F)

    raise ThumbAssemblyError(f"0x{value:08X} is not a valid Thumb modified immediate")


def iter_modified_immediates():
    """Yield every distinct 32-bit constant representable as a modified immediate.

    Useful for exhaustively comparing :func:`encode_modified_immediate` against a
    reference assembler.
    """
    seen = set()

    def emit(v):
        v &= 0xFFFFFFFF
        if v not in seen:
            seen.add(v)
            return True
        return False

    for v in range(0x100):
        if emit(v):
            yield v
    for byte in range(1, 0x100):
        for v in (
            (byte << 16) | byte,
            (byte << 24) | (byte << 8),
            (byte << 24) | (byte << 16) | (byte << 8) | byte,
        ):
            if emit(v):
                yield v
    for rotation in range(8, 32):
        for imm7 in range(0x80):
            v = _ror32(0x80 | imm7, rotation)
            if emit(v):
                yield v


def _split_modified_immediate(const: int):
    twelve = encode_modified_immediate(const)
    return (twelve >> 11) & 1, (twelve >> 8) & 0x7, twelve & 0xFF


def _movw(ops):
    if len(ops) != 2:
        raise ThumbAssemblyError("movw expects 'Rd, #imm16'")
    rd = _reg(ops[0])
    if rd in (13, 15):  # sp/pc are UNPREDICTABLE destinations (keystone rejects them)
        raise ThumbAssemblyError(f"movw cannot target {ops[0]}")
    imm = _imm(ops[1])
    if not 0 <= imm <= 0xFFFF:
        raise ThumbAssemblyError(f"movw immediate 0x{imm:X} out of 16-bit range")
    imm4 = (imm >> 12) & 0xF
    i = (imm >> 11) & 1
    imm3 = (imm >> 8) & 0x7
    imm8 = imm & 0xFF
    hw1 = 0xF240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return _emit32(hw1, hw2)


def _mov_w(ops):
    if len(ops) != 2:
        raise ThumbAssemblyError("mov.w expects 'Rd, #const'")
    rd = _reg(ops[0])
    if rd in (13, 15):  # sp/pc are UNPREDICTABLE destinations (keystone rejects them)
        raise ThumbAssemblyError(f"mov.w cannot target {ops[0]}")
    i, imm3, imm8 = _split_modified_immediate(_imm(ops[1]))
    hw1 = 0xF04F | (i << 10)
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return _emit32(hw1, hw2)


def _addsub_w(ops, base_hw1, name):
    if len(ops) != 3:
        raise ThumbAssemblyError(f"{name} expects 'Rd, Rn, #const'")
    rd = _reg(ops[0])
    rn = _reg(ops[1])
    if rd == 15:  # pc destination is a different (branch) instruction; keystone rejects
        raise ThumbAssemblyError(f"{name} cannot target pc")
    if rn == 15:  # pc source is the literal/ADR form, not this encoding
        raise ThumbAssemblyError(f"{name} cannot use pc as the source register")
    # An sp destination is only defined for the SP plus/minus immediate form (rn == sp);
    # keystone accepts e.g. "add.w sp, r0, #1" but it's UNPREDICTABLE on ARMv7-M.
    if rd == 13 and rn != 13:
        raise ThumbAssemblyError(
            f"{name} can only target sp with sp as the source register"
        )
    i, imm3, imm8 = _split_modified_immediate(_imm(ops[2]))
    hw1 = base_hw1 | (i << 10) | rn
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return _emit32(hw1, hw2)


def _mov(ops):
    if len(ops) != 2:
        raise ThumbAssemblyError("mov expects 'Rd, Rm'")
    rd = _reg(ops[0])
    rm = _reg(ops[1])
    d = (rd >> 3) & 1
    hw = 0x4600 | (d << 7) | (rm << 3) | (rd & 0x7)
    return _emit16(hw)


def _low_reg_imm8(ops, base, name):
    # MOVS (immediate) T1 and ADDS (immediate) T2: low register, 8-bit immediate.
    if len(ops) != 2:
        raise ThumbAssemblyError(f"{name} expects 'Rd, #imm8'")
    rd = _reg(ops[0])
    if rd > 7:
        raise ThumbAssemblyError(f"{name} needs a low register, got {ops[0]}")
    imm = _imm(ops[1])
    if not 0 <= imm <= 0xFF:
        raise ThumbAssemblyError(f"{name} immediate 0x{imm:X} out of 8-bit range")
    return _emit16(base | (rd << 8) | imm)


def _cmp(ops):
    if len(ops) != 2:
        raise ThumbAssemblyError("cmp expects 'Rn, Rm'")
    rn = _reg(ops[0])
    rm = _reg(ops[1])
    if rn > 7 or rm > 7:
        raise ThumbAssemblyError("cmp (register) needs low registers")
    return _emit16(0x4280 | (rm << 3) | rn)


def _cmp_w(ops):
    if len(ops) != 2:
        raise ThumbAssemblyError("cmp.w expects 'Rn, #const'")
    rn = _reg(ops[0])
    if rn == 15:  # UNPREDICTABLE (keystone rejects it)
        raise ThumbAssemblyError("cmp.w cannot use pc")
    i, imm3, imm8 = _split_modified_immediate(_imm(ops[1]))
    hw1 = 0xF1B0 | (i << 10) | rn
    hw2 = (imm3 << 12) | 0x0F00 | imm8
    return _emit32(hw1, hw2)


def _sub_sp(ops):
    if len(ops) != 2 or _reg(ops[0]) != 13:
        raise ThumbAssemblyError("sub expects 'sp, #imm'")
    imm = _imm(ops[1])
    if imm % 4 or not 0 <= imm <= 0x1FC:
        raise ThumbAssemblyError(
            f"sub sp immediate 0x{imm:X} must be a multiple of 4 in [0, 0x1FC]"
        )
    return _emit16(0xB080 | (imm >> 2))


def _ldr_w_literal(rt_token, imm_token):
    # LDR (literal), T2: load Rt from Align(PC, 4) +/- imm12 (a byte offset). The
    # explicit ``[pc, #imm]`` form encodes imm directly, independent of the
    # instruction's address. Any Rt is permitted (keystone allows pc, a branch).
    rt = _reg(rt_token)
    imm = int(imm_token, 0)
    if not -0xFFF <= imm <= 0xFFF:
        raise ThumbAssemblyError(f"ldr.w literal offset {imm} out of +/-4095 range")
    u = 1 if imm >= 0 else 0  # add vs. subtract
    hw1 = 0xF85F | (u << 7)
    hw2 = (rt << 12) | (abs(imm) & 0xFFF)
    return _emit32(hw1, hw2)


def _ldr_w_imm(rt_token, rn_token, imm):
    # LDR (immediate), T3: Rt = [Rn + imm12]. Rn = pc is the literal form.
    rt = _reg(rt_token)
    rn = _reg(rn_token)
    if rn == 15:
        raise ThumbAssemblyError("ldr.w [pc, #imm] is the literal form")
    if not 0 <= imm <= 0xFFF:
        raise ThumbAssemblyError(f"ldr.w offset 0x{imm:X} out of 12-bit range")
    return _emit32(0xF8D0 | rn, (rt << 12) | imm)


def _strb(rt_token, rn_token, imm):
    # STRB (immediate), T1: low registers, 5-bit offset.
    rt = _reg(rt_token)
    rn = _reg(rn_token)
    if rt > 7 or rn > 7:
        raise ThumbAssemblyError("strb (immediate) needs low registers")
    if not 0 <= imm <= 31:
        raise ThumbAssemblyError(f"strb offset {imm} out of [0, 31]")
    return _emit16(0x7000 | (imm << 6) | (rn << 3) | rt)


def _lsls(ops):
    # LSL (immediate), T1. A shift of 0 is MOVS (register), a different instruction.
    if len(ops) != 3:
        raise ThumbAssemblyError("lsls expects 'Rd, Rm, #imm5'")
    rd = _reg(ops[0])
    rm = _reg(ops[1])
    if rd > 7 or rm > 7:
        raise ThumbAssemblyError("lsls (immediate) needs low registers")
    shift = _imm(ops[2])
    if not 1 <= shift <= 31:
        raise ThumbAssemblyError(f"lsls shift {shift} out of [1, 31]")
    return _emit16((shift << 6) | (rm << 3) | rd)


def _cbz(ops, addr, nonzero):
    name = "cbnz" if nonzero else "cbz"
    if len(ops) != 2:
        raise ThumbAssemblyError(f"{name} expects 'Rn, <target>'")
    rn = _reg(ops[0])
    if rn > 7:
        raise ThumbAssemblyError(f"{name} needs a low register")
    offset = _branch_target(ops[1]) - (addr + 4)
    if offset & 1 or not 0 <= offset <= 126:
        raise ThumbAssemblyError(f"{name} target out of range (offset {offset})")
    i, imm5 = offset >> 6, (offset >> 1) & 0x1F
    return _emit16(0xB100 | (nonzero << 11) | (i << 9) | (imm5 << 3) | rn)


def _b(ops, addr):
    if len(ops) != 1:
        raise ThumbAssemblyError("b expects a single target")
    offset = _branch_target(ops[0]) - (addr + 4)
    if offset & 1:
        raise ThumbAssemblyError("branch target must be halfword-aligned")
    imm = offset >> 1
    if not -0x400 <= imm <= 0x3FF:
        raise ThumbAssemblyError(
            f"b target out of range for the narrow encoding (offset {offset})"
        )
    return _emit16(0xE000 | (imm & 0x7FF))


def _b_cond(cond, ops, addr):
    if len(ops) != 1:
        raise ThumbAssemblyError(f"b{cond} expects a single target")
    if cond == "al":
        raise ThumbAssemblyError("use 'b' for an unconditional branch")
    offset = _branch_target(ops[0]) - (addr + 4)
    if offset & 1:
        raise ThumbAssemblyError("branch target must be halfword-aligned")
    imm = offset >> 1
    if not -0x80 <= imm <= 0x7F:
        raise ThumbAssemblyError(
            f"b{cond} target out of range for the narrow encoding (offset {offset})"
        )
    return _emit16(0xD000 | (_CONDITIONS[cond] << 8) | (imm & 0xFF))


def _b_w(ops, addr, name="b.w", link=False):
    if len(ops) != 1:
        raise ThumbAssemblyError(f"{name} expects a single target")
    offset = _branch_target(ops[0]) - (addr + 4)
    if offset & 1:
        raise ThumbAssemblyError("branch target must be halfword-aligned")
    if not -(1 << 24) <= offset < (1 << 24):
        raise ThumbAssemblyError(
            f"{name} target out of +/-16MB range (offset {offset})"
        )
    s = (offset >> 24) & 1
    i1 = (offset >> 23) & 1
    i2 = (offset >> 22) & 1
    imm10 = (offset >> 12) & 0x3FF
    imm11 = (offset >> 1) & 0x7FF
    j1 = (~(i1 ^ s)) & 1
    j2 = (~(i2 ^ s)) & 1
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | imm11
    return _emit32(hw1, hw2)


def _it_slots(text: str) -> List[int]:
    """Condition codes an IT instruction gives the instructions after it."""
    mnemonic, *ops = text.split()
    if len(ops) != 1:
        raise ThumbAssemblyError("IT expects a single condition")
    pattern = mnemonic[2:]  # the 't'/'e' characters following "it"
    if len(pattern) > 3 or any(ch not in "te" for ch in pattern):
        raise ThumbAssemblyError(f"Unsupported IT form {mnemonic!r}")
    try:
        cond = _CONDITIONS[ops[0]]
    except KeyError:
        raise ThumbAssemblyError(f"Invalid condition {ops[0]!r}") from None
    if cond == 14 and "e" in pattern:
        raise ThumbAssemblyError("an IT AL block has no else slots")
    return [cond] + [cond if ch == "t" else cond ^ 1 for ch in pattern]


def _it(mnemonic, ops):
    _it_slots(" ".join([mnemonic, *ops]))
    cond = _CONDITIONS[ops[0]]
    pattern = mnemonic[2:]
    fc0 = cond & 1
    n = 1 + len(pattern)
    mask = 1 << (4 - n)
    for idx, ch in enumerate(pattern):
        val = fc0 if ch == "t" else (fc0 ^ 1)
        mask |= val << (3 - idx)
    return _emit16(0xBF00 | (cond << 4) | mask)


def _assemble_one(text: str, addr: int) -> List[int]:
    # ``ldr.w Rt, [pc, #imm]`` has bracket syntax the generic tokenizer mangles.
    ldr = _LDR_W_PC_RE.match(text)
    if ldr:
        return _ldr_w_literal(ldr.group(1), ldr.group(2))
    mem = _MEM_IMM_RE.match(text)
    if mem:
        op, rt, rn, offset = mem.groups()
        offset = int(offset, 0) if offset else 0
        if op == "strb":
            return _strb(rt, rn, offset)
        return _ldr_w_imm(rt, rn, offset)

    tokens = text.replace(",", " ").split()
    if not tokens:
        return []
    mnemonic, ops = tokens[0], tokens[1:]

    if _MOV_W_RE.match(mnemonic):
        return _mov_w(ops)
    if mnemonic == "movw":
        return _movw(ops)
    if mnemonic == "mov":
        return _mov(ops)
    if mnemonic == "movs":
        return _low_reg_imm8(ops, 0x2000, "movs")
    if mnemonic == "adds":
        return _low_reg_imm8(ops, 0x3000, "adds")
    if mnemonic == "cmp":
        return _cmp(ops)
    if mnemonic == "lsls":
        return _lsls(ops)
    if mnemonic in ("cbz", "cbnz"):
        return _cbz(ops, addr, mnemonic == "cbnz")
    if mnemonic == "cmp.w":
        return _cmp_w(ops)
    if mnemonic == "nop" and not ops:
        return _emit16(0xBF00)
    if _ADD_W_RE.match(mnemonic):
        return _addsub_w(ops, 0xF100, "add.w")
    if _SUB_W_RE.match(mnemonic):
        return _addsub_w(ops, 0xF1A0, "sub.w")
    if mnemonic == "sub":
        return _sub_sp(ops)
    if mnemonic == "b.w":
        return _b_w(ops, addr)
    if mnemonic == "bl":
        return _b_w(ops, addr, "bl", link=True)
    if mnemonic == "b":
        return _b(ops, addr)
    b_cond = _B_COND_RE.match(mnemonic)
    if b_cond:
        return _b_cond(b_cond.group(1), ops, addr)
    if mnemonic.startswith("it"):
        return _it(mnemonic, ops)

    raise ThumbAssemblyError(f"Unsupported instruction: {text!r}")


def assemble(code: str, addr: int = 0) -> List[int]:
    """Assemble one or more Thumb-2 instructions.

    Parameters
    ----------
    code : str
        Assembly source. Multiple instructions may be separated by ``;``.
    addr : int
        Address of the first instruction, used to resolve PC-relative branches.

    Returns
    -------
    list of int
        Little-endian machine-code bytes.

    Raises
    ------
    ThumbAssemblyError
        If any instruction or operand cannot be assembled.
    """
    result = []
    it_slots = []  # conditions still owed to the current IT block
    for piece in code.lower().split(";"):
        piece = piece.strip()
        if not piece:
            continue
        predicate = _PREDICATED_RE.match(piece.split()[0])
        if it_slots:
            expected = it_slots.pop(0)
            if not predicate or _CONDITIONS[predicate.group(1)] != expected:
                raise ThumbAssemblyError(
                    f"{piece!r} doesn't match its IT slot ({_CONDITION_NAMES[expected]})"
                )
        elif predicate:
            raise ThumbAssemblyError(f"{piece!r} is conditional outside an IT block")
        encoded = _assemble_one(piece, addr + len(result))
        if piece.startswith("it"):
            it_slots = _it_slots(piece)
        result.extend(encoded)
    if it_slots:
        raise ThumbAssemblyError("IT block is missing instructions")
    return result
