"""Map the stock internal-flash code and everything that references it.

Recursive-descent disassembly (capstone) of internal flash, seeded from the
vector table, the static-constructor and init tables, ``tools/re/<device>.syms``
and any Thumb pointer found along the way (iterated until nothing new turns
up). The ITCM code and the external-flash code are scanned the same way,
because they reach internal flash through absolute pointers.

Used by the patcher to relocate stock code (``patches/relocate.py``) and by
``tools/re/xrefs.py``, which prints the map.

Reference kinds (``Analysis.refs``):

* absolute words: ``vector``, ``literal``, ``pointer in flash data``,
  ``ITCM literal``, ``external literal``, ``external data pointer``,
  ``DTCM .data``; ``... (verify)`` kinds are pointer-like data words
  that are probably not pointers.
* self-relative words (target = word address + value): ``ctor``, ``init
  table`` and ``rwdata table`` entries.
* ``pc-offset literal``: ``ldr rX, [pc, #k]; add rX, pc; adds rX, #adj``;
  target = ``base`` + value, where ``base`` (= pc + adj) moves with the code.
"""

import bisect
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import capstone
from capstone import arm as A

from .compression import lz77_decompress
from .firmware import Device
from .syms import parse_syms, syms_path

REPO = Path(__file__).resolve().parents[1]


def extract_rwdata(device_cls, internal):
    """Decompress each stock RWDATA init block; returns ``[(dst_addr, data), ...]``."""
    start, length = device_cls.Int.RWDATA_OFFSET, device_cls.Int.RWDATA_LEN
    blocks = []
    for i in range(start, start + length - 4, 16):
        data_addr = i + 4 + struct.unpack_from("<I", internal, i + 4)[0]
        data_len = struct.unpack_from("<I", internal, i + 8)[0] >> 1
        dst = struct.unpack_from("<I", internal, i + 12)[0]
        blocks.append(
            (dst, lz77_decompress(internal[data_addr : data_addr + data_len]))
        )
    return blocks


INT_BASE = 0x0800_0000
EXT_BASE = 0x9000_0000
VECTOR_TABLE_END = 0x2CC

