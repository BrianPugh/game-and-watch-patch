
def decompress(data):
    """ Decompresses rwdata used to initialize variables.

    Header format (not included in data) are 16 bytes in a table where:

    Index
    -------------
    0-3    Relative offset of data from header
    4-7    Length of the compressed data in bytes
    8-11   Destination (in RAM) of decompressed data
    12-15  Something else.
    """

    index = 0
    out = bytearray()

    while(index < len(data)):
        opcode = data[index]
        index += 1

        # Opcode parsing
        direct_len = opcode & 0x03
        offset_256 = ((opcode >> 2) & 0x03)
        pattern_len = opcode >> 4

        if direct_len == 0:
            direct_len = data[index] + 3
            index += 1
        assert direct_len > 0
        direct_len -= 1

        if pattern_len == 0xF:
            pattern_len += data[index]
            # pattern_len (not including the +2) can be in range [0, 270]
            index += 1

        # Direct Copy
        for _ in range(direct_len):
            out.append(data[index])
            index += 1

        # Pattern
        if pattern_len > 0:
            offset_add = data[index]
            index += 1

            if offset_256 == 0x03:
                offset_256 = data[index]
                index += 1

            offset = offset_add + offset_256 * 256
            # offset can be in range [0, 0xffff]

            # +2 because anything shorter wouldn't be a pattern.
            for _ in range(pattern_len + 2):
                out.append(out[-offset])

    #for i in range(0, len(out), 4):
    #    val = int.from_bytes(out[i:i+4], 'little')
    #    if 0x9000_0000 <= val <= 0x9010_0000:
    #        print(f"0x{i:06X}: 0x{val:08X}")

    return out


