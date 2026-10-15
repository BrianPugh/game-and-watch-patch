#!/usr/bin/env python3
"""Map the stock internal-flash code and everything that references it.

Recursive-descent disassembly (capstone) of internal flash, seeded from the
vector table, the static-constructor table, ``tools/re/<device>.syms`` and any
Thumb pointer found along the way (iterated until nothing new turns up). The
ITCM code and the external-flash code are scanned the same way, because they
reach internal flash through absolute pointers.

It reports what a relocator of internal code has to know:

* Byte classification: code, literal pools, jump tables, everything else.
* Relocatable units. armcc shares literal pools across a whole module, so
  functions that load each other's literals, share a 16-bit branch, fall
  through into each other or share a jump table must move together. Branches
  between units are all 32-bit (BL/B.W), so they can be re-encoded in place.
* Every absolute or self-relative reference into internal code: vector table,
  static constructors, the RWDATA table, literal pools, pointer tables in
  flash data, ITCM and external-flash veneers, and DTCM ``.data``.

Usage:
    python tools/re/xrefs.py --device=mario [--units] [--json build/re/mario_xrefs.json]
"""

import argparse
import bisect
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import capstone
from capstone import arm as A

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches import Device  # noqa: E402
from tools.re.build_elf import extract_rwdata, parse_syms  # noqa: E402

INT_BASE = 0x0800_0000
EXT_BASE = 0x9000_0000
VECTOR_TABLE_END = 0x2CC

# Layout facts that aren't in the Device classes yet.
LAYOUT = {
    "mario": {
        "ctor_table": (0x0801_7E00, 0x0801_7E18),
        # game_reload copies this much of external flash to ITCM 0.
        "itcm_from_ext": 0x1E5C,
        # External-flash code (ends where the sleep images start).
        "ext_code": (0x900C_0000, 0x900C_58F8),
    },
}

CONDS = {
    "eq",
    "ne",
    "cs",
    "hs",
    "cc",
    "lo",
    "mi",
    "pl",
    "vs",
    "vc",
    "hi",
    "ls",
    "ge",
    "lt",
    "gt",
    "le",
}

_md = capstone.Cs(
    capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS
)
_md.detail = True


def u32(mem, base, addr):
    return struct.unpack_from("<I", mem, addr - base)[0]


def _load_size(ins):
    m = ins.mnemonic.split(".")[0]
    if m.startswith("ldrd"):
        return 8
    if m.startswith(("ldrh", "ldrsh")):
        return 2
    if m.startswith(("ldrb", "ldrsb")):
        return 1
    if m == "vldr":
        return 8 if A.ARM_REG_D0 <= ins.operands[0].reg <= A.ARM_REG_D31 else 4
    return 4


