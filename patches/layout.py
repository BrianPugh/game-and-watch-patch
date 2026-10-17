"""Where the patcher puts things: one ``Region`` per span of memory it fills.

A ``Region`` hands out space from a cursor and from holes (freed stock code),
and records who asked for each piece. ``ExtRegion`` compacts the stock external
image in address order instead: every stock byte from its cursor on is either
kept (shifted down) or dropped.

Offsets are from the region's ``base``, like the image buffers they describe.
"""

from dataclasses import dataclass

from .exception import NotEnoughSpaceError
from .utils import round_up_word


@dataclass
class Allocation:
    offset: int
    size: int
    owner: str
    label: str


class Region:
    """``size`` bytes at ``base``; new data goes at ``pos`` or in a hole.

    ``pending`` holds callables returning the size of data that will be written
    at ``pos`` later (the compressed rwdata and SRAM3 images), and ``slack`` is
    held back on top of that; ``free_space`` leaves room for both.
    """

    def __init__(self, name, base, size, pos=0):
        self.name = name
        self.base = base
        self.size = size
        self.pos = pos
        # [start, end) of freed ranges not yet reused; they shrink as data fills them.
        self.holes = []
        # Every range given to free(), as freed.
        self.freed = []
        self.allocations = []
        self.pending = []
        self.slack = 0

    def __repr__(self):
        return f"Region({self.name!r}, 0x{self.base:08X}, 0x{self.size:X})"

    @property
    def free_space(self):
        """Bytes left at the cursor (holes not included)."""
        return self.size - self.pos - sum(p() for p in self.pending) - self.slack

    @property
    def hole_space(self):
        return sum(end - start for start, end in self.holes)

    def _take_hole(self, size):
        """Offset of the smallest hole that fits ``size``, or None."""
        fits = [h for h in self.holes if h[1] - h[0] >= size]
        if not fits:
            return None
        hole = min(fits, key=lambda h: h[1] - h[0])
        offset = hole[0]
        hole[0] += size
        return offset

    def alloc(self, size, owner, label=""):
        """Offset of ``size`` new bytes (word aligned)."""
        offset = self._take_hole(round_up_word(size))
        if offset is None:
            if self.free_space < size:
                raise NotEnoughSpaceError(
                    f"{size} bytes don't fit in {self.name} ({self.free_space} free)"
                )
            offset = self.pos
            self.pos += round_up_word(size)
        self.allocations.append(Allocation(offset, size, owner, label))
        return offset

    def append(self, size, owner, label=""):
        """Offset of ``size`` bytes already written at the cursor (unaligned)."""
        offset = self.pos
        self.pos += size
        self.allocations.append(Allocation(offset, size, owner, label))
        return offset

    def reserve(self, offset, size, owner, label=""):
        """Claim ``size`` bytes at a fixed ``offset``."""
        if not 0 <= offset <= offset + size <= self.size:
            raise NotEnoughSpaceError(
                f"0x{self.base + offset:08X}-0x{self.base + offset + size:08X} "
                f"is outside {self.name}"
            )
        for a in self.allocations:
            if offset < a.offset + a.size and a.offset < offset + size:
                raise ValueError(
                    f"{label or owner} overlaps {a.label or a.owner} in {self.name}"
                )
        self.allocations.append(Allocation(offset, size, owner, label))
        return offset

    def free(self, start, end):
        """Make [start, end) reusable."""
        self.holes.append([start, end])
        self.freed.append((start, end))

    def is_freed(self, offset):
        return any(start <= offset < end for start, end in self.freed)


class ExtRegion(Region):
    """The external image, compacted in stock address order.

    ``keep`` shifts the stock data from ``cursor`` on down by ``-shift``;
    ``drop`` leaves a range out, which grows the shift in ``align`` steps (the
    rest of the range stays as zero padding). Data at the same shift moves as
    one block, so offsets inside it stay intact.
    """

    def __init__(self, name, buf, align, cursor=0):
        super().__init__(name, buf.FLASH_BASE, len(buf))
        self.buf = buf
        self.align = align
        self.cursor = cursor
        self.shift = 0

    def keep(self, end):
        """Shift the stock data [cursor, end) down by the current shift."""
        start = self.cursor
        if end > start and self.shift:
            self.buf.move(start, self.shift, size=end - start)
        self.cursor = max(start, end)

    def drop(self, start, end):
        """Leave the stock data [start, end) out of the image.

        Data from the cursor to ``start`` is kept. Bytes before the cursor were
        already placed, so only [max(start, cursor), end) counts.
        """
        self.keep(start)
        start = max(start, self.cursor)
        n = (end - start) // self.align * self.align
        self.buf.clear_range(start, end)
        self.cursor = start + n
        self.shift -= n

    def pack(self, start, end, blocks):
        """Replace the stock data [start, end) with smaller blocks.

        ``blocks`` is ``(stock offset, data)`` pairs inside the range; they're
        packed back to back (word aligned) where ``start`` lands, and each
        block's stock offset maps to its new place. The data after the range
        shifts down in ``align`` steps, like ``drop``.
        """
        self.keep(start)
        packed = bytearray()
        new = []
        for offset, data in blocks:
            if not start <= offset < end:
                raise ValueError(f"0x{offset:X} is outside 0x{start:X}-0x{end:X}")
            new.append((offset, len(packed), len(data)))
            packed += bytes(data).ljust(round_up_word(len(data)), b"\x00")
        n = (end - start - len(packed)) // self.align * self.align
        if n < 0:
            raise ValueError("The packed blocks are larger than the range.")
        # Nothing keeps (and so re-maps) [start, end) after this: the cursor
        # moves to the end, and the next kept data lands after the padding.
        dst = start + self.shift
        self.buf.clear_range(dst, end)
        self.buf[dst : dst + len(packed)] = packed
        for offset, pos, size in new:
            self.buf._lookup.add(self.base + offset, self.base + dst + pos, size)
        self.cursor = end
        self.shift -= n
