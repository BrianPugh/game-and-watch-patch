import struct

from .exception import InvalidIPSError


def strip_header(data, shift=-16):
    """Moves all offsets in IPS data by ``shift``"""

    data = bytearray(data)

    idx = 0
    if data[:5] != b"PATCH":
        raise InvalidIPSError
    idx += 5
    while data[idx : idx + 3] != b"EOF":
        p1, p2 = struct.unpack(">BH", data[idx : idx + 3])
        p2 = (p1 << 16) | p2
        p2 += shift

        if p2 < 0:
            raise NotImplementedError(
                "Haven't implemented code for patches that change header."
            )

        p1 = (p2 & (0xFF << 16)) >> 16
        data[idx : idx + 3] = struct.pack(">B", p1) + struct.pack(">H", p2)
        idx += 3

        data_len = struct.unpack(">H", data[idx : idx + 2])[0]
        idx += 2
        if data_len:
            idx += data_len
        else:
            idx += 1

    return bytes(data)
