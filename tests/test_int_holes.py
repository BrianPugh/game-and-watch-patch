from patches.firmware import Device


def make_device(size=0x1000):
    device = Device.__new__(Device)
    device.internal = bytearray(size)
    device.int_holes = []
    device.int_freed = []
    return device


def test_free_int_fills_udf_and_word_aligns():
    device = make_device()
    device.free_int(0x102, 0x20A)
    assert device.int_holes == [[0x104, 0x208]]
    assert device.int_freed == [(0x104, 0x208)]
    assert device.internal[0x104:0x208] == b"\xfe\xde" * ((0x208 - 0x104) // 2)
    assert device.internal[0x100:0x104] == b"\x00" * 4
    assert device.int_hole_space == 0x104


def test_take_int_hole_best_fit():
    device = make_device()
    device.free_int(0x100, 0x200)  # 256 bytes
    device.free_int(0x400, 0x440)  # 64 bytes
    assert device._take_int_hole(50) == 0x400  # Smallest hole that fits; uses 52.
    assert device._take_int_hole(10) == 0x434  # Rounds to 12, exactly what's left.
    assert device._take_int_hole(13) == 0x100  # Small hole is now empty.
    assert device.int_holes == [[0x110, 0x200], [0x440, 0x440]]
    assert device._take_int_hole(0x1000) is None
