from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image

h, w = 256, 256
canvas = np.zeros((h, w), dtype=np.uint8)
data = Path("build/decrypt.bin").read_bytes()

offset = 0x0009_8B84

if offset > len(data):
    raise IndexError

tileset = data[offset : offset + 0x1_0000]

i_sprite = 0
block_size = 16
pixel_count = block_size ** 2
for i in range(0, len(tileset), pixel_count):
    sprite = tileset[i : i + pixel_count]

    x = i_sprite * block_size % w
    y = block_size * (i_sprite * block_size // w)
    view = canvas[y : y + block_size, x : x + block_size]
    view[:] = np.frombuffer(sprite, dtype=np.uint8).reshape(block_size, block_size)

    i_sprite += 1

palette_offsets = [
    0xB_EC68,
    0xB_EDA8,
    0xB_EEE8,
    0xB_F028,
    0xB_F168,
]

for i, palette_offset in enumerate(palette_offsets):
    palette = data[palette_offset : palette_offset + 320]
    p = np.frombuffer(palette, dtype=np.uint8).reshape((80, 4))
    p = p.astype(np.float32)[:, :3] / 255
    p = np.fliplr(p)  # BGR->RGB
    cmap = matplotlib.colors.ListedColormap(p)
    color_canvas = np.round(255 * cmap(canvas)[..., :3])
    color_canvas = color_canvas.astype(np.uint8)
    im = Image.fromarray(color_canvas)
    im.save(f"tileset_palette_{i}.png")
    # plt.imshow(canvas, cmap=cmap); plt.show()