class Code:
    """Recursive-descent disassembly of one Thumb region."""

    def __init__(self, mem, base, lo, hi):
        self.mem, self.base, self.lo, self.hi = mem, base, lo, hi
        self.insns = {}  # addr -> (size, mnemonic, op_str)
        self.branches = []  # (src, dst, mnemonic, size)
        self.pcrel = []  # (src, addr, load size; 0 for adr / add rX, pc)
        self.tables = []  # (src, start, end) of tbb/tbh tables
        self.entries = set()
        self.unbounded_tables = []

    def contains(self, a):
        return self.lo <= a < self.hi

    def descend(self, seeds):
        work = [a & ~1 for a in seeds if self.contains(a & ~1)]
        self.entries.update(work)
        done = set()
        while work:
            a = work.pop()
            if a in done:
                continue
            done.add(a)
            self._run(a, work)

    def _run(self, a, work):
        last_cmp, it_left = {}, 0
        while self.contains(a) and a not in self.insns:
            ins = next(
                _md.disasm(self.mem[a - self.base : a - self.base + 4], a, 1), None
            )
            if ins is None:
                return
            self.insns[a] = (ins.size, ins.mnemonic, ins.op_str)
            ops, nxt = ins.operands, a + ins.size
            mn = ins.mnemonic.split(".")[0]
            if mn[0] == "b" and mn[1:] in CONDS:
                mn = "bcc"
            # Decoding one instruction at a time loses capstone's IT state; track it here.
            cond = mn == "bcc" or it_left > 0
            if it_left:
                it_left -= 1
            if mn.startswith("it"):
                it_left = len(mn) - 1

            for op in ops:
                if (
                    op.type == A.ARM_OP_MEM
                    and op.mem.base == A.ARM_REG_PC
                    and op.mem.index == 0
                ):
                    self.pcrel.append(
                        (a, ((a + 4) & ~3) + op.mem.disp, _load_size(ins))
                    )
            if mn == "adr" or (
                mn in ("add", "addw", "sub", "subw")
                and len(ops) == 3
                and ops[1].type == A.ARM_OP_REG
                and ops[1].reg == A.ARM_REG_PC
                and ops[2].type == A.ARM_OP_IMM
            ):
                imm = -ops[-1].imm if mn.startswith("sub") else ops[-1].imm
                self.pcrel.append((a, ((a + 4) & ~3) + imm, 0))
            if mn == "cmp" and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
                last_cmp[ops[0].reg] = ops[1].imm

            if mn in ("tbb", "tbh"):
                n = last_cmp.get(ops[0].mem.index)
                start = a + 4
                if n is None:
                    self.unbounded_tables.append(a)
                    return
                esz = 1 if mn == "tbb" else 2
                self.tables.append((a, start, start + esz * (n + 1)))
                for i in range(n + 1):
                    o = start - self.base + esz * i
                    off = (
                        self.mem[o]
                        if esz == 1
                        else struct.unpack_from("<H", self.mem, o)[0]
                    )
                    work.append(start + 2 * off)
                return
            if (
                mn in ("b", "bcc", "bl", "cbz", "cbnz")
                and ops
                and ops[-1].type == A.ARM_OP_IMM
            ):
                self.branches.append((a, ops[-1].imm, ins.mnemonic, ins.size))
                work.append(ops[-1].imm)
                if mn == "b" and not cond:
                    return
                a = nxt
                continue
            writes_pc = (
                ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == A.ARM_REG_PC
            )
            writes_pc = writes_pc and (mn.startswith("ldr") or mn in ("mov", "add"))
            writes_pc = writes_pc or (
                mn in ("pop", "ldm", "ldmia", "ldmfd") and "pc" in ins.op_str
            )
            if (mn == "bx" or writes_pc or mn == "udf") and not cond:
                return
            a = nxt

    def literals(self):
        """{addr: size} of words loaded PC-relatively from inside this region."""
        out = {}
        for _, lit, sz in self.pcrel:
            if sz and self.contains(lit):
                out[lit] = max(out.get(lit, 0), sz)
        return out


def classify(code):
    """bytearray over [lo, hi): 0 unknown, 1 code, 2 literal, 3 jump table."""
    cls = bytearray(code.hi - code.lo)
    for a, (sz, _, _) in code.insns.items():
        cls[a - code.lo : a - code.lo + sz] = b"\x01" * sz
    for _, s, e in code.tables:
        cls[s - code.lo : e - code.lo] = b"\x03" * (e - s)
    for lit, sz in code.literals().items():
        cls[lit - code.lo : lit - code.lo + sz] = b"\x02" * sz
    return cls


def unknown_runs(cls, lo):
    runs, i = [], 0
    while i < len(cls):
        if cls[i]:
            i += 1
            continue
        j = i
        while j < len(cls) and not cls[j]:
            j += 1
        runs.append((lo + i, lo + j))
        i = j
    return runs


