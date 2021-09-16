
def decompress(data):
    """ Decompresses rwdata used to initialize variables.

    The table at address 0x0801807c has format:
        0-3    Relative offset to this elements location to the initialization function.
               Example:
                    0x0801807c + (0xFFFE9617 - 0x100000000) == 0x8001693
                    # 0x8001693 is the location of the bss_init_function
        The function is then passed in a pointer to the offset 4.
        The function returns a pointer to the end of the inputs it has consumed
        from the table. For example, the lz_decompress function consumes 12 bytes
        from the table. This, combined with the relative offset to the function,
        means that 16 bytes in total of the table are used.

    Header format (not included in data) are 16 bytes in a table where:

    Index
    -------------
    0-3    Relative offset of data from header
    4-7    Length of the compressed data in bytes
    8-11   Destination (in RAM) of decompressed data
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


