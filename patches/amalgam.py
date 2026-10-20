"""Mario and Zelda in one 256 KB bank-1 image (``--device=amalgam``).

Each firmware is patched in its own buffers at its stock addresses, like a
single-model build, from the same ELF. Both allocate internal space from one
bank-1 ``Region``; the amalgam removes both factory tests (--no-factory-test):

    0x08000000  Mario's stock code (it doesn't move)
    0x08018100  free (84 bytes; Zelda's factory test in its buffer)
    0x08018154  Zelda's stock code past its factory test (reset handler,
                init and rwdata tables), in place
    0x0801B400  Zelda's stock code below its factory test, vector table
                included (``ZELDA_DST``: 1 KB aligned for VTOR), compacted:
                the stock code it frees there is left out (``Relocation``)
                and the room shows up past it
    0x08031CD8  our code (``OUR_CODE``), then data and compressed images

Our code starts past both firmwares, so neither stock image overlaps it in its
own buffer. A hole one device frees under the other's code (Zelda's factory
test lies under Mario's code) is clipped (``Region.free``).

Zelda is patched first: its external data stays at offset 0 (its saves are raw
flash offsets below 0x20000), and Mario's goes after it (``set_ext_base``).

``merge`` builds the image from the buffers: each allocation from its owner's
buffer, and everything else (our code, free space) from whichever device
changed it. A device writing where another one owns, or both changing the same
byte differently, is an error.

Mario's vector table boots the image into our bootloader, which starts Zelda
through its own vector table when a backup register selects it (main.c). Each
game-select DUAL BOOT entry selects the other firmware and resets.

With --sd-bootloader, Sylverb's SD bootloader owns 0x08032000 on (LEFT+GAME
boots it), so the image ends there, and our code is linked to end there too
(scripts/amalgam_sd.py). That leaves 808 bytes past Zelda's code, so
--xip-minigame is implied: with --no-factory-test it frees enough of Zelda's
code that the compaction makes room for ours. Mario's freed code holds data.
The compressed RAM images go into holes, a block each
(``Region.pending_in_holes``).
"""

import copy
import sys

import numpy as np

from .firmware import HeaderMetaData
from .layout import Region, backfill
from .mario import MarioGnW
from .utils import round_up_page
from .zelda import ZeldaGnW

SIZE = 0x4_0000
BASE = 0x0800_0000
# Zelda's code below its factory test moves past both stock images (1 KB
# aligned for VTOR: its vector table moves too), and our code follows it.
ZELDA_DST = BASE + (
    -(-max(ZeldaGnW.Int.STOCK_ROM_END, MarioGnW.Int.STOCK_ROM_END) // 0x400) * 0x400
)
OUR_CODE = ZELDA_DST + ZeldaGnW.AMALGAM_RELOCATE_END - BASE

# --sd-bootloader: Sylverb's SD bootloader runs in place from here to the end
# of bank 1 (its SD updater writes it there), so nothing of ours goes there.
SD_BOOTLOADER = 0x0803_2000
# Shared by our code and data, no device owns these bytes.
SHARED = "both"
# Label of each device's game-select switch entry: the firmware it boots.
OTHER_LABEL = {"mario": "ZELDA", "zelda": "MARIO"}


class AmalgamError(Exception):
    pass


def configure(zelda, mario, parser):
    """Parse the command line for each device with its own options and
    defaults (the 2 share some option names, e.g. --game-select-dual-boot-icon
    with different default icons). An option either device knows is fine.
    Returns the global options."""
    if {"-h", "--help"} & set(sys.argv[1:]):
        parser.conflict_handler = "resolve"
        zelda.add_arguments(parser)
        mario.add_arguments(parser)
        parser.parse_args()  # Prints the help and exits.
    unknown = None
    for device in (zelda, mario):
        p = copy.deepcopy(parser)
        device.add_arguments(p)
        args, rest = p.parse_known_args()
        unknown = set(rest) if unknown is None else unknown & set(rest)
        for flag in ("encrypt", "triple_boot", "relocate_stock"):
            if getattr(args, flag, None):
                parser.error(
                    f"--{flag.replace('_', '-')} isn't supported with --device=amalgam"
                )
        if getattr(args, "internal_only", False):
            parser.error("--internal-only isn't supported with --device=amalgam")
        args.extended = True
        # Its holes make room for both firmwares in one bank.
        args.no_factory_test = True
        if args.sd_bootloader:
            # Our code needs the room it frees in Zelda's code.
            args.xip_minigame = True
        # A game-select entry, labeled with the other firmware, switches to
        # it (main.c), and it's the only way to; --game-select-dual-boot adds
        # the dual-boot target's entry after it.
        args.game_select_switch_label = OTHER_LABEL[device.name]
        device.amalgam = True
        device.set_args(args, p)
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(sorted(unknown))}")
    zelda.args.relocate_stock = ZELDA_DST
    return mario.args


def load(devices, patch):
    """Copy our code into each device's (extended) internal image."""
    # With --sd-bootloader, our code ends at the SD bootloader, and so does
    # the image.
    if len(patch) not in (SIZE, SD_BOOTLOADER - BASE):
        raise AmalgamError(f"Expected a {SIZE} byte patch, got {len(patch)}")
    patch = bytes(patch).ljust(SIZE, b"\x00")
    zelda = next(d for d in devices if d.relocates_in_amalgam)
    vt = zelda.internal.address("__zelda_vector_table__")
    if vt != zelda.args.relocate_stock:
        raise AmalgamError(
            f"__zelda_vector_table__ (0x{vt:08X}) isn't where Zelda's stock "
            f"code goes (0x{zelda.args.relocate_stock:08X})"
        )
    for d in devices:
        d.internal.extend(b"\x00" * (SIZE - len(d.internal)))
        d.internal[d.internal.STOCK_ROM_END :] = patch[d.internal.STOCK_ROM_END :]
        d.internal_orig = bytes(d.internal)


