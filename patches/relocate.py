"""Relocate a range of (patched) stock internal code to another address.

The stock reference map comes from ``patches/xrefs.py``. Moving a range
(each address to ``Relocation.final``) needs:

* absolute words (vector table, literal pools, pointer tables, ITCM/DTCM
  rwdata, external and SRAM3 data, our code's ``R_ARM_ABS32`` relocations)
  pointing into the range: repointed.
* self-relative words (constructor, init and rwdata tables, ``ldr``/``add pc``
  offsets) whose word and target are on opposite sides of the boundary.
* 32-bit branches crossing the boundary, in either direction: re-encoded. That's
  stock code calling our hooks, and our code calling stock (ELF relocations;
  link with ``--emit-relocs``).

Relative branches and PC-relative loads inside the range need nothing, unless
the move compacts: then stock code freed in the range is left out, every kept
run moves by its own offset (``final``), and 32-bit branches between runs are
re-encoded too.

It runs in 2 steps because the RAM images are compressed before the internal
image is final: ``fix_ram_images()`` before the rwdata/SRAM3 blobs are
written, ``apply()`` once everything else is patched.
"""

import contextlib
import io
import struct

import numpy as np
from elftools.elf.relocation import RelocationSection

from . import writelog, xrefs
from .exception import NotEnoughSpaceError
from .layout import BANK2_BASE, BANK2_END
from .xrefs import Code

R_ARM_ABS32 = 2
R_ARM_THM_CALL = 10
R_ARM_THM_JUMP24 = 30


def _decode_branch(hw1, hw2):
    """(kind, offset) of a 32-bit Thumb BL / B.W / Bcc.W, else None."""
    if hw1 & 0xF800 != 0xF000 or not hw2 & 0x8000:
        return None
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    op = hw2 & 0xD000
    if op in (0xD000, 0x9000):  # BL, B.W (T4)
        i1, i2 = 1 ^ (j1 ^ s), 1 ^ (j2 ^ s)
        off = (
            (s << 24)
            | (i1 << 23)
            | (i2 << 22)
            | ((hw1 & 0x3FF) << 12)
            | ((hw2 & 0x7FF) << 1)
        )
        off -= s << 25
        return ("bl" if op == 0xD000 else "b.w"), off
    if op == 0x8000 and (hw1 >> 6) & 0xF < 0xE:  # Bcc.W (T3)
        off = (
            (s << 20)
            | (j2 << 19)
            | (j1 << 18)
            | (((hw1 >> 0) & 0x3F) << 12)
            | ((hw2 & 0x7FF) << 1)
        )
        off -= s << 21
        return "bcc.w", off
    return None


def _encode_branch(hw1, hw2, offset):
    """Re-encode the 32-bit branch (hw1, hw2) with a new byte ``offset``."""
    kind, _ = _decode_branch(hw1, hw2)
    if offset & 1:
        raise ValueError("odd branch offset")
    if kind == "bcc.w":
        if not -(1 << 20) <= offset < (1 << 20):
            raise ValueError(f"Bcc.W offset {offset:#x} out of range")
        s = (offset >> 20) & 1
        hw1 = 0xF000 | (s << 10) | (hw1 & 0x03C0) | ((offset >> 12) & 0x3F)
        hw2 = (
            (hw2 & 0xD000)
            | (((offset >> 18) & 1) << 13)
            | (((offset >> 19) & 1) << 11)
            | ((offset >> 1) & 0x7FF)
        )
        return hw1, hw2
    if not -(1 << 24) <= offset < (1 << 24):
        raise ValueError(f"branch offset {offset:#x} out of range")
    s = (offset >> 24) & 1
    i1, i2 = (offset >> 23) & 1, (offset >> 22) & 1
    j1, j2 = 1 ^ i1 ^ s, 1 ^ i2 ^ s
    hw1 = 0xF000 | (s << 10) | ((offset >> 12) & 0x3FF)
    hw2 = (hw2 & 0xD000) | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    return hw1, hw2


