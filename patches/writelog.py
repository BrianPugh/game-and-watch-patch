"""Who wrote each byte: the provenance behind the patch manifest.

A tracked image buffer (``Firmware.prov``) keeps, per byte, the index in
``WRITES`` of the last write to it: the patch step running at the time (the
last ``printi`` message, or a ``step`` block) and the patcher source line that
made it. ``patches/manifest.py`` diffs the final images against stock and
names each change after its byte's write.

Copies of data that's already there (moves, compaction, relocation) run
``untracked`` and carry the source bytes' provenance along with
``provenance``/``set_provenance``; other untracked writes (the OTFDEC pass,
clearing dropped data) leave no author (-1).
"""

import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]

_step = ""
_untracked = 0

# Frames that are the write machinery, not the patch making the write.
_SKIP_FILES = {"patches/patch.py", "patches/writelog.py"}
_SKIP_FUNCS = {"__setitem__", "set_range", "clear_range", "_pack"}


@dataclass(frozen=True)
class Write:
    step: str
    site: str

    @property
    def reason(self):
        return f"{self.step} ({self.site})" if self.step else self.site


WRITES = []
_index = {}


@dataclass
class RamImage:
    """A block stock copies into RAM at boot, as the patcher leaves it (it's
    stored compressed, so the manifest and patched ELF can't read it back)."""

    run: int  # Run address.
    data: bytes
    stock: bytes
    prov: np.ndarray | None
    label: str


def set_step(msg):
    global _step
    _step = msg.strip()


@contextlib.contextmanager
def step(msg):
    """Name the writes in this block ``msg``, then go back to the outer step."""
    global _step
    outer, _step = _step, msg
    try:
        yield
    finally:
        _step = outer


@contextlib.contextmanager
def untracked():
    global _untracked
    _untracked += 1
    try:
        yield
    finally:
        _untracked -= 1


def _site():
    f = sys._getframe(1)
    while f is not None:
        path = Path(f.f_code.co_filename)
        try:
            rel = path.resolve().relative_to(REPO).as_posix()
        except ValueError:
            rel = path.name
        if rel not in _SKIP_FILES and f.f_code.co_name not in _SKIP_FUNCS:
            return f"{rel}:{f.f_lineno}"
        f = f.f_back
    return "?"


def new(step=None, site=None):
    """Index of a write made at the caller (or as given)."""
    w = Write(_step if step is None else step, site or _site())
    if w not in _index:
        _index[w] = len(WRITES)
        WRITES.append(w)
    return _index[w]


def track(fw):
    """Start keeping provenance for ``fw``."""
    fw.prov = np.full(len(fw), -1, dtype=np.int32)


def _prov(fw):
    """``fw.prov``, grown or cut to the buffer's length (images only change
    length at the end)."""
    p = fw.prov
    if p is not None and len(p) != len(fw):
        if len(p) > len(fw):
            p = p[: len(fw)].copy()
        else:
            p = np.concatenate([p, np.full(len(fw) - len(p), -1, dtype=np.int32)])
        fw.prov = p
    return p


def record(fw, start, stop, fixup=False):
    """Log a write of ``fw[start:stop]`` (call it from ``__setitem__``, before
    the buffer changes length).

    ``fixup``: a mechanical fix of what's there (a relocated reference); bytes
    a patch wrote stay that patch's.
    """
    p = _prov(fw)
    if p is None or stop <= start:
        return
    if _untracked:
        p[start:stop] = -1
    elif fixup:
        span = p[start:stop]
        span[span < 0] = new()
    else:
        p[start:stop] = new()


def provenance(fw, start, stop):
    """A copy of the provenance of ``fw[start:stop]`` (None: untracked)."""
    p = _prov(fw)
    return None if p is None else p[start:stop].copy()


def set_provenance(fw, start, prov):
    """Give ``fw[start:]`` the provenance ``prov`` (from ``provenance``)."""
    p = _prov(fw)
    if p is None:
        return
    n = len(p[start : start + len(prov)])
    p[start : start + n] = -1 if prov is None else prov[:n]
