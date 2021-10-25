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
