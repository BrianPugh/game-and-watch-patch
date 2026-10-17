"""The write log (patches/writelog.py) and the manifest's diff (patches/manifest.py)."""

import numpy as np

from patches import writelog
from patches.firmware import Firmware
from patches.manifest import View, diff


class Buf(Firmware):
    FLASH_LEN = 64


def _tracked():
    fw = Buf()
    writelog.track(fw)
    return fw


def _reasons(fw, start, stop):
    return [writelog.WRITES[i].step if i >= 0 else None for i in fw.prov[start:stop]]


def test_write_names_step_and_site():
    fw = _tracked()
    writelog.set_step("Hook the thing.")
    fw.replace(8, b"\x01\x02")
    w = writelog.WRITES[fw.prov[8]]
    assert w.step == "Hook the thing."
    assert w.site.startswith("tests/test_manifest.py:")
    assert fw.prov[7] == -1 and fw.prov[10] == -1


def test_step_block_restores_outer_step():
    fw = _tracked()
    writelog.set_step("outer")
    with writelog.step("inner"):
        fw[0] = 1
    fw[1] = 1
    assert _reasons(fw, 0, 2) == ["inner", "outer"]


def test_move_carries_provenance():
    fw = _tracked()
    writelog.set_step("patch")
    fw.replace(0, b"\xaa" * 4)
    fw.move(0, 16, size=8)
    # The patched bytes moved; the source was cleared without an author.
    assert _reasons(fw, 16, 20) == ["patch"] * 4
    assert _reasons(fw, 20, 24) == [None] * 4
    assert _reasons(fw, 0, 8) == [None] * 8


def test_untracked_write_has_no_author():
    fw = _tracked()
    writelog.set_step("patch")
    fw.replace(0, b"\xaa" * 4)
    with writelog.untracked():
        fw.clear_range(0, 2)
    assert _reasons(fw, 0, 4) == [None, None, "patch", "patch"]


def test_fixup_keeps_patch_attribution():
    fw = _tracked()
    writelog.set_step("patch")
    fw.replace(0, b"\xaa" * 2)
    with writelog.step("fixup"):
        writelog.record(fw, 0, 4, fixup=True)
    assert _reasons(fw, 0, 4) == ["patch", "patch", "fixup", "fixup"]


def test_buffer_length_changes_follow():
    fw = _tracked()
    fw.extend(b"\x00" * 8)
    fw[70] = 1
    assert len(fw.prov) == 72
    del fw[60:]
    assert writelog.provenance(fw, 0, len(fw)).shape == (60,)


def _view(stock, final, prov=None):
    stock, final = np.array(stock, np.uint8), np.array(final, np.uint8)
    return View(0x1000, 0x1000, final, stock, prov, default="dflt")


def test_diff_merges_runs_with_one_writer():
    with writelog.step("a"):
        a = writelog.new()
    with writelog.step("b"):
        b = writelog.new()
    stock = [0] * 16
    final = [1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]
    prov = np.array([a] * 8 + [b] * 4 + [-1] * 4, np.int32)
    out = diff(_view(stock, final, prov))
    # 0-3 merge (gap of 2, same writer), 8-9 is b's, 15 has no writer.
    assert [(e.addr - 0x1000, e.new.hex(), e.reason) for e in out] == [
        (0, "01000001", writelog.WRITES[a].reason),
        (8, "0101", writelog.WRITES[b].reason),
        (15, "01", "dflt"),
    ]


def test_diff_skips_and_reports_appended_bytes():
    v = _view([0] * 4, [1, 1, 0, 0, 7, 7])
    v.skip = [(0, 1)]
    out = diff(v)
    assert [(e.addr - 0x1000, e.old, e.new) for e in out] == [
        (1, b"\x00", b"\x01"),
        (4, b"", b"\x07\x07"),
    ]
