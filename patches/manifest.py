"""The patch manifest and the patched ELF (``build/manifest.txt``, ``build/patched.elf``).

The manifest lists every byte the patcher changed in stock code and data: each
stock byte that's still in the output (internal code, wherever relocation put
it; kept external data; the RAM images stock copies at boot) is compared with
the stock firmware, and each run of changes is named after its writer
(``patches/writelog.py``: the patch step and source line). Being a diff, it's
complete; data the patcher moves or compresses is in ``build/layout.txt``.

The patched ELF maps the output images at their run addresses with our
symbols and every stock symbol, as ``stock_<device>_<name>`` (the names our C
code uses) at its final address, for gdb and disassemblers. For our C code's
source lines (``--debug``), also load ``build/gw_patch.elf`` (our code doesn't
move).
"""

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import syms, writelog
from .elf_image import in_ram, link, toolchain_prefix
from .layout import BANK2_BASE
from .xrefs import EXT_BASE, INT_BASE, VECTOR_TABLE_END, extract_rwdata

# Bytes shown of each side of a change.
SHOW = 16


@dataclass
class View:
    """Stock bytes in the output: ``final`` at ``addr`` was ``stock`` at ``stock_addr``."""

    addr: int
    stock_addr: int
    final: np.ndarray
    stock: np.ndarray
    prov: np.ndarray | None
    # Reason for changed bytes nothing logged (e.g. pointers in RAM images).
    default: str = "(unattributed)"
    # [start, end) offsets to leave out (each has a summary line instead).
    skip: list = field(default_factory=list)


@dataclass
class Entry:
    addr: int
    stock_addr: int
    old: bytes
    new: bytes
    reason: str


def _u8(data):
    return np.frombuffer(bytes(data), dtype=np.uint8)


def _reason(view, owner):
    return view.default if owner < 0 else writelog.WRITES[owner].reason


def diff(view):
    """The view's changes, as ``Entry``s. Runs of changes with the same writer
    merge across up to 3 unchanged bytes (an instruction's unchanged byte)."""
    n = min(len(view.final), len(view.stock))
    changed = view.final[:n] != view.stock[:n]
    for s, e in view.skip:
        changed[max(s, 0) : max(e, 0)] = False
    owner = np.full(n, -1, dtype=np.int32)
    if view.prov is not None:
        m = min(n, len(view.prov))
        owner[:m] = view.prov[:m]
    runs = []
    for i in np.flatnonzero(changed).tolist():
        o = int(owner[i])
        if runs and i - runs[-1][1] <= 3 and runs[-1][2] == o:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, o])
    out = []
    for s, e, o in runs:
        out.append(
            Entry(
                view.addr + s,
                view.stock_addr + s,
                bytes(view.stock[s:e]),
                bytes(view.final[s:e]),
                _reason(view, o),
            )
        )
    # Bytes past the stock data (e.g. veneers appended to a RAM image).
    if len(view.final) > n:
        o = int(view.prov[n]) if view.prov is not None and len(view.prov) > n else -1
        out.append(
            Entry(
                view.addr + n,
                view.stock_addr + n,
                b"",
                bytes(view.final[n:]),
                _reason(view, o),
            )
        )
    return out


