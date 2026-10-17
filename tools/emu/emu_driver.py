"""Shared GWemu harness pieces: gwprov profiles and frame-accurate input.

The device-specific scripts (``mario_menu_test.py``, ``zelda_menu_test.py``)
supply the poll address and RAM addresses; see tools/emu/README.md.
"""

import struct
import subprocess
import sys
from pathlib import Path

from gwprov.debug_shell import DebugSession, GwemuGDBBackend
from gwprov.gwemu_manager import instances, screenshot_instance

REPO = Path(__file__).resolve().parents[2]

# Bit layout of the button mask read_buttons returns (stock_firmware_common.h).
BITS = {
    "RIGHT": 0,
    "LEFT": 1,
    "DOWN": 2,
    "UP": 3,
    "START": 4,
    "SELECT": 5,
    "B": 6,
    "A": 7,
    "TIME": 8,
    "PAUSE": 9,
    "GAME": 10,
}

# gwprov's names for the stock blobs a new profile starts with.
STOCK_BLOBS = {
    "mario": {"bank1": "Stock Mario", "bank0": "Mario Assets"},
    "zelda": {"bank1": "Stock Zelda", "bank0": "Zelda Assets"},
}


def mask(buttons: str) -> int:
    """``"LEFT+GAME"`` -> button mask; ``""`` -> 0."""
    return sum(1 << BITS[b] for b in buttons.split("+") if b)