class Relocation:
    """Move internal ``[lo, hi)`` of a patched device to ``dst``."""

    def __init__(self, device, lo, hi, dst, keep_vector_table=True, compact=False):
        """``dst`` is in bank 2 (a separate image) or in this device's own
        internal image, past its code and data; either range is reserved now so
        nothing else is placed there.

        ``keep_vector_table``: the moved vector table also stays at the start
        of the image (boot and VTOR use 0x08000000). The amalgam sets it false:
        Mario's vector table is there.

        ``compact`` (the amalgam): stock code freed in the range (``add_hole``,
        from ``Device.free_int``) is left out. ``finalize`` then fixes where
        each kept run goes, reserves only those and frees the rest up to our
        code; nothing may ask where moved code goes before that."""
        # PC-relative loads need word alignment; a moved vector table, VTOR's
        # (its 179 entries round up to 256 words).
        if (dst - lo) % 4:
            raise ValueError("relocation offset must be a multiple of 4")
        if lo == device.internal.FLASH_BASE and dst % 0x400:
            raise ValueError("a moved vector table must be 1 KB aligned")
        if dst_in_bank2(dst):
            if compact:
                raise ValueError("only a move within the image compacts")
            if dst + hi - lo > BANK2_END:
                raise ValueError(
                    f"0x{dst:08X}-0x{dst + hi - lo:08X} doesn't fit in bank 2 "
                    f"(0x{BANK2_BASE:08X}-0x{BANK2_END:08X})"
                )
            device.bank2_space.reserve(
                dst - BANK2_BASE, hi - lo, device.name, "stock code"
            )
        else:
            if dst < hi:
                raise ValueError(f"0x{dst:08X} overlaps the range it moves")
            if not compact:
                region = device.int_space
                region.reserve(
                    dst - region.base, hi - lo, device.name, "relocated stock code"
                )
        self.device = device
        self.keep_vector_table = keep_vector_table
        self.lo, self.hi, self.dst = lo, hi, dst
        self.compact = compact
        # [start, end) addresses of freed stock code compaction leaves out.
        self.holes = []
        # (start, end, new start) of each kept run, once finalized.
        self.runs = None if compact else [(lo, hi, dst)]
        self.map = device.stock_map
        self.counts = {}
        # writelog provenance of the moved range at dst, once applied.
        self.prov = None

    def moves(self, addr):
        return self.lo <= addr < self.hi

    def compacts(self, addr):
        """Whether freeing stock code at ``addr`` closes the gap instead of
        leaving a hole at the new place."""
        return self.compact and self.moves(addr)

    def add_hole(self, start, end):
        """Leave the freed stock code [start, end) out of the move."""
        if self.runs is not None:
            raise RuntimeError("stock code freed after the compaction was finalized")
        if not (self.lo <= start and end <= self.hi):
            raise ValueError(f"0x{start:08X}-0x{end:08X} straddles the moved range")
        self.holes.append((start, end))

    def finalize(self, limit):
        """Fix where each kept run goes (back to back from ``dst``), reserve
        them, and free the rest up to ``limit`` (where our code starts)."""
        if self.runs is not None:
            return
        self.runs = compact_runs(self.lo, self.hi, self.dst, self.holes)
        s, e, d = self.runs[-1]
        end = d + e - s
        if end > limit:
            raise NotEnoughSpaceError(
                f"the compacted stock code ends at 0x{end:08X}, past our code "
                f"(0x{limit:08X})"
            )
        region = self.device.int_space
        region.reserve(
            self.dst - region.base,
            end - self.dst,
            self.device.name,
            "relocated stock code",
        )
        region.free(end - region.base, limit - region.base)

    def final(self, addr):
        """Where ``addr`` ends up; freed code compaction left out maps to
        where its gap was closed."""
        if not self.moves(addr):
            return addr
        if self.runs is None:
            raise RuntimeError("compacting relocation used before finalize()")
        return final_address(self.runs, addr)

    def freed(self, addr):
        """In stock code the patcher removed."""
        base = self.device.internal.FLASH_BASE
        return self.device.is_freed_int(addr - base)

    def int_delta(self, offset):
        """How far the internal image's stock ``offset`` moves within the
        image: if it's in the range and the destination is in the same image,
        else 0."""
        base = self.device.internal.FLASH_BASE
        if dst_in_bank2(self.dst) or not self.moves(base + offset):
            return 0
        return self.final(base + offset) - (base + offset)

    def _count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def _fix_abs(self, buf, off, kind):
        v = struct.unpack_from("<I", buf, off)[0]
        if self.moves(v & ~1):
            _pack("<I", buf, off, v + self.final(v & ~1) - (v & ~1))
            self._count(kind)

    def fix_ram_images(self):
        """Fix pointers in data that ends up compressed: rwdata blocks, SRAM3."""
        with writelog.step("Fix references for the relocated stock code"):
            self._fix_ram_images()

    def _fix_ram_images(self):
        rw = self.device.internal.rwdata
        for r in self.map.refs:
            kind, src = r["kind"], r["src"]
            if kind in ("ITCM literal", "DTCM .data"):
                for data, dst in zip(rw.datas, rw.dsts):
                    if dst <= src < dst + len(data):
                        self._fix_abs(data, src - dst, kind)
                        break
                else:
                    # Zelda copies its ITCM code out of external flash at boot.
                    ext = self._copy_source(src)
                    buf, off = (None, None) if ext is None else self.device.locate(ext)
                    if buf is None:
                        raise NotImplementedError(
                            f"{kind} at 0x{src:08X} isn't in an rwdata block"
                        )
                    self._fix_abs(buf, off, kind)
            elif kind in ("external literal", "external data pointer"):
                buf, off = self.device.locate(src)
                if buf is None:
                    raise NotImplementedError(f"can't locate {kind} at 0x{src:08X}")
                self._fix_abs(buf, off, kind)

    def _copy_source(self, addr):
        """External address that a boot-time ITCM/DTCM copy fills ``addr`` from."""
        layout = xrefs.LAYOUT[self.device.name]
        for src, dst, n in layout.get("itcm_copies", []) + layout.get(
            "dtcm_copies", []
        ):
            if dst <= addr < dst + n:
                return src + addr - dst
        return None

    def apply(self):
        d = self.device
        base = d.internal.FLASH_BASE
        cur = bytes(d.internal)
        lo, hi, final = self.lo, self.hi, self.final
        moved = bytearray(cur[lo - base : hi - base])
        prov = writelog.provenance(d.internal, lo - base, hi - base)

        def where(addr):
            if self.moves(addr):
                return moved, addr - lo
            return d.internal, addr - base

        # Current code in the range (patches changed some of it).
        code = Code(cur, base, lo + xrefs.VECTOR_TABLE_END, hi)
        # Freed stock code may hold data now. Live code can still branch there
        # behind a guard that's never taken (e.g. sleep_image_tick skips the
        # removed decoder when the images are deleted), so don't descend into it.
        # Code moved to external flash leaves only stubs (patches/xip.py).
        code.contains = lambda a: (
            code.lo <= a < code.hi and not self.freed(a) and not d.xip_hides(a)
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code.descend({a | 1 for a in self.map.code.entries if self.moves(a)})

        # Branches and PC-relative loads in the range: the descent's, and,
        # as now encoded, the stock map's in code it doesn't reach (only data
        # or freed code leads there). With one offset, branches within the
        # range need nothing; compaction needs every one.
        def kept(a):
            return self.moves(a) and not self.freed(a) and not d.xip_hides(a)

        branches = {src: (t, size) for src, t, mn, size in code.branches}
        for src, t, mn, size in self.map.code.branches:
            if src not in branches and kept(src):
                now = _branch_target(moved, src - lo, src, size)
                if now is not None:
                    branches[src] = (now, size)
        pcrel = {src: t for src, t, sz in code.pcrel}
        for src, t, sz in self.map.code.pcrel:
            if src not in pcrel and kept(src):
                now = _pcrel_target(moved, src - lo, src)
                if now is not None:
                    pcrel[src] = now

        # Branches out of the range (e.g. hooks into our code), and with
        # compaction, between runs.
        for src, (t, size) in sorted(branches.items()):
            if self.moves(t) and final(t) - final(src) == t - src:
                continue
            where_to = (
                "crosses a gap compaction closes"
                if self.moves(t)
                else "leaves the range"
            )
            if size != 4:
                raise NotImplementedError(f"16-bit branch at 0x{src:08X} {where_to}")
            o = src - lo
            hw1, hw2 = struct.unpack_from("<HH", moved, o)
            hw1, hw2 = _encode_branch(hw1, hw2, final(t) - (final(src) + 4))
            struct.pack_into("<HH", moved, o, hw1, hw2)
            self._count("branch between runs" if self.moves(t) else "branch out")
        for src, t in sorted(pcrel.items()):
            if not self.moves(t):
                raise NotImplementedError(
                    f"PC-relative access at 0x{src:08X} leaves the range"
                )
            if final(t) - final(src) != t - src:
                _encode_pcrel(moved, src - lo, final(t) - ((final(src) + 4) & ~3))
                self._count("pc-relative between runs")

        # Stock code left in place (the amalgam keeps Zelda's code past its
        # factory test): its branches into the range, as patched.
        def stays(a):
            return not self.moves(a) and not self.freed(a) and not d.xip_hides(a)

        for src, t, mn, size in self.map.code.branches:
            if not (stays(src) and self.moves(t)):
                continue
            if size != 4:
                raise NotImplementedError(
                    f"16-bit branch at 0x{src:08X} enters the range"
                )
            o = src - base
            hw1, hw2 = struct.unpack_from("<HH", d.internal, o)
            decoded = _decode_branch(hw1, hw2)
            if decoded is None or not self.moves(src + 4 + decoded[1]):
                continue  # A patch replaced it.
            hw1, hw2 = _encode_branch(hw1, hw2, final(src + 4 + decoded[1]) - (src + 4))
            _pack("<HH", d.internal, o, hw1, hw2)
            self._count("branch in")
        for src, t, sz in self.map.code.pcrel:
            if stays(src) and self.moves(t):
                raise NotImplementedError(
                    f"PC-relative access at 0x{src:08X} enters the range"
                )

        # Our code: branches into and pointers to the range. It reaches stock
        # code only through stock_<device>_* symbols, so the symbol tells what
        # moves, not the address: the amalgam's ELF also calls the other
        # firmware's stock code at the same addresses.
        stock_end = base + d.internal.STOCK_ROM_END
        for addr, rtype, name in elf_relocs(d):
            if not stock_end <= addr < base + len(d.internal):
                continue
            if not name.startswith(f"stock_{d.name}_"):
                continue
            o = addr - base
            if rtype in (R_ARM_THM_CALL, R_ARM_THM_JUMP24):
                hw1, hw2 = struct.unpack_from("<HH", d.internal, o)
                decoded = _decode_branch(hw1, hw2)
                if decoded is None:
                    continue  # ld turns calls to undefined weak symbols into nop.w
                kind, off = decoded
                t = addr + 4 + off
                if self.moves(t):
                    hw1, hw2 = _encode_branch(hw1, hw2, final(t) - (addr + 4))
                    _pack("<HH", d.internal, o, hw1, hw2)
                    self._count("our branch in")
            elif rtype == R_ARM_ABS32:
                self._fix_abs(d.internal, o, "our pointer")
        for o in d.int_pointers:
            self._fix_abs(d.internal, o, "patcher pointer")

        # Absolute words in internal flash.
        absolute = {
            r["src"]
            for r in self.map.refs
            if r["kind"] in ("vector", "literal", "pointer in flash data")
        }
        absolute |= {a for a, sz in code.literals().items() if sz == 4}
        pc_offsets = {
            r["src"]: r["base"]
            for r in self.map.refs
            if r["kind"] == "pc-offset literal"
        }
        absolute = {a for a in absolute if not self.freed(a)}
        for a in sorted(absolute - set(pc_offsets)):
            buf, o = where(a)
            self._fix_abs(buf, o, "absolute")

        # Self-relative words; the RWDATA table is the patcher's, read it as written.
        rw = d.internal.rwdata
        selfrel = {
            r["src"]: r["src"] for r in self.map.refs if "self-relative" in r["kind"]
        }
        table = base + rw.table_start
        selfrel = {a: a for a in selfrel if not table <= a < base + rw.table_end}
        for i in range(len(rw.datas)):
            for f in (0, 4):
                selfrel[table + 16 * i + f] = table + 16 * i + f
        for a in (base + rw.table_end - 8, base + rw.table_end - 4):
            selfrel[a] = a
        selfrel.update(pc_offsets)
        selfrel = {a: b for a, b in selfrel.items() if not self.freed(a)}
        for a, b in sorted(selfrel.items()):
            v = struct.unpack_from("<i", cur, a - base)[0]
            t = (b + v) & 0xFFFF_FFFF
            new_t = t + final(t & ~1) - (t & ~1)
            new_b = final(b)
            if new_t - new_b != v:
                buf, o = where(a)
                _pack("<i", buf, o, new_t - new_b)
                self._count("self-relative")

        # The bytes changed in the moved copy are this fix-up's, unless a
        # patch wrote them (e.g. a hook's bl, re-encoded).
        before = np.frombuffer(cur[lo - base : hi - base], dtype=np.uint8)
        changed = np.frombuffer(bytes(moved), dtype=np.uint8) != before
        prov[changed & (prov < 0)] = writelog.new()

        vt = xrefs.VECTOR_TABLE_END
        if lo == base and self.keep_vector_table:
            with writelog.untracked():
                d.internal[:vt] = moved[:vt]
            writelog.set_provenance(d.internal, 0, prov[:vt])

        if dst_in_bank2(self.dst):
            off = self.dst - BANK2_BASE
            out = d.bank2
            if len(out) < off + len(moved):
                out.extend(b"\xff" * (off + len(moved) - len(out)))
        else:
            off = self.dst - base
            out = d.internal
            if not self.compact:
                # Freed stock code moved with the rest, but its holes were
                # handed out at the new place (Device.free_int) and may hold
                # data now.
                for start, end in d.freed_int:
                    if self.moves(base + start):
                        s, e = start - (lo - base), end - (lo - base)
                        moved[s:e] = out[off + s : off + e]
                        prov[s:e] = writelog.provenance(out, off + s, off + e)
        if out is d.internal:
            # Each kept run (all of it unless compacted); what compaction
            # left out isn't copied.
            for s, e, new in self.runs:
                o = new - base
                with writelog.untracked():
                    out[o : o + e - s] = moved[s - lo : e - lo]
                writelog.set_provenance(out, o, prov[s - lo : e - lo])
        else:
            with writelog.untracked():
                out[off : off + len(moved)] = moved
        self.prov = prov

        # Old copy: udf, so anything still pointing here faults. The OTFDEC
        # key/nonce stay; the patcher encrypts external flash with them.
        start = max(lo, base + vt)
        keep = (base + d.internal.NONCE_OFFSET, base + d.internal.KEY_OFFSET + 16)
        with writelog.untracked():
            for a in range(start, hi, 2):
                if not keep[0] <= a < keep[1]:
                    d.internal[a - base : a - base + 2] = b"\xfe\xde"
        return self.counts


# 32-bit literal loads (U bit cleared): LDRB, LDRH, LDR, LDRSB, LDRSH with a
# 12-bit offset; LDRD and VLDR with an 8-bit word offset.
_LDR_IMM12 = (0xF81F, 0xF83F, 0xF85F, 0xF91F, 0xF93F)
_LDR_IMM8X4 = {0xE95F: 0xFF7F, 0xED1F: 0xFF3F}  # LDRD, VLDR: (pattern, mask)


def _branch_target(buf, o, addr, size):
    """Target of the branch at ``buf[o]`` (at ``addr``), or None if a patch
    made it something else."""
    if size == 4:
        hw1, hw2 = struct.unpack_from("<HH", buf, o)
        decoded = _decode_branch(hw1, hw2)
        return None if decoded is None else addr + 4 + decoded[1]
    hw = struct.unpack_from("<H", buf, o)[0]
    if hw & 0xF800 == 0xE000:  # B (T2)
        off = (hw & 0x7FF) << 1
        off -= (off & 0x800) << 1
    elif hw & 0xF000 == 0xD000 and (hw >> 8) & 0xF < 0xE:  # B<c> (T1)
        off = (hw & 0xFF) << 1
        off -= (off & 0x100) << 1
    elif hw & 0xF500 == 0xB100:  # CBZ, CBNZ
        off = ((hw >> 9) & 1) << 6 | ((hw >> 3) & 0x1F) << 1
    else:
        return None
    return addr + 4 + off


def _pcrel_target(buf, o, addr):
    """Address the PC-relative load or ``adr`` at ``buf[o]`` (at ``addr``)
    reaches, or None if it's something else (``_encode_pcrel``'s forms)."""
    pc = (addr + 4) & ~3
    hw1 = struct.unpack_from("<H", buf, o)[0]
    if hw1 & 0xF800 in (0x4800, 0xA000):
        return pc + (hw1 & 0xFF) * 4
    hw2 = struct.unpack_from("<H", buf, o + 2)[0]
    sign = 1 if hw1 & 0x80 else -1
    if hw1 & 0xFF7F in _LDR_IMM12:
        return pc + sign * (hw2 & 0xFFF)
    if any(hw1 & mask == pattern for pattern, mask in _LDR_IMM8X4.items()):
        return pc + sign * (hw2 & 0xFF) * 4
    if hw1 & 0xFBFF in (0xF20F, 0xF2AF):
        imm = ((hw1 >> 10) & 1) << 11 | ((hw2 >> 12) & 7) << 8 | (hw2 & 0xFF)
        return pc + (imm if hw1 & 0xFBFF == 0xF20F else -imm)
    return None


def _encode_pcrel(buf, o, offset):
    """Re-encode the PC-relative load or ``adr`` at ``buf[o]`` to reach
    ``offset`` bytes from Align(PC, 4)."""
    hw1 = struct.unpack_from("<H", buf, o)[0]
    if hw1 & 0xF800 in (0x4800, 0xA000):  # LDR (literal), ADR: 16-bit
        if offset % 4 or not 0 <= offset <= 1020:
            raise NotImplementedError(
                f"16-bit PC-relative offset {offset:#x} at {o:#x}"
            )
        struct.pack_into("<H", buf, o, (hw1 & 0xFF00) | offset // 4)
        return
    hw2 = struct.unpack_from("<H", buf, o + 2)[0]
    u = int(offset >= 0)
    mag = abs(offset)
    if hw1 & 0xFF7F in _LDR_IMM12:
        if mag > 0xFFF:
            raise NotImplementedError(f"PC-relative offset {offset:#x} at {o:#x}")
        hw1 = (hw1 & ~0x80) | (u << 7)
        hw2 = (hw2 & 0xF000) | mag
    elif any(hw1 & mask == pattern for pattern, mask in _LDR_IMM8X4.items()):
        if mag % 4 or mag > 1020:
            raise NotImplementedError(f"PC-relative offset {offset:#x} at {o:#x}")
        hw1 = (hw1 & ~0x80) | (u << 7)
        hw2 = (hw2 & 0xFF00) | mag // 4
    elif hw1 & 0xFBFF in (0xF20F, 0xF2AF):  # ADDW/SUBW Rd, PC, #imm12 (ADR.W)
        if mag > 0xFFF:
            raise NotImplementedError(f"PC-relative offset {offset:#x} at {o:#x}")
        hw1 = (0xF20F if u else 0xF2AF) | ((mag >> 11) & 1) << 10
        hw2 = (hw2 & 0x0F00) | ((mag >> 8) & 7) << 12 | (mag & 0xFF)
    else:
        raise NotImplementedError(f"unknown PC-relative instruction at {o:#x}")
    struct.pack_into("<HH", buf, o, hw1, hw2)


def compact_runs(lo, hi, dst, holes):
    """(start, end, new start) of each part of [lo, hi) outside ``holes``,
    back to back from ``dst``."""
    runs, pos, cur = [], dst, lo
    for start, end in sorted(holes):
        if start > cur:
            runs.append((cur, start, pos))
            pos += start - cur
        cur = max(cur, end)
    if cur < hi:
        runs.append((cur, hi, pos))
    return runs


def final_address(runs, addr):
    """Where ``addr`` goes with ``runs`` (``compact_runs``); an address in a
    left-out hole maps to where the hole was closed."""
    closed = runs[0][2]
    for start, end, new in runs:
        if addr < start:
            return closed
        if addr < end:
            return new + addr - start
        closed = new + end - start
    return closed


def elf_relocs(device):
    """(address, type, symbol name) of every relocation in our ELF that
    lands in flash."""
    elf = device.internal.elf
    load = [
        (s["p_vaddr"], s["p_paddr"], s["p_memsz"])
        for s in elf.iter_segments()
        if s["p_type"] == "PT_LOAD"
    ]
    for sec in elf.iter_sections():
        if not isinstance(sec, RelocationSection):
            continue
        target = elf.get_section(sec["sh_info"])
        if not target["sh_flags"] & 2:  # SHF_ALLOC
            continue
        symtab = elf.get_section(sec["sh_link"])
        for rel in sec.iter_relocations():
            vaddr = rel["r_offset"]
            name = symtab.get_symbol(rel["r_info_sym"]).name
            for v, p, n in load:  # .data relocations land in its flash copy
                if v <= vaddr < v + n:
                    yield p + vaddr - v, rel["r_info_type"], name
                    break


def _pack(fmt, buf, off, *values):
    """``struct.pack_into``, logged for the manifest (it bypasses
    ``Firmware.__setitem__``)."""
    struct.pack_into(fmt, buf, off, *values)
    if getattr(buf, "prov", None) is not None:
        writelog.record(buf, off, off + struct.calcsize(fmt), fixup=True)


def dst_in_bank2(addr):
    return BANK2_BASE <= addr < BANK2_END