def analyze(device):
    device_cls = Device.registry[device]
    layout = LAYOUT[device]
    internal = (REPO / f"internal_flash_backup_{device}.bin").read_bytes()
    key = internal[device_cls.Int.KEY_OFFSET : device_cls.Int.KEY_OFFSET + 16]
    nonce = internal[device_cls.Int.NONCE_OFFSET : device_cls.Int.NONCE_OFFSET + 8]
    ext_fw = device_cls.Ext(REPO / f"flash_backup_{device}.bin")
    ext_fw.crypt(key, nonce)
    external = bytes(ext_fw)
    rwdata = extract_rwdata(device_cls, internal)
    rwdata_table = INT_BASE + device_cls.Int.RWDATA_OFFSET
    code_end = rwdata_table

    def iword(a):
        return u32(internal, INT_BASE, a)

    def in_int(a):
        return INT_BASE + VECTOR_TABLE_END <= a < code_end

    # ITCM image: external-flash prefix plus the ITCM rwdata block.
    itcm_dst, itcm_data = next((dst, data) for dst, data in rwdata if dst < 0x0001_0000)
    itcm = bytearray(itcm_dst + len(itcm_data))
    itcm[: layout["itcm_from_ext"]] = external[: layout["itcm_from_ext"]]
    itcm[itcm_dst:] = itcm_data
    dtcm = [(dst, data) for dst, data in rwdata if dst >= 0x2000_0000]

    refs = []  # dicts: kind, src, target (+ extra)

    seeds = set()
    for o in range(4, VECTOR_TABLE_END, 4):
        v = iword(INT_BASE + o)
        if in_int(v & ~1):
            seeds.add(v)
            refs.append({"kind": "vector", "src": INT_BASE + o, "target": v})
    for a in range(*layout["ctor_table"], 4):
        t = (a + struct.unpack_from("<i", internal, a - INT_BASE)[0]) & 0xFFFF_FFFF
        seeds.add(t)
        refs.append({"kind": "ctor (self-relative)", "src": a, "target": t})
    for i in range(rwdata_table, rwdata_table + device_cls.Int.RWDATA_LEN - 4, 16):
        t = (i + struct.unpack_from("<i", internal, i - INT_BASE)[0]) & 0xFFFF_FFFF
        seeds.add(t)
        refs.append({"kind": "rwdata table (self-relative)", "src": i, "target": t})
    syms, _ = parse_syms(REPO / "tools" / "re" / f"{device}.syms")
    seeds |= {a | 1 for a in syms if in_int(a)}

    # Other code regions first: their pointers into internal flash are seeds too.
    ext_lo, ext_hi = layout["ext_code"]
    other = {
        "ITCM": Code(bytes(itcm), 0, 0, len(itcm)),
        "external": Code(external, EXT_BASE, ext_lo, ext_hi),
    }
    int_words = [iword(INT_BASE + o) for o in range(0, code_end - INT_BASE, 4)]
    itcm_words = [u32(itcm, 0, o) for o in range(0, len(itcm) - 3, 2)]
    ext_veneers = {
        a | 1
        for a in range(ext_lo, ext_hi - 8, 2)
        if external[a - EXT_BASE : a - EXT_BASE + 4]
        == b"\xdf\xf8\x00\xf0"  # ldr.w pc, [pc, #0]
    }
    for name, c in other.items():
        c.descend(
            {w for w in int_words + itcm_words if w & 1 and c.contains(w & ~1)}
            | {a | 1 for a in syms if c.contains(a)}
            | ext_veneers
        )
        for src, lit, sz in c.pcrel:
            if sz == 4 and c.contains(lit):
                v = u32(c.mem, c.base, lit)
                if in_int(v & ~1):
                    seeds.add(v)
                    refs.append({"kind": f"{name} literal", "src": lit, "target": v})
        for src, t, _, _ in c.branches:
            if in_int(t):
                refs.append({"kind": f"{name} direct branch", "src": src, "target": t})
    for dst, data in dtcm:
        for o in range(0, len(data) - 3, 4):
            v = struct.unpack_from("<I", data, o)[0]
            if in_int(v & ~1):
                seeds.add(v)
                refs.append({"kind": "DTCM .data", "src": dst + o, "target": v})

    # Internal flash, iterated: Thumb pointers in literals or unknown bytes become seeds.
    code = Code(internal, INT_BASE, INT_BASE + VECTOR_TABLE_END, code_end)
    while True:
        code.descend(seeds)
        cls = classify(code)
        new = set()
        lits = code.literals()
        for lit, sz in lits.items():
            v = iword(lit) if sz == 4 else 0
            if v & 1 and in_int(v & ~1) and (v & ~1) not in code.insns:
                new.add(v)
        for lo, hi in unknown_runs(cls, code.lo):
            words = {o: iword(o) for o in range((lo + 3) & ~3, hi - 3, 4)}
            ptr = {o for o, v in words.items() if v & 1 and in_int(v & ~1)}
            for o in ptr:
                v = words[o]
                # A lone word must land on a prologue; one in a run of pointers is a table entry.
                table = o - 4 in ptr or o + 4 in ptr
                if (v & ~1) not in code.insns and (
                    table or is_prologue(internal, v & ~1)
                ):
                    new.add(v)
        if not new:
            break
        seeds |= new

    cls = classify(code)
    for lit, sz in code.literals().items():
        v = iword(lit) if sz == 4 else 0
        if INT_BASE <= v < INT_BASE + len(internal):
            refs.append({"kind": "literal", "src": lit, "target": v})
    for lo, hi in unknown_runs(cls, code.lo):
        for o in range((lo + 3) & ~3, hi - 3, 4):
            v = iword(o)
            if v & 1 and (v & ~1) in code.insns:
                refs.append({"kind": "pointer in flash data", "src": o, "target": v})

    unreferenced = []
    ctor_lo, ctor_hi = layout["ctor_table"]
    for lo, hi in unknown_runs(cls, code.lo):
        for a in range(lo, hi - 1, 2):
            if ctor_lo <= a < ctor_hi:
                continue
            if (
                is_prologue(internal, a)
                and struct.unpack_from("<H", internal, a - INT_BASE)[0] != 0xB400
            ):
                unreferenced.append(a)
                break
    return internal, code, cls, refs, other, unreferenced