def gwprov(*args, quiet: bool = False):
    exe = Path(sys.executable).parent / "gwprov"
    subprocess.run(
        [str(exe), *map(str, args)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL if quiet else None,
    )


def register_backups(model: str, work: Path):
    """Register the repo's stock backups for ``model`` with gwprov.

    gwprov keeps one backup pair per directory, so with both models in the
    repo root it only finds Mario; each model gets a directory of symlinks.
    """
    folder = work / f"{model}-backups"
    folder.mkdir(parents=True, exist_ok=True)
    for name in (f"internal_flash_backup_{model}.bin", f"flash_backup_{model}.bin"):
        link = folder / name
        if not link.exists():
            link.symlink_to(REPO / name)
    gwprov("backups", "add", folder)


def make_profile(
    profile: Path, bank2: Path, internal: Path, external: Path, model: str = "mario"
):
    """Stock ``model`` profile with our patched images swapped in."""
    if not (profile / "flash-map.yaml").exists():
        register_backups(model, profile.parent)
        gwprov("profile", "create", f"--{model}", profile)
    # Whichever of the stock or our blobs the profile holds.
    for bank, name in [
        *STOCK_BLOBS[model].items(),
        ("bank2", "Erased (0xFF)"),
        ("bank1", "Patched"),
        ("bank0", "Patched"),
        ("bank2", "Test bank2"),
    ]:
        try:
            gwprov("profile", profile, "edit", "remove", bank, name, quiet=True)
        except subprocess.CalledProcessError:
            pass
    for bank, name, image in (
        ("bank1", "Patched", internal),
        ("bank0", "Patched", external),
        ("bank2", "Test bank2", bank2),
    ):
        gwprov("profile", profile, "edit", "add", bank, name, image, "--offset", 0)


def make_bank2(path: Path, kind: str, base: Path = None, spin_pc: int = 0x0810_0008):
    """Bank 2: ``base`` (or erased flash), with a tiny image or erased header at 0."""
    data = bytearray(b"\xff" * 0x40000)
    if base is not None:
        image = base.read_bytes()
        data[: len(image)] = image
    data[:16] = b"\xff" * 16
    if kind == "spin":
        # Initial SP, reset vector -> spin_pc (Thumb), then `b .`.
        data[0:8] = struct.pack("<II", 0x2002_0000, spin_pc | 1)
        data[8:10] = b"\xfe\xe7"
    path.write_bytes(data)


class Driver:
    """Drive the firmware one input poll at a time.

    ``poll_pc`` is the instruction after the firmware's ``bl read_buttons``. A
    breakpoint stays on it for the whole session, and between calls the target
    sits halted there with that frame's input not yet injected. So every poll
    gets an injected value (GWemu's stuck TIME never reaches the firmware), and
    reads and screenshots see a frame boundary.
    """

    def __init__(self, pid: int, out_dir: Path, poll_pc: int):
        row = next(r for r in instances() if r["pid"] == pid)
        host, port = row["gdbEndpoint"].split(":")
        backend = GwemuGDBBackend(host=host, port=int(port))
        backend.open()
        self.dbg = DebugSession(backend, "gwemu", qmp_socket=row["qmpSocket"])
        self.pid = pid
        self.out_dir = out_dir
        self.poll_pc = poll_pc
        self.at_poll = False

    def _wait_poll(self):
        result = self.dbg.wait_stopped(10)
        if not (result.get("stopped") and result["registers"]["pc"] == self.poll_pc):
            raise RuntimeError(f"read_buttons return site not reached: {result}")
        self.at_poll = True

    def run_to_poll(self):
        """Resume a halted target and stop at its next poll."""
        self.dbg.bp(self.poll_pc)
        self.dbg.resume()
        self._wait_poll()

    def _sync(self):
        """Halt at the next poll (on first use, or after the target ran free)."""
        if not self.at_poll:
            self.dbg.halt()
            self.run_to_poll()

    def _release(self, value: int):
        """Inject ``value`` into the current poll and step off the breakpoint."""
        self._sync()
        self.dbg.backend.write_register("r0", value)
        self.dbg.clear_bp(self.poll_pc)
        self.dbg.step()
        self.at_poll = False

    def frames(self, buttons: str = "", n: int = 1):
        """Inject a button mask (e.g. "LEFT+GAME"; "" = nothing) for n frames."""
        value = mask(buttons)
        for _ in range(n):
            self._release(value)
            self.run_to_poll()

    def last_frame(self, buttons: str):
        """Inject one frame and let the target run free (e.g. it resets)."""
        self._release(mask(buttons))
        self.dbg.resume()

    def run_to(self, pc: int):
        """Resume a halted target and stop at ``pc`` (off the poll; e.g. in
        code that resets)."""
        self.dbg.bp(pc)
        self.dbg.resume()
        result = self.dbg.wait_stopped(10)
        self.dbg.clear_bp(pc)
        if not (result.get("stopped") and result["registers"]["pc"] == pc):
            raise RuntimeError(f"0x{pc:08X} not reached: {result}")

    def release_to(self, buttons: str, pc: int):
        """Inject one frame and stop at ``pc``."""
        self._release(mask(buttons))
        self.run_to(pc)

    def tap(self, buttons: str, settle: int = 8):
        self.frames(buttons, 2)
        self.frames("", settle)

    def shot(self, name: str):
        """Screenshot at a frame boundary."""
        self._sync()
        screenshot_instance(pid=self.pid, output=self.out_dir / f"{name}.png")

    def read(self, addr: int, n: int) -> bytes:
        self._sync()
        return self.dbg.backend.read_memory(addr, n)

    def read8(self, addr: int) -> int:
        return self.read(addr, 1)[0]

    def read16(self, addr: int) -> int:
        return struct.unpack("<H", self.read(addr, 2))[0]

    def wait_until(self, condition, what: str, max_frames: int = 600, step: int = 10):
        """Run idle frames until ``condition()``; animations don't take a
        fixed number of frames (relocated builds needed different counts)."""
        for _ in range(0, max_frames, step):
            if condition():
                return
            self.frames("", step)
        raise RuntimeError(f"timed out waiting for {what}")

    def press_until(self, buttons: str, condition, what: str, tries: int = 10):
        """Tap ``buttons`` until ``condition()``; the firmware ignores some
        presses (e.g. GAME during a clock animation)."""
        for _ in range(tries):
            self.tap(buttons, settle=10)
            try:
                return self.wait_until(condition, what, max_frames=120)
            except RuntimeError:
                pass
        raise RuntimeError(f"{buttons} never led to {what}")

    def pc(self) -> int:
        """PC wherever the target is (it may never reach another poll)."""
        if not self.at_poll:
            self.dbg.halt()
        return self.dbg.backend.read_register("pc")


def check(condition: bool, message: str):
    print(("PASS: " if condition else "FAIL: ") + message)
    return condition
