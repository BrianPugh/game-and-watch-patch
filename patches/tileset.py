from io import BytesIO
from math import ceil

import numpy as np
from PIL import Image

from .exception import ParsingError

_BLOCK_SIZE = 16
_BLOCK_PIXEL = _BLOCK_SIZE * _BLOCK_SIZE

PALETTE_OFFSETS = [
    0xB_EC68,
    0xB_EDA8,
    0xB_EEE8,
    0xB_F028,
    0xB_F168,
]


def bytes_to_tilemap(data, palette=None, bpp=8, width=256):
    """
    Parameters
    ----------
    palette : bytes
       320 long RGBA (80 colors). Alpha is ignored.

    Returns
    -------
    PIL.Image
        Rendered RGB image.
    """

    # assert bpp in [4, 8]

    if bpp < 8:
        nibbles = bytearray()
        # offset = 0x0
        for b in data:
            shift = 8 - bpp
            while shift >= 0:
                nibbles.append(b >> shift & (2**bpp - 1))
                shift -= bpp
            # nibbles.append((b >> 4) | (offset << 4))
            # nibbles.append((b & 0xF) | (offset << 4))
        data = bytes(nibbles)
        del nibbles

    # Assemble bytes into an index-image
    h, w = int(ceil(len(data) / width / _BLOCK_SIZE) * _BLOCK_SIZE), width
    canvas = np.zeros((h, w), dtype=np.uint8)
    i_sprite = 0
    for i in range(0, len(data), _BLOCK_PIXEL):
        sprite = data[i : i + _BLOCK_PIXEL]

        x = i_sprite * _BLOCK_SIZE % w
        y = _BLOCK_SIZE * (i_sprite * _BLOCK_SIZE // w)
        view = canvas[y : y + _BLOCK_SIZE, x : x + _BLOCK_SIZE]
        sprite_block = np.frombuffer(sprite, dtype=np.uint8).reshape(
            _BLOCK_SIZE, _BLOCK_SIZE
        )
        view[:] = sprite_block

        i_sprite += 1

    if palette is None:
        return Image.fromarray(canvas, "L")

    # Apply palette to index-image
    p = np.frombuffer(palette, dtype=np.uint8).reshape((80, 4))
    p = p[:, :3]
    p = np.fliplr(p)  # BGR->RGB

    im = Image.fromarray(canvas, "P")
    im.putpalette(p)

    return im


def rgb_to_index(tilemap, palette):
    if isinstance(tilemap, Image.Image):
        tilemap = tilemap.convert("RGB")
        tilemap = np.array(tilemap)
    elif isinstance(tilemap, np.ndarray):
        pass
    else:
        raise TypeError(f"Don't know how to handle tilemap type {type(tilemap)}")

    # Convert rgb tilemap to index image
    p = np.frombuffer(palette, dtype=np.uint8).reshape((80, 4))
    p = p[:, :3]
    p = np.fliplr(p)  # BGR->RGB
    p = p[None, None].transpose(0, 1, 3, 2)  # (1, 1, 3, 80)

    # Find closest color
    diff = tilemap[..., None] - p
    dist = np.linalg.norm(diff, axis=2)
    tilemap = np.argmin(dist, axis=-1).astype(np.uint8)

    return tilemap


def tilemap_to_bytes(tilemap, palette=None, bpp=8):
    """
    Parameters
    ----------
    tilemap : PIL.Image.Image or numpy.ndarray
        RGB data
    palette : bytes
       320 long RGBA (80 colors). Alpha is ignored.

    Returns
    -------
    bytes
        Bytes representation of index image
    """

    if isinstance(tilemap, Image.Image):
        tilemap = tilemap.convert("RGB")
        tilemap = np.array(tilemap)
    elif isinstance(tilemap, np.ndarray):
        pass
    else:
        raise TypeError(f"Don't know how to handle tilemap type {type(tilemap)}")

    if palette is not None:
        tilemap = rgb_to_index(tilemap, palette)

    # Need to undo the tiling now.
    out = []
    for i in range(0, tilemap.shape[0], _BLOCK_SIZE):
        for j in range(0, tilemap.shape[1], _BLOCK_SIZE):
            sprite = tilemap[i : i + _BLOCK_SIZE, j : j + _BLOCK_SIZE]
            sprite_bytes = sprite.tobytes()
            out.append(sprite_bytes)
    out = b"".join(out)

    if bpp == 4:
        out_packed = bytearray()
        assert len(out) % 2 == 0
        for i in range(0, len(out), 2):
            b1, b2 = out[i], out[i + 1]
            b1 &= 0xF
            b2 &= 0xF
            out_packed.append((b1 << 4) | b2)
        out = bytes(out_packed)

    return out


def decode_backdrop(data):
    """Convert easter egg images to GIF

    Based on:
        https://gist.github.com/GMMan/c1f0b516afdbb71769752ee06adbbd9a

    Returns
    -------
    PIL.Image.Image
        Decoded image
    int
        Number of bytes consumed to create image.
    """

    def rgb565_to_rgba32(pix):
        r = int(((pix >> 11) * 255 + 15) / 31)
        g = int((((pix >> 5) & 0x3F) * 255 + 31) / 63)
        b = int(((pix & 0x1F) * 255 + 15) / 31)
        return r, g, b

    idx = 0
    out = []

    # Header
    out.append(b"GIF89a")

    width = int.from_bytes(data[idx : idx + 2], "little")
    idx += 2

    height = int.from_bytes(data[idx : idx + 2], "little")
    idx += 2

    palette_size = data[idx]
    idx += 1
    idx += 1  # padding

    palette = []
    for _ in range(palette_size):
        palette.append(int.from_bytes(data[idx : idx + 2], "little"))
        idx += 2

    gct_size = 0
    calc_gct_size = 2
    while calc_gct_size < palette_size:
        gct_size += 1
        calc_gct_size <<= 1

    # Logical screen descriptor
    out.append(width.to_bytes(2, "little"))
    out.append(height.to_bytes(2, "little"))
    out.append(((1 << 7) | gct_size).to_bytes(1, "little"))
    out.append(b"\x00")
    out.append(b"\x00")

    # Global Color Table
    for i in range(calc_gct_size):
        if i < len(palette):
            r, g, b = rgb565_to_rgba32(palette[i])
            out.append(r.to_bytes(1, "little"))
            out.append(g.to_bytes(1, "little"))
            out.append(b.to_bytes(1, "little"))
        else:
            out.append(b"\x00")
            out.append(b"\x00")
            out.append(b"\x00")

    # Image descriptor
    out.append(b"\x2c")
    out.append(b"\x00\x00")  # x
    out.append(b"\x00\x00")  # y
    out.append(width.to_bytes(2, "little"))
    out.append(height.to_bytes(2, "little"))
    out.append(b"\x00")

    # Frame
    min_code_size = data[idx]
    idx += 1
    out.append(min_code_size.to_bytes(1, "little"))

    while True:
        block_size = data[idx]
        idx += 1
        out.append(block_size.to_bytes(1, "little"))
        if block_size == 0:
            break
        out.append(data[idx : idx + block_size])
        idx += block_size

    trailer = data[idx]
    idx += 1

    if trailer != 0x3B:
        raise ParsingError("Invalid GIF Trailer")
    out.append(trailer.to_bytes(1, "little"))
    out = b"".join(out)

    im = Image.open(BytesIO(out))

    return im, idx