def is_prologue(mem, addr):
    hw = struct.unpack_from("<H", mem, addr - INT_BASE)[0]
    return (hw & 0xFF00) == 0xB500 or hw in (0xE92D, 0xB40C) or (hw & 0xFF00) == 0xB400


def units(code):
    """Contiguous [lo, hi) spans that must move together."""
    starts = sorted(
        code.entries
        | {t for _, t, m, _ in code.branches if m.startswith("bl") and code.contains(t)}
    )
    start_set = set(starts)

    def block(a):
        return starts[bisect.bisect_right(starts, a) - 1]

    parent = {s: s for s in starts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for src, t, _ in code.pcrel:
        if code.contains(t) and block(src) != block(t):
            union(block(src), block(t))
    for src, t, _, size in code.branches:
        if size == 2 and code.contains(t) and block(src) != block(t):
            union(block(src), block(t))
    for a, (sz, mn, op_str) in code.insns.items():
        nxt, m = a + sz, mn.split(".")[0]
        if (
            nxt in start_set
            and block(a) != nxt
            and not (m in ("b", "bx", "udf") or "pc" in op_str)
        ):
            union(block(a), nxt)
    for src, s, e in code.tables:
        union(block(src), block(e - 1))

    groups = defaultdict(list)
    for s in starts:
        groups[find(s)].append(s)
    spans = []
    for members in groups.values():
        i = bisect.bisect_right(starts, max(members))
        spans.append([min(members), starts[i] if i < len(starts) else code.hi])
    spans.sort()
    merged = []
    for lo, hi in spans:  # interleaved groups become one span
        if merged and lo < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--device", required=True, choices=sorted(LAYOUT))
    parser.add_argument(
        "--units", action="store_true", help="List every relocatable unit."
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Write units and references here (keep it under build/).",
    )
    args = parser.parse_args()

    internal, code, cls, refs, other, unreferenced = analyze(args.device)
    c = Counter(cls)
    print(
        f"internal 0x{code.lo:08X}-0x{code.hi:08X}: code {c[1]} B, literals {c[2]} B, "
        f"jump tables {c[3]} B, other {c[0]} B; {len(code.entries)} entries"
    )
    if code.unbounded_tables:
        print(
            "  tbb/tbh without a bound (targets not followed):",
            [hex(a) for a in code.unbounded_tables],
        )
    for name, o in other.items():
        print(f"{name} code 0x{o.lo:08X}-0x{o.hi:08X}: {len(o.insns)} instructions")

    spans = units(code)
    sizes = sorted((hi - lo for lo, hi in spans), reverse=True)
    print(
        f"\n{len(spans)} relocatable units: largest {sizes[0]} B, median {sizes[len(sizes) // 2]} B, "
        f"{sum(s > 1024 for s in sizes)} over 1 KB"
    )
    unit_lo = [lo for lo, _ in spans]

    def unit(a):
        i = bisect.bisect_right(unit_lo, a) - 1
        return i if i >= 0 and a < spans[i][1] else None

    cross = Counter()
    for src, t, mn, size in code.branches:
        if code.contains(t) and unit(src) != unit(t):
            cross[f"{mn} ({size} B)"] += 1
    print("branches between units (fix by re-encoding):", dict(cross))
    narrow = [k for k in cross if k.endswith("(2 B)")]
    if narrow:
        print("  WARNING: 16-bit branches between units:", narrow)

    print("\nabsolute / self-relative references into internal code:")
    for kind, n in Counter(r["kind"] for r in refs).most_common():
        print(f"  {kind:32} {n}")
    print(
        f"\nunreferenced push prologues in non-code bytes (dead, or reached some way not modeled): "
        f"{len(unreferenced)}",
        [hex(a) for a in unreferenced],
    )
    if args.units:
        print()
        for lo, hi in spans:
            print(f"  0x{lo:08X}-0x{hi:08X} {hi - lo:6} B")
    if args.json:
        args.json.write_text(
            json.dumps(
                {"units": spans, "refs": refs, "unreferenced": unreferenced}, indent=1
            )
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