class DeviceImages:
    """One device's stock data in the output images."""

    def __init__(self, device, internal, bank2, external):
        self.d = device
        self.internal = _u8(internal)
        self.bank2 = _u8(bank2 or b"")
        # The external image as the device left it (unencrypted).
        self.external = _u8(external)
        self.int_prov = writelog.provenance(device.internal, 0, len(device.internal))
        self.ext_prov = writelog.provenance(device.external, 0, len(device.external))
        # Named stock symbols, and every other function the xrefs scan found
        # (so a name doesn't run on into the next, unnamed, function).
        m = device.stock_map
        table = {
            a & ~1: f"sub_{a & ~1:08x}"
            for code in (m.code, *m.other.values())
            for a in code.entries
        }
        funcs, datas = syms.parse_syms(syms.syms_path(device.name))
        table.update(datas)
        table.update(funcs)
        self.syms = sorted(table.items())
        self._sym_addrs = [a for a, _ in self.syms]

    def symbol(self, addr):
        """``name+0x..`` of the stock symbol at or before stock ``addr``."""
        i = bisect_right(self._sym_addrs, addr) - 1
        if i < 0:
            return ""
        a, name = self.syms[i]
        # Symbols carry no size; don't name something far past one.
        if addr - a > 0x1000 or (addr ^ a) & 0xFF00_0000:
            return ""
        return name if addr == a else f"{name}+0x{addr - a:x}"

    # Internal flash -----------------------------------------------------------

    def int_views(self):
        """Views and summary lines of the stock internal code."""
        d = self.d
        stock = _u8(d.internal_stock)
        end = d.internal.STOCK_ROM_END
        r = d.relocation
        views, summaries = [], []

        def view(lo, hi, addr, final, prov, default="(unattributed)"):
            views.append(
                View(
                    addr,
                    INT_BASE + lo,
                    final,
                    stock[lo:hi],
                    prov,
                    default,
                    skip=[(s - lo, e - lo) for s, e in d.freed_int],
                )
            )

        def in_place(lo, hi):
            view(lo, hi, INT_BASE + lo, self.internal[lo:hi], self.int_prov[lo:hi])

        if r is None:
            in_place(0, end)
        else:
            lo, hi = r.lo - INT_BASE, r.hi - INT_BASE
            if lo == 0 and r.keep_vector_table:
                in_place(0, VECTOR_TABLE_END)
            if r.dst >= BANK2_BASE:
                o = r.dst - BANK2_BASE
                final = self.bank2[o : o + hi - lo]
            else:
                o = r.dst - INT_BASE
                final = self.internal[o : o + hi - lo]
            view(
                lo,
                hi,
                r.dst,
                final,
                r.prov,
                default="Fix references for the relocated stock code",
            )
            if hi < end:
                in_place(hi, end)
            if not d.amalgam:
                start = max(r.lo, INT_BASE + VECTOR_TABLE_END)
                summaries.append(
                    (
                        start,
                        r.hi - start,
                        start,
                        f"stock code moved to 0x{r.dst:08X}; the old copy is udf "
                        f"(but the OTFDEC key/nonce)",
                    )
                )
        for (s, e), (fs, _) in zip(d.freed_int, d.freed_int_ranges()):
            summaries.append(
                (
                    INT_BASE + fs,
                    e - s,
                    INT_BASE + s,
                    "freed stock code: udf, our code or data (layout.txt)",
                )
            )
        return views, summaries

    # External flash -----------------------------------------------------------

    def ext_views(self):
        d = self.d
        stock = _u8(d.external_stock)
        compressed = d.external.compressed
        views, summaries = [], []
        for so, fo, n, packed in d.ext_space.kept:
            if packed:
                summaries.append(
                    (EXT_BASE + fo, n, EXT_BASE + so, "repacked (layout.txt)")
                )
                continue
            skip = []
            for co, cn in compressed:
                s, e = max(co, so), min(co + cn, so + n)
                if s < e:
                    skip.append((s - so, e - so))
                    summaries.append(
                        (
                            EXT_BASE + fo + s - so,
                            e - s,
                            EXT_BASE + s,
                            "LZMA compressed in place (layout.txt)",
                        )
                    )
            views.append(
                View(
                    EXT_BASE + fo,
                    EXT_BASE + so,
                    self.external[fo : fo + n],
                    stock[so : so + n],
                    self.ext_prov[fo : fo + n],
                    skip=skip,
                )
            )
        # Stock code run from external flash (patches/xip.py).
        int_stock = _u8(d.internal_stock)
        for u in d.xip_units:
            fo, n = u.ext_addr - EXT_BASE, u.hi - u.lo
            views.append(
                View(
                    u.ext_addr,
                    u.lo,
                    self.external[fo : fo + n],
                    int_stock[u.lo - INT_BASE : u.hi - INT_BASE],
                    u.ext_prov,
                )
            )
        return views, summaries

    # RAM images ---------------------------------------------------------------

    def ram_blocks(self):
        """``(run address, data, stock data or None, prov, label)`` of every
        block the firmware copies into RAM at boot."""
        d = self.d
        out = []
        if d.internal.rwdata is not None:
            stock = dict(extract_rwdata(type(d), d.internal_stock))
            for dst, data in zip(d.internal.rwdata.dsts, d.internal.rwdata.datas):
                out.append((dst, bytes(data), stock.get(dst), None, "rwdata"))
        for img in d.ram_images:
            out.append((img.run, img.data, img.stock, img.prov, img.label))
        return out

    def ram_views(self):
        views = []
        for run, data, stock, prov, _ in self.ram_blocks():
            if stock is None:
                continue  # Not stock's (the SRAM3 image).
            views.append(
                View(
                    run,
                    run,
                    _u8(data),
                    _u8(stock),
                    prov,
                    default="Pointer fixups (fix_ext_refs, relocation, rwdata_erase)",
                )
            )
        return views

    # Stock addresses ----------------------------------------------------------

    def final_address(self, addr):
        """Where stock ``addr`` (and what was there) is in the output, or None."""
        d = self.d
        if INT_BASE <= addr < INT_BASE + len(d.internal_stock):
            off = addr - INT_BASE
            for u in d.xip_units:
                if u.lo <= addr < u.hi:
                    new = u.final_address(addr)
                    if new != addr:
                        return new
            if off >= d.internal.STOCK_ROM_END or d.is_freed_int(off):
                return None
            if d.relocation and d.relocation.moves(addr):
                return addr + d.relocation.delta
            return addr
        if EXT_BASE <= addr < EXT_BASE + len(d.external_stock):
            off = addr - EXT_BASE
            if any(co <= off < co + cn for co, cn in d.external.compressed):
                return None
            for so, fo, n, packed in d.ext_space.kept:
                if so <= off < so + n:
                    if packed and off != so:
                        return None
                    return EXT_BASE + fo + off - so
            new = d.lookup.get(addr)
            # Moved within external flash: only to the overlay, compressed now.
            if new is None or EXT_BASE <= new < EXT_BASE + 0x1000_0000:
                return None
            return new
        return addr


