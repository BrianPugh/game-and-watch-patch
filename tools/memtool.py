#!/usr/bin/env python3
"""
Goal: try and find unused regions of RAM.
"""

import argparse
import pickle
import random
import sys
import termios
import tty
from functools import partial
from pathlib import Path
from time import strftime

import matplotlib.pyplot as plt
import numpy as np
from pyocd.core.exceptions import TransferFaultError
from pyocd.core.helpers import ConnectHelper

time_str = strftime("%Y%m%d-%H%M%S")
auto_int = partial(int, base=0)  # Auto detect input format
ENTER = "\r"

MEM_ADDR = {
    # name: start_addr, size
    "axi_sram_1": (0x2400_0000, 256 << 10),
    "axi_sram_2": (0x2404_0000, 384 << 10),
    "axi_sram_3": (0x240A_0000, 384 << 10),
    "ahb_sram_1": (0x3000_0000, 64 << 10),
    "ahb_sram_2": (0x3001_0000, 64 << 10),
    "srd_sram_1": (0x3800_0000, 32 << 10),
    "dtcm_ram_1": (0x2000_0000, 128 << 10),
    "itcm_ram_1": (0x0000_0000, 64 << 10),
    "backup_ram_1": (0x3880_0000, 4 << 10),
}


def get_char(prompt="", valid=None, echo=True, newline=True):
    """reads a single character"""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        while True:
            sys.stdout.write(prompt)
            sys.stdout.flush()

            tty.setraw(fd)
            char = sys.stdin.read(1)

            if char == "\x03":  # CTRL + C
                sys.exit(1)

            if echo:
                sys.stdout.write(char)
                sys.stdout.flush()

            if valid is None or char in valid:
                return char
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if newline:
            sys.stdout.write("\n")
            sys.stdout.flush()


