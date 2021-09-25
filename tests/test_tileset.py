import random

import numpy as np

from patches.tileset import bytes_to_tilemap, tilemap_to_bytes


def test_tileset_auto():
    data = (80 * np.random.rand(160, 256)).astype(np.uint8).tobytes()
    palette = random.randbytes(80 * 4)

    img = bytes_to_tilemap(data, palette)
    new_data = tilemap_to_bytes(img, palette)

    assert data == new_data