def entries(images):
    """``(title, DeviceImages, [Entry])`` of each group of views, summaries included."""
    out = []
    for img in images:
        int_views, int_summaries = img.int_views()
        ext_views, ext_summaries = img.ext_views()
        groups = [
            (f"{img.d.name} internal flash", int_views, int_summaries),
            (f"{img.d.name} external flash", ext_views, ext_summaries),
            (f"{img.d.name} RAM images", img.ram_views(), []),
        ]
        for title, views, summaries in groups:
            rows = [e for v in views for e in diff(v)]
            rows += [
                Entry(a, stock, b"", b"", f"[{n} bytes] {why}")
                for a, n, stock, why in summaries
            ]
            rows.sort(key=lambda e: e.addr)
            out.append((title, img, rows))
    return out


def _hex(data):
    if len(data) <= SHOW:
        return data.hex()
    return data[:SHOW].hex() + ".."


def manifest_lines(images):
    groups = entries(images)
    n = sum(len(rows) for _, _, rows in groups)
    unattributed = sum(
        1 for _, _, rows in groups for e in rows if e.reason == "(unattributed)"
    )
    lines = [
        "# Every change to stock code and data in the output (patches/manifest.py).",
        "# address  size  stock symbol  old -> new  reason (patch step, source line)",
        f"# {n} entries, {unattributed} unattributed",
    ]
    for title, img, rows in groups:
        if not rows:
            continue
        lines.append("")
        lines.append(title)
        for e in rows:
            sym = img.symbol(e.stock_addr)
            moved = f" (stock 0x{e.stock_addr:08X})" if e.stock_addr != e.addr else ""
            if e.old or e.new:
                size = max(len(e.old), len(e.new))
                change = f"{_hex(e.old) or '-'} -> {_hex(e.new)}"
                lines.append(
                    f"  0x{e.addr:08X} {size:6d}  {sym}{moved}  {change}  {e.reason}"
                )
            else:
                lines.append(f"  0x{e.addr:08X}  {sym}{moved}  {e.reason}")
    return lines


def write_manifest(path, images):
    Path(path).write_text("\n".join(manifest_lines(images)) + "\n")


# Patched ELF --------------------------------------------------------------------