def zero_runs(a):
    """
    Source: https://stackoverflow.com/a/24892274
    """
    # Create an array that is 1 where a is 0, and pad each end with an extra 0.
    iszero = np.concatenate(([0], np.equal(a, 0).view(np.int8), [0]))
    absdiff = np.abs(np.diff(iszero))
    # Runs start and end where absdiff is 1.
    ranges = np.where(absdiff == 1)[0].reshape(-1, 2)
    return ranges


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
            getattr(self, args.command)(sys.argv[2:])
        else:
            with ConnectHelper.session_with_chosen_probe() as session:
                self.board = session.board
                self.target = self.board.target
                self.target.resume()
                getattr(self, args.command)(sys.argv[2:])

    def clear(self, argv):
        self.target.halt()
        for name, (start, size) in MEM_ADDR.items():
            print(f"Erasing {name}")
            self.target.write_memory_block8(start, b"\x00" * size)
        self.target.reset()
        self.target.resume()

    def capture(self, argv):
        parser = argparse.ArgumentParser(description="Capture memory data from device.")
        parser.add_argument("addr_start")
        args = parser.parse_args(sys.argv[2:3])

        if args.addr_start not in MEM_ADDR:
            parser.add_argument("addr_end", type=auto_int)

        parser.add_argument(
            "--dump",
            action="store_true",
            help="Make a single observation and directly save it as a binary.",
        )
        parser.add_argument(
            "--print",
            action="store_true",
            help="Print the byte(s) as they're captured.",
        )
        parser.add_argument(
            "--analyze", action="store_true", help="Analyze results afterwards."
        )

        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--random",
            action="store_true",
            help="Write random initial data to address range.",
        )
        group.add_argument(
            "--zero",
            action="store_true",
            help="Write zeros initial data to address range.",
        )

        parser.add_argument(
            "--output", "-o", type=Path, default=Path(f"captures/{time_str}.pkl")
        )
        args = parser.parse_args(argv)

        if args.addr_start in MEM_ADDR:
            args.addr_end = MEM_ADDR[args.addr_start][0] + MEM_ADDR[args.addr_start][1]
            args.addr_start = MEM_ADDR[args.addr_start][0]
        else:
            args.addr_start = auto_int(args.addr_start)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        size = args.addr_end - args.addr_start
        samples = []

        def read():
            return bytes(self.target.read_memory_block8(args.addr_start, size))

        def write(data):
            return self.target.write_memory_block8(args.addr_start, data)

        if args.random:
            random_data = random.randbytes(size)
            write(random_data)
            samples.append(random_data)
        elif args.zero:
            zero_data = b"\x00" * size
            write(zero_data)
            samples.append(zero_data)

        ###################
        # Collect samples #
        ###################
        while True:
            char = get_char("Enter command (h for help): ", [ENTER, " ", "h", "r", "q"])
            if char == "h":
                print("Help:")
                print("    Enter or Space - Capture a memory screenshot")
                print("    r - Reset Target")
                print("    q - save and quit")
            elif char == ENTER or char == " ":
                print("Capturing... ", end="", flush=True)
                self.target.halt()
                try:
                    data = read()
                except TransferFaultError as e:
                    print(e)
                    self.target.resume()
                    continue
                self.target.resume()
                print("Captured!")
                samples.append(data)

                if args.print:
                    for i in range(0, size, 16):
                        print(f"0x{args.addr_start + i:08x}:  ", end="")
                        try:
                            for j in range(16):
                                print(f"0x{data[i+j]:02X} ", end="")
                        except IndexError:
                            print("")
                            break
                        print("")

                if args.dump:
                    out = args.output.with_suffix(".bin")
                    out.write_bytes(data)
                    print(f"Saved dump to {out}")
                    return
            elif char == "r":
                print("Reseting Target")
                self.target.reset()
            elif char == "q":
                print("Quitting")
                break
            else:
                raise ValueError(f'Unknown option "{char}"')
        # Serialize
        with open(args.output, "wb") as f:
            pickle.dump(samples, f)
        print(f"Saved session to {args.output}")

        if args.analyze:
            self.analyze([str(args.output), "--show"])

    def analyze(self, argv):
        parser = argparse.ArgumentParser(description="Analyze captured data.")
        parser.add_argument("src", type=Path, help="Load a pkl file for analysis.")
        parser.add_argument("--show", action="store_true", help="Show matplotlib plot")

        args = parser.parse_args(argv)

        with open(args.src, "rb") as f:
            samples = pickle.load(f)

        samples = [np.frombuffer(sample, dtype=np.uint8) for sample in samples]

        COLOR_SAME = np.array([0x71, 0xC4, 0x94], dtype=np.uint8)
        COLOR_DIFF = np.array([0x8A, 0x58, 0x17], dtype=np.uint8)
        COLOR_PAD = np.array([0xFF, 0xFF, 0xFF], dtype=np.uint8)

        start = samples[0]
        width = 1024
        new_len = int(width * np.ceil(len(start) / width))
        padding = np.full(new_len - len(start), -1)
        n_comparisons = len(samples) - 1
        for i, sample in enumerate(samples[1:]):
            i += 1
            diff = start != sample

            free_segs = zero_runs(diff)
            free_segs_lens = free_segs[:, 1] - free_segs[:, 0]
            free_segs_max_idx = free_segs_lens.argmax()

            if args.show:
                diff_padded = np.concatenate((diff, padding))
                diff_padded = diff_padded.reshape(-1, width)
                h, w = diff_padded.shape

                canvas = np.zeros((h, w, 3), dtype=np.uint8)
                canvas[diff_padded == 0] = COLOR_SAME
                canvas[diff_padded == 1] = COLOR_DIFF
                canvas[diff_padded == -1] = COLOR_PAD

                plt.subplot(n_comparisons, 1, i)
                plt.imshow(canvas)
                plt.title(f"Comparison from {i} to 0")

        free_seg_max = free_segs[free_segs_max_idx, :]
        print(
            f"The longest untouched memory segment is inclusive offset {free_seg_max}"
        )

        if args.show:
            plt.show()


if __name__ == "__main__":
    Main()
