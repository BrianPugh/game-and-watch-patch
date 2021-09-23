#!/usr/bin/env python3
"""
Goal: try and find unused regions of RAM.
"""

import argparse
import random
import sys
import tty
import termios
import pickle

import numpy as np
import matplotlib.pyplot as plt

from functools import partial
from pathlib import Path
from pyocd.core.helpers import ConnectHelper
from pyocd.flash.file_programmer import FileProgrammer
from time import strftime


time_str = strftime("%Y%m%d-%H%M%S")
auto_int = partial(int, base=0)  # Auto detect input format
ENTER = "\r"


def _get_char(prompt=""):
    """reads a single character"""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def get_char(prompt, valid=None):
    while True:
        char = _get_char(prompt)
        if char == "\x03":  # CTRL + C
            sys.exit(1)
        print("")
        if valid is None or char in valid:
            return char


class Main:
    def __init__(self):
        parser = argparse.ArgumentParser(description="Memory observer.")
        parser.add_argument("command")
        args = parser.parse_args(sys.argv[1:2])

        if not hasattr(self, args.command):
            print("Unrecognized command")
            parser.print_help()
            exit(1)

        if args.command in set(["analyze"]):
            # Commands that don't want an ocd session
            getattr(self, args.command)()
        else:
            with ConnectHelper.session_with_chosen_probe() as session:
                board = session.board
                target = board.target
                target.resume()
                getattr(self, args.command)(board, target)

    def capture(self, board, target):
        parser = argparse.ArgumentParser(description="Capture memory data from device.")
        parser.add_argument("addr_start", type=auto_int)
        parser.add_argument("addr_end", type=auto_int)
        parser.add_argument("--random", action="store_true",
                            help="Write random initial data to address range.")
        parser.add_argument("--output", "-o", type=Path, default=Path(f"captures/{time_str}.pkl"))
        args = parser.parse_args(sys.argv[2:])

        args.output.parent.mkdir(parents=True, exist_ok=True)
        size = args.addr_end - args.addr_start
        mem_original = random.randbytes(size) if args.random else b""

        def read():
            return bytes(target.read_memory_block8(args.addr_start, size))

        def write(data):
            return target.write_memory_block8(args.addr_start, data)

        #######################
        # Perform initial r/w #
        #######################
        if mem_original:
            get_char("Press Enter to load initial memory values", ENTER)
            target.halt()
            write(mem_original)
        else:
            get_char("Press Enter to read initial memory values", ENTER)
            target.halt()
            mem_original = read()
        target.resume()

        ##############################
        # Collect additional samples #
        ##############################
        observations = [mem_original]
        while True:
            char = get_char("Press Enter to capture memory values. \"q\" to quit", [ENTER, "q"])
            if char == ENTER:
                target.halt()
                data = read()
                target.resume()
                observations.append(data)
            elif char == "q":
                break
            else:
                raise ValueError(f"Unknown option \"{char}\"")

        # Serialize
        with open(args.output, "wb") as f:
            pickle.dump(observations, f)

    def analyze(self):
        parser = argparse.ArgumentParser(description="Analyze captured data.")
        parser.add_argument("src", type=Path, help="Load a npz file for analysis.")

        args = parser.parse_args(sys.argv[2:])
        raise NotImplementedError





if __name__ == "__main__":
    Main()
