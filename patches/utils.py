from math import ceil

from colorama import Fore, Style


def printi(msg, *args):
    print(Fore.MAGENTA + msg + Style.RESET_ALL, *args)


def printe(msg, *args):
    print(Fore.YELLOW + msg + Style.RESET_ALL, *args)


def printd(msg, *args):
    print(Fore.BLUE + msg + Style.RESET_ALL, *args)


def round_down_word(val):
    return (val // 4) * 4


def round_up_word(val):
    return ceil(val / 4) * 4


def round_down_page(val):
    return (val // 4096) * 4096


def round_up_page(val):
    return ceil(val / 4096) * 4096


def seconds_to_frames(seconds):
    return int(round(60 * seconds))

def fds_crc(data):
    """
    Do not include any existing checksum, not even the blank checksums 00 00 or FF FF.
    The formula will automatically count 2 0x00 bytes without the programmer adding them manually.
    Also, do not include the gap terminator (0x80) in the data.
    If you wish to do so, change sum to 0x0000.
    """
    checksum = 0x8000
    size = len(data)
    for i in range(size + 2):
        if i < size:
            byte = data[i]
        else:
            byte = 0x00

        for bit_index in range(8):
            bit = (byte >> bit_index) & 0x1
            carry = checksum & 0x1
            checksum = (checksum >> 1) | (bit << 15)
            if carry:
                checksum ^= 0x8408
    return checksum.to_bytes(2, "little")

def fds_remove_crc_gaps(rom):
    """Remove each block's CRC padding so it can be played by FDS
    https://wiki.nesdev.org/w/index.php/FDS_disk_format
    """
    offset = 0x0

    def get_block(size, crc_gap=2):
        nonlocal offset
        block = rom[offset : offset + size]
        offset += size + crc_gap
        return block

    disk_info_block = get_block(0x38)

    file_amount_block = get_block(0x2)
    assert file_amount_block[0] == 0x02
    n_files = file_amount_block[1]

    blocks = [disk_info_block, file_amount_block]
    for i in range(n_files):
        file_header_block = get_block(0x10)
        assert file_header_block[0] == 3
        blocks.append(file_header_block)

        file_size = int.from_bytes(file_header_block[13 : 13 + 2], "little")
        file_data_block = get_block(file_size + 1)
        blocks.append(file_data_block)

    out = b"".join(blocks)

    # Zero pad to be 65500 bytes long
    padding = b"\x00" * (65500 - len(out))
    out += padding

    return out
