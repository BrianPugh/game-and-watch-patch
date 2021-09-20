from pathlib import Path
import numpy as np
import matplotlib
from PIL import Image


data = Path("build/decrypt.bin").read_bytes()

addr = 0xaace4
iconset_bytes = data[addr:addr+6144 + 6144 + 4096]

h, w = int(len(iconset_bytes) / (256/2)), 256
canvas = np.zeros((h, w), dtype=np.uint8)

iconset_nibbles = bytearray()
offset = 0x0
for b in iconset_bytes:
    iconset_nibbles.append((b >> 4) | (offset << 4))
    iconset_nibbles.append((b & 0xf) | (offset << 4))

i_sprite = 0
block_size = 16
pixel_count = block_size ** 2
for i in range(0, len(iconset_nibbles), pixel_count):
    sprite = iconset_nibbles[i:i+pixel_count]

    x = i_sprite * block_size % w
    y = block_size * (i_sprite * block_size // w)
    view = canvas[y:y+block_size, x:x+block_size]
    view[:] = np.frombuffer(sprite, dtype=np.uint8).reshape(block_size, block_size)

    i_sprite += 1

palette_offsets = [
    0xb_ec68,
    0xb_eda8,
    0xb_eee8,
    0xb_f028,
    0xb_f168,
]

for i, palette_offset in enumerate(palette_offsets):
    palette = data[palette_offset:palette_offset+320]
    p = np.frombuffer(palette, dtype=np.uint8).reshape((80, 4))
    p = p.astype(np.float32)[:, :3] / 255
    p = np.fliplr(p)  # BGR->RGB
    cmap = matplotlib.colors.ListedColormap(p)
    color_canvas = np.round(255 * cmap(canvas)[..., :3])
    color_canvas = color_canvas.astype(np.uint8)
    im = Image.fromarray(color_canvas)
    im.save(f"iconset_palette_{i}.png")
