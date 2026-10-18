"""patches/xip.py's check: which stock units can run from external flash."""

import bisect
import contextlib
import io
import struct

import pytest

from patches import syms, xip, xrefs
from patches.mario import BALL_UNIT
from patches.zelda import VERMIN_UNIT

REPO = xrefs.REPO


@pytest.fixture(scope="module", params=["mario", "zelda"])
def device(request):
    name = request.param
    for f in (f"internal_flash_backup_{name}.bin", f"flash_backup_{name}.bin"):
        if not (REPO / f).exists():
            pytest.skip(f"{f} not found")
    with contextlib.redirect_stdout(io.StringIO()):
        analysis = xrefs.analyze(name)
    return name, analysis, syms.load(name)


def _unit(analysis, addr):
    spans = xrefs.units(analysis.code)
    i = bisect.bisect_right([s for s, _ in spans], addr) - 1
    return tuple(spans[i])


def test_minigame(device):
    name, analysis, names = device
    lo, hi = {"mario": BALL_UNIT, "zelda": VERMIN_UNIT}[name]
    entries, ctors = xip.check(analysis, names, lo, hi)
    if name == "mario":
        # BALL's API thunks; its static constructor stays internal.
        thunks = [0x3358, 0x335E, 0x3368, 0x3370, 0x3378, 0x337E, 0x3390]
        assert entries == [0x0800_0000 + a for a in thunks]
        assert ctors == [0x0800_3350]
    else:
        api = [
            "vermin_open",
            "vermin_tick_api",
            "vermin_input_api",
            "vermin_sound_api",
            "vermin_reset_api",
            "vermin_is_running",
            "vermin_save_scores_api",
        ]
        assert entries == [names[n] & ~1 for n in api]
        assert ctors == []


def test_not_a_unit(device):
    name, analysis, names = device
    lo, hi = {"mario": BALL_UNIT, "zelda": VERMIN_UNIT}[name]
    with pytest.raises(xip.XipError, match="isn't a unit"):
        xip.check(analysis, names, lo, hi - 2)


# (unit start, why it can't move) of units that only calls make unsafe.
UNSAFE = {
    "mario": [
        (0x0800_9C48, "reachable from vector 0x0800003C"),  # under SysTick
        (0x0800_479C, "reachable from calls flash_disable_memory_mapped"),
        (0x0800_9AC0, "reachable from main before power_on_init"),
        (0x0800_45D2, "reachable from ctor"),
    ],
    "zelda": [
        (0x0801_2A58, "reachable from vector 0x0800003C"),
        (0x0800_E540, "reachable from calls flash_disable_memory_mapped"),
        (0x0800_1EE4, "reachable from main before power_on_init"),
        (0x0800_0B88, "reachable from ctor"),
    ],
}


def test_unsafe(device):
    name, analysis, names = device
    for addr, why in UNSAFE[name]:
        with pytest.raises(xip.XipError, match=why):
            xip.check(analysis, names, *_unit(analysis, addr))


def test_data_reference(device):
    _, analysis, names = device
    reset = struct.unpack_from("<I", analysis.internal, 4)[0]
    with pytest.raises(xip.XipError, match="vector at 0x08000004 points into"):
        xip.check(analysis, names, *_unit(analysis, reset & ~1))