# Layout facts that aren't in the Device classes yet.
LAYOUT = {
    "mario": {
        "ctor_table": (0x0801_7E00, 0x0801_7E18),
        # Init-table entries before the RWDATA entries the patcher rewrites
        # (self-relative function offsets; 0x0801807c -> bss clear at 0x08001692).
        "init_entries": [0x0801_807C],
        # (external source, ITCM destination, length): game_reload copies the
        # start of external flash to ITCM 0 (the ITCM rwdata block follows it).
        "itcm_copies": [(0x9000_0000, 0x0000_0000, 0x1E5C)],
        # External-flash code (ends where the sleep images start).
        "ext_code": (0x900C_0000, 0x900C_58F8),
        # External-flash ROMs and media; pointer-like words in them are false positives.
        "ext_skip": [
            (0x9000_1E60, 0x9000_BE60),  # SMB1 ROM
            (0x9001_2D44, 0x9009_8B84),  # mario song
            (0x9009_8B84, 0x900A_8B84),  # clock tileset
            (0x900A_EC58, 0x900B_EC58),  # SMB2 ROM
            (0x900C_58F8, 0x900F_4D18),  # sleep images
            (
                0x900F_5858,
                0x900F_DF00,
            ),  # ITCM overlay game_reload copies 0 bytes of (dead)
        ],
    },
    "zelda": {
        "ctor_table": (0x0801_B0BC, 0x0801_B0D4),
        # Zero-init entry (0x0800306a) ahead of the RWDATA entry.
        "init_entries": [0x0801_B368],
        # itcm_overlay_load (0x0800f784) copies these once at boot; the code is
        # linked for ITCM (NES CPU core at 0, GB core at 0x2d58, menu UI at 0x9c7c).
        "itcm_copies": [
            (0x9030_C388, 0x0000_0000, 0x1E70),
            (0x9030_B4A0, 0x0000_1E70, 0xEE8),
            (0x9030_E7C8, 0x0000_2D58, 0x6F24),
            (0x9031_56EC, 0x0000_9C7C, 0x1B44),
        ],
        "dtcm_copies": [(0x9030_E1F8, 0x2000_AE88, 0x5D0)],
        # External-flash code that runs in place.
        "ext_code": (0x9031_7230, 0x9032_54A0),
        "ext_skip": [
            (0x9000_0000, 0x9002_0000),  # NVRAM banks and save slots
            (0x9002_0000, 0x9003_0000),  # sprites
            (0x9003_0000, 0x900D_2000),  # LoZ1/LoZ2 (EN/JP) ROMs and the timer data
            (0x900D_2000, 0x901F_4C00),  # Link's Awakening ROMs
            (0x901F_4C00, 0x9028_8120),  # 11 backdrop images
            # Unencrypted tail: empty, reset-cleared and LA scratch areas.
            (0x9032_54A0, 0x9040_0000),
        ],
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
        # (literal, base, adj): `ldr rX, [pc, #k]; add rX, pc[; adds rX, #adj]`;
        # target = base + value + adj (armcc picks adj = literal - base).
        self.pc_offsets = []

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
        last_cmp, it_left, pending, added = {}, 0, {}, None
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
            if added is not None:
                lit, base, reg = added
                adj = 0
                if (
                    mn == "adds"
                    and len(ops) == 2
                    and ops[0].reg == reg
                    and ops[1].type == A.ARM_OP_IMM
                ):
                    adj = ops[1].imm
                self.pc_offsets.append((lit, base, adj))
                added = None
            if (
                mn == "ldr"
                and len(ops) == 2
                and ops[1].type == A.ARM_OP_MEM
                and ops[1].mem.base == A.ARM_REG_PC
            ):
                pending[ops[0].reg] = ((a + 4) & ~3) + ops[1].mem.disp
            elif (
                mn == "add"
                and len(ops) == 2
                and ops[1].type == A.ARM_OP_REG
                and ops[1].reg == A.ARM_REG_PC
                and ops[0].reg in pending
            ):
                added = (pending.pop(ops[0].reg), a + 4, ops[0].reg)
            elif ops and ops[0].type == A.ARM_OP_REG:
                pending.pop(ops[0].reg, None)
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
                # capstone reports targets >= 0x80000000 (external flash) as negative.
                t = ops[-1].imm & 0xFFFF_FFFF
                self.branches.append((a, t, ins.mnemonic, ins.size))
                work.append(t)
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


@dataclass
class Analysis:
    internal: bytes  # stock internal flash
    external: bytes  # decrypted stock external flash
    code: "Code"
    cls: bytearray  # classify(code)
    refs: list  # {"kind", "src", "target"} dicts
    other: dict  # name -> Code for the ITCM and external code
    unreferenced: list  # push prologues nothing references
    layout: dict


def itcm_image(layout, external, rwdata):
    """ITCM as it is after boot: the external-flash blocks the firmware copies
    there plus the ITCM rwdata block."""
    blocks = [
        (dst, external[src - EXT_BASE : src - EXT_BASE + n])
        for src, dst, n in layout.get("itcm_copies", [])
    ]
    blocks += [(dst, data) for dst, data in rwdata if dst < 0x0001_0000]
    itcm = bytearray(max((dst + len(data) for dst, data in blocks), default=0))
    for dst, data in blocks:
        itcm[dst : dst + len(data)] = data
    return bytes(itcm)


def copy_sources(layout):
    """External-flash ranges the firmware copies to ITCM/DTCM."""
    return [
        (src, src + n)
        for src, _, n in layout.get("itcm_copies", []) + layout.get("dtcm_copies", [])
    ]


def analyze(device, internal=None, external=None, repo=REPO):
    """Analyze the stock firmware. ``internal``/``external`` default to the
    backups in ``repo``; ``external`` must be decrypted."""
    device_cls = Device.registry[device]
    layout = LAYOUT[device]
    if internal is None:
        internal = (repo / f"internal_flash_backup_{device}.bin").read_bytes()
    internal = bytes(internal)
    if external is None:
        key = internal[device_cls.Int.KEY_OFFSET : device_cls.Int.KEY_OFFSET + 16]
        nonce = internal[device_cls.Int.NONCE_OFFSET : device_cls.Int.NONCE_OFFSET + 8]
        ext_fw = device_cls.Ext(repo / f"flash_backup_{device}.bin")
        ext_fw.crypt(key, nonce)
        external = ext_fw
    external = bytes(external)
    rwdata = extract_rwdata(device_cls, internal)
    rwdata_table = INT_BASE + device_cls.Int.RWDATA_OFFSET
    code_end = rwdata_table

    def iword(a):
        return u32(internal, INT_BASE, a)

    def in_int(a):
        return INT_BASE + VECTOR_TABLE_END <= a < code_end

    image_end = INT_BASE + len(internal.rstrip(b"\xff"))

    def in_image(a):
        # The used image, plus the bank end (stock compares the image end against it).
        return INT_BASE <= a <= image_end or a == INT_BASE + len(internal)

    itcm = itcm_image(layout, external, rwdata)
    dtcm = [(dst, data) for dst, data in rwdata if dst >= 0x2000_0000]
    dtcm += [
        (dst, external[src - EXT_BASE : src - EXT_BASE + n])
        for src, dst, n in layout.get("dtcm_copies", [])
    ]

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
    for a in layout["init_entries"]:
        t = (a + struct.unpack_from("<i", internal, a - INT_BASE)[0]) & 0xFFFF_FFFF
        seeds.add(t)
        refs.append({"kind": "init table (self-relative)", "src": a, "target": t})
    syms, _ = parse_syms(syms_path(device))
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
                if in_image(v):
                    if v & 1 and in_int(v & ~1):
                        seeds.add(v)
                    refs.append({"kind": f"{name} literal", "src": lit, "target": v})
        for src, t, _, _ in c.branches:
            if in_int(t):
                refs.append({"kind": f"{name} direct branch", "src": src, "target": t})
    # Remaining external data (e.g. the NES emulator's handler table at 0x900a9ce4).
    covered = set()
    for c in other.values():
        for a, (sz, _, _) in c.insns.items():
            covered.update(range(a, a + sz))
        for lit, sz in c.literals().items():
            covered.update(range(lit, lit + sz))
    skip = layout["ext_skip"] + [layout["ext_code"]] + copy_sources(layout)
    candidates = {}
    for o in range(0, len(external) - 3, 4):
        a = EXT_BASE + o
        v = struct.unpack_from("<I", external, o)[0]
        if (
            in_image(v)
            and a not in covered
            and not any(lo <= a < hi for lo, hi in skip)
        ):
            candidates[a] = v
    code_ptrs = {a for a, v in candidates.items() if v & 1 and in_int(v & ~1)}
    # Isolated pointer-like words are usually code or pixel bytes; tables come in runs.
    # Accept runs, then grow to pointers whose targets cluster with accepted ones.
    accepted = {
        a
        for a in code_ptrs
        if any(b in code_ptrs for b in range(a - 32, a + 33, 4) if b != a)
    }
    while True:
        near = {
            a
            for a in code_ptrs - accepted
            if any(abs(candidates[a] - candidates[b]) <= 0x100 for b in accepted)
        }
        if not near:
            break
        accepted |= near
    for a, v in sorted(candidates.items()):
        if a in accepted:
            seeds.add(v)
            refs.append({"kind": "external data pointer", "src": a, "target": v})
        else:
            refs.append({"kind": "external data word (verify)", "src": a, "target": v})
    for dst, data in dtcm:
        for o in range(0, len(data) - 3, 4):
            v = struct.unpack_from("<I", data, o)[0]
            if in_image(v):
                if v & 1 and in_int(v & ~1):
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
    pc_offset_lits = {lit for lit, _, _ in code.pc_offsets}
    for lit, base, adj in code.pc_offsets:
        v = struct.unpack_from("<i", internal, lit - INT_BASE)[0]
        refs.append(
            {
                "kind": "pc-offset literal",
                "src": lit,
                "target": (base + v + adj) & 0xFFFF_FFFF,
                "base": base + adj,
            }
        )
    for lit, sz in code.literals().items():
        v = iword(lit) if sz == 4 else 0
        if in_image(v) and lit not in pc_offset_lits:
            refs.append({"kind": "literal", "src": lit, "target": v})
    for lo, hi in unknown_runs(cls, code.lo):
        for o in range((lo + 3) & ~3, hi - 3, 4):
            v = iword(o)
            if v & 1 and (v & ~1) in code.insns:
                refs.append({"kind": "pointer in flash data", "src": o, "target": v})
            elif in_image(v):
                refs.append(
                    {
                        "kind": "data pointer in flash data (verify)",
                        "src": o,
                        "target": v,
                    }
                )

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
    return Analysis(internal, external, code, cls, refs, other, unreferenced, layout)


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