def bank1_region(zelda, mario):
    """The bank-1 region both devices allocate from."""
    region = Region("bank1", BASE, SIZE, pos=mario.internal.empty_offset)
    ours = mario.internal.address("__our_code__", sub_base=True)
    region.note(ours, region.pos - ours, SHARED, "our code")
    # Past Zelda's code kept in place, below its relocated code.
    region.free(zelda.internal.STOCK_ROM_END, ZELDA_DST - BASE)
    if mario.args.sd_bootloader:
        region.reserve(
            SD_BOOTLOADER - BASE, BASE + SIZE - SD_BOOTLOADER, SHARED, "SD bootloader"
        )
        # There's hardly any room at the cursor.
        region.pending_in_holes = True
    return region


def patch(layout, zelda, mario):
    region = zelda.int_space
    if region.pending_in_holes:
        # Mario's compressed images go in the holes Mario frees, so they
        # don't hold Zelda's back.
        for p in mario.pending:
            region.pending.remove(p)
    layout.patch_device(zelda)
    if region.pending_in_holes:
        region.pending += mario.pending
    # Mario's stock data moves up in its own buffer, so it has to start past
    # its stock image (only matters when Zelda's data shrinks below 1 MB).
    base = max(len(zelda.external), len(mario.external))
    mario.set_ext_base(round_up_page(base))
    layout.patch_device(mario)
    backfill((zelda, mario))


def _owners(region, devices):
    """Per byte: the index in ``devices`` of the owner, or -1 (shared)."""
    index = {d.name: i for i, d in enumerate(devices)}
    owner = np.full(region.size, -1, dtype=np.int8)
    # Data in a freed hole sits inside a larger allocation; it goes last.
    for a in sorted(region.allocations, key=lambda a: -a.size):
        if a.owner == SHARED:
            owner[a.offset : a.offset + a.size] = -1
        elif a.owner in index:
            owner[a.offset : a.offset + a.size] = index[a.owner]
        else:
            raise AmalgamError(f"unknown owner {a.owner!r} in {region.name}")
    return owner


def _addresses(mask, base, n=4):
    found = np.flatnonzero(mask)
    s = ", ".join(f"0x{base + int(o):08X}" for o in found[:n])
    return s + (f" (+{len(found) - n} more)" if len(found) > n else "")


def merge_internal(region, devices):
    """The bank-1 image from each device's internal buffer."""
    base = region.base
    owner = _owners(region, devices)
    bufs = [np.frombuffer(bytes(d.internal), dtype=np.uint8) for d in devices]
    origs = [np.frombuffer(d.internal_orig, dtype=np.uint8) for d in devices]
    shared = owner < 0
    # Shared bytes start out as in the device that keeps its stock code in
    # place: everything there but its stock code is our code or free.
    ref = next(i for i, d in enumerate(devices) if not d.relocates_in_amalgam)

    out = origs[ref].copy()
    written = np.zeros(region.size, dtype=bool)
    for i, d in enumerate(devices):
        if d.relocates_in_amalgam:
            # Its relocation copied this range out after any data put there,
            # unless the data came after it (backfill).
            end = d.AMALGAM_RELOCATE_END - base
            mine = (owner[:end] == i) & ~shared[:end]
            for start, size in d.backfilled:
                mine[start : start + size] = False
            if mine.any():
                raise AmalgamError(
                    f"{d.name} placed data over its own relocated stock code: "
                    + _addresses(mine, base)
                )
        # Its freed code is udf in its buffer; the other device may own data
        # there.
        exempt = np.zeros(region.size, dtype=bool)
        for start, end in d.freed_int_ranges():
            exempt[start:end] = True
        # A relocated device's buffer holds the stock code it moved; what
        # it patched there is in its relocated copy.
        if d.relocates_in_amalgam:
            exempt[: d.AMALGAM_RELOCATE_END - base] = True
        changed = (bufs[i] != origs[i]) & ~exempt
        foreign = changed & ~shared & (owner != i)
        if foreign.any():
            raise AmalgamError(
                f"{d.name} wrote into another device's bank-1 range: "
                + _addresses(foreign, base)
            )
        if (shared & ~exempt & (origs[i] != origs[ref])).any():
            raise AmalgamError(f"{d.name}'s copy of our code differs")
        mine = changed & shared
        clash = mine & written & (out != bufs[i])
        if clash.any():
            raise AmalgamError(
                "both devices changed the same bytes: " + _addresses(clash, base)
            )
        out[mine] = bufs[i][mine]
        written |= mine
        out[owner == i] = bufs[i][owner == i]
    return bytearray(out.tobytes())


def merge_external(zelda, mario):
    """Zelda's external image, then Mario's from its ``ext_base``."""
    if len(zelda.external) > mario.ext_base:
        raise AmalgamError("Zelda's external data grew past Mario's ext_base")
    out = bytearray(zelda.external)
    out += b"\x00" * (mario.ext_base - len(out))
    out += mario.external[mario.ext_base :]
    return out


def metadata(internal, external):
    """The header word retro-go reads at 0x080001B8 (Mario's vector table)."""
    meta = HeaderMetaData(
        external_flash_size=len(external), is_mario=True, is_zelda=True
    )
    internal[0x01B8:0x01BC] = meta.pack()