def _our_symbols(elf):
    """``(funcs, datas, mapping)`` from our linked ELF, stock aliases left out."""
    funcs, datas, mapping = [], [], []
    symtab = elf.get_section_by_name(".symtab")
    for s in symtab.iter_symbols():
        name, shndx = s.name, s["st_shndx"]
        if not name or shndx in ("SHN_UNDEF", "SHN_ABS") or name.startswith("stock_"):
            continue
        addr = s["st_value"]
        if name in ("$t", "$d", "$a") or name.startswith(("$t.", "$d.")):
            mapping.append((addr, name[:2]))
            continue
        kind = s["st_info"]["type"]
        if kind == "STT_FUNC":
            funcs.append((addr & ~1, name))
        elif kind in ("STT_OBJECT", "STT_NOTYPE"):
            datas.append((addr, name))
    return funcs, datas, mapping


def _ram_regions(images):
    """RAM sections of a single-device build: the ITCM image and each other block."""
    if len(images) != 1:
        return []  # Both firmwares own all of RAM.
    blocks = [(run, data) for run, data, *_ in images[0].ram_blocks()]
    itcm = [(run, data) for run, data in blocks if run < 0x0001_0000]
    out = []
    if itcm:
        mem = bytearray(max(run + len(data) for run, data in itcm))
        for run, data in itcm:
            mem[run : run + len(data)] = data
        out.append((".itcm", 0, bytes(mem), True))
    for run, data in sorted(blocks):
        if run >= 0x0001_0000:
            out.append((f".ram_{run:08x}", run, data, False))
    return out


def write_elf(path, images, internal, bank2, external, elf, work):
    """The patched ELF (``images``: ``DeviceImages`` of each device)."""
    prefix = toolchain_prefix()
    if prefix is None:
        print("No arm-none-eabi binutils; skipping the patched ELF.")
        return False
    regions = [(".intflash", INT_BASE, bytes(internal), True)]
    if bank2:
        regions.append((".bank2", BANK2_BASE, bytes(bank2), True))
    if external:
        regions.append((".extflash", EXT_BASE, bytes(external), True))
    regions += _ram_regions(images)

    def mapped(addr):
        return any(b <= addr < b + len(data) for _, b, data, _ in regions)

    funcs, datas, our_mapping = _our_symbols(elf)
    mapping = [(a, k) for a, k in our_mapping if mapped(a)]
    mapping += [(b, "$t" if code else "$d") for _, b, _, code in regions]
    # The vector table is data, the code after it Thumb.
    mapping += [(INT_BASE, "$d"), (INT_BASE + VECTOR_TABLE_END, "$t")]
    for img in images:
        d = img.d
        f, dt = syms.parse_syms(syms.syms_path(d.name))
        for addr, name in f.items():
            new = img.final_address(addr)
            if new is not None:
                funcs.append((new, syms.c_name(d.name, name)))
                if EXT_BASE <= new < EXT_BASE + len(external):
                    mapping.append((new, "$t"))
        for addr, name in dt.items():
            new = img.final_address(addr)
            if new is not None:
                datas.append((new, syms.c_name(d.name, name)))
        for addr, name, size in d.patcher_funcs:
            funcs.append((addr, name))
            mapping += [(addr, "$t"), (addr + size, "$d")]
        if d.relocation:
            vt = d.relocation.dst
            mapping += [(vt, "$d"), (vt + VECTOR_TABLE_END, "$t")]
        end = getattr(d, "STOCK_RELOCATE_END", None)
        if end is not None and img.final_address(end) is not None:
            mapping.append((img.final_address(end), "$d"))  # The init table.
        for s, _ in d.freed_int_ranges():
            mapping.append((INT_BASE + s, "$d"))
    flash_end = elf.get_section_by_name(".symtab").get_symbol_by_name("__flash_end__")
    if flash_end:
        mapping.append((flash_end[0]["st_value"], "$d"))
    # One mapping symbol per address; the last added wins.
    mapping = [(a, k) for a, k in {a: k for a, k in mapping}.items() if mapped(a)]
    funcs = sorted({(a, n) for a, n in funcs if mapped(a) or in_ram(a)})
    datas = sorted({(a, n) for a, n in datas if mapped(a) or in_ram(a)})
    entry = int.from_bytes(bytes(internal[4:8]), "little") & ~1
    link(path, regions, funcs, datas, mapping, entry, work, prefix)
    return True
