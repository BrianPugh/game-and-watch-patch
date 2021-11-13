""" Dictates device used in Makefile and C parts of the code
"""
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="mario")
    args, _ = parser.parse_known_args()
    device = args.device.upper()
    print(f"-DGNW_DEVICE_{device}=1")
