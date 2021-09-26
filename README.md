Custom firmware for the Game and Watch: Super Mario Bros. console.


[![Click to play demo](https://thumbs.gfycat.com/UntriedMajesticAfricancivet-mobile.jpg)](https://gfycat.com/untriedmajesticafricancivet)


# What is this?
This repo contains custom code as well as a patching utility to add additional functionality to the stock Game and Watch firmware. This is the only custom firmware that allows you to run both the stock firmware as well as retro-go without soldering a higher capacity flash chip!


# Features
* Works correctly with [retro-go](https://github.com/kbeckmann/game-and-watch-retro-go) in internal flash bank 2.

* Press button combination (`LEFT` + `GAME`) to launch retro-go from internal flash bank 2.

* Ability to store the enitre firmware in internal flash! No external flash required!

    * Option to remove the "Mario Song" easter egg.
    * Option to remove the 5 sleeping illustrations.
    * LZMA compressed data.
    * Inteligently move as much data to internal firmware as possible.

* Configurable timeouts.

* Ability to play SMB1 ROM hacks via `--smb1=path-to-patched-smb1-rom`

* Dumps SMB1 and SMB2 ROMs that are playable by other emulators.

* Run `make help` to see all configuration options.


# Usage
Place your `internal_flash_backup.bin` and `flash_backup.bin` in the root of this
repo. To extract these from your gnw system, see the [game and watch backup repo](https://github.com/ghidraninja/game-and-watch-backup)

Install python dependencies (>=python3.6 required) via:

```
pip3 install -r requirements.txt
```

Download STM32 Driver files:

```
make download_sdk
```

To just build and flash, you can just run `make flash`, however it's a bit finicky. You'll probably have better success running the following command (but see the [retro-go section](##retro-go) for suggested usage).:

```
make flash_patched_ext
make flash_patched_int
```

The default programmer interface is `stlink`, you can chose a different interface via the `ADAPTER` variable. For example, `ADAPTER=rpi`.

I recommend pressing the power button at the same time you press enter. Note that the same configuration parameters have to be passed to each `make` command.

For additional configuration options, run `make help`.


### Retro Go
Since most people are going to be using this with retro-go, want the minimum amount of external storage used, and don't care about the sleeping images or the mario song easter egg, here are the recommend commands. Note that this uses an undocumented 128KB of internal Bank 1 and requires a [patched version of openocd](https://github.com/kbeckmann/ubuntu-openocd-git-builder) installed.

```
# in this repo
make clean
make PATCH_PARAMS="--internal-only" flash_patched_int

# in the retro-go repo
make clean
make -j8 INTFLASH_BANK=2 flash
```

# Graphical mods

## SMB1 Rom Hacks
The clock uses the SMB1 ROM for game logic and most graphics. Changing the SMB1 ROM with a ROM Hack that changes these graphics will also influence the clock.

Run `make` to dump the SMB1 ROM to `build/smb1.nes`. Most ROM hacks are provided as an IPS patch file and need to be applied to the ROM via a [ROM patcher](https://www.marcrobledo.com/RomPatcher.js/).

Pass in the path to your patched SMB1 ROM file using the `--smb1` argument.

## Other Clock Graphics Mods
Run `make` to dump the clock tileset to `build/tileset.png`. You can copy and edit this file using any image editng tool. To use your modified tileset, pass in the path via the `--clock-tileset` argument.

# Advanced usage
Other potentially useful make targets are listed below. Note that external flash only needs to be flashed if the patched external binary is greater than zero bytes.

```
make clean
make patch  # Generates the patched bin at build/internal_flash_patched.bin, but doesn't flash
make flash_stock_int
make flash_stock_ext
make flash_patch_int
make flash_patch_ext
```

# TODO
* More custom sprites, icons, and other graphical mods.

# Development
Main stages to developing a feature:
1. Find a place to take control in the stock rom (usually function calls).
2. Add the stock function and its address to `Core/Inc/stock_firmware.h`.
3. Implement your own function, possibly in `Core/Src/main.c`. There's a good chance your custom function will call the function in (2). You will also probably have to add `-Wl,--undefined=my_custom_function` to `LDFLAGS` in the Makefile so that it doesn't get optimized out as unreachable code.
4. Add a patch definition to `patches/patches.py`.

# Journal
This is my first time ever developing patches for a closed source binary. [I documented my journey in hopes that it helps other people](docs/journal.md). If you have any recommendations, tips, tricks, or anything like that, please leave a github issue and I'll update the documentation!



# Acknowledgement

Thanks to the community that made this possible! This repo was built with the help of others. Repos referenced during the development of this project:

* [game-and-watch-retro-go](https://github.com/kbeckmann/game-and-watch-retro-go) by [kbeckmann](https://github.com/kbeckmann)
* [game-and-watch-backup](https://github.com/ghidraninja/game-and-watch-backup) by [ghidraninja](https://github.com/ghidraninja)
* [game-and-watch-base](https://github.com/ghidraninja/game-and-watch-base) by [ghidraninja](https://github.com/ghidraninja)
* [game-and-watch-decrypt](https://github.com/GMMan/game-and-watch-decrypt) by [GMMan](https://github.com/GMMan)
* [game-and-watch-drawing-song-re](https://github.com/jaames/game-and-watch-drawing-song-re/) by [jaames](https://github.com/jaames)

I would also like to thank the [stacksmashing discord](https://discord.gg/zBN3ex8v4p) for all the help (special shoutout to @cyanic)!
