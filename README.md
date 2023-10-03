Custom firmware for the newer Nintendo Game and Watch consoles.

[![Click to play demo](https://i.imgur.com/IxgAVsj.jpeg)](https://gfycat.com/untriedmajesticafricancivet)


# What is this?
This repo contains custom code as well as a patching utility to add additional functionality to the stock Game and Watch firmware. In short, this project allows you to run the firmware your game and watch came with along side [retro-go](https://github.com/kbeckmann/game-and-watch-retro-go).


# Features
* Works correctly with [retro-go](https://github.com/kbeckmann/game-and-watch-retro-go) in internal flash bank 2.
* Press button combination (`LEFT` + `GAME`) to launch retro-go from internal flash bank 2.
* Run `make help` to see all configuration options.

### Mario (`PATCH_PARAMS="--device=mario"`)
* Ability to store the entire firmware in internal flash! No external flash required!
    * Option to remove the "Mario Song" easter egg.
    * Option to remove the 5 sleeping illustrations.
    * LZMA compressed data.
    * Intelligently move as much data to internal firmware as possible.
* Configurable timeouts.
* Ability to play SMB1 ROM hacks via `--smb1=path-to-patched-smb1-rom.nes`
* Ability to dynamically load SMB1 ROM hack sprites for the clock via `--smb1-graphics=path-to-patch.ips`
    * Can add up to 8 additional graphics sets.
    * Cycle through via the down button on the clock screen.
    * Add all your ips files to `ips/` and have the patcher automatically discover them via the flag `--smb1-graphics-glob`
* Dumps SMB1 and SMB2 ROMs that are playable by other emulators.
* See [the mario document for more information](docs/mario.md).

### Zelda (`PATCH_PARAMS="--device=zelda"`)
* No extra features currently implemented, just compatible with retro-go
* External flash savings to come in the future.
* See [the zelda document for more information](docs/zelda.md).

# Usage
Place your `internal_flash_backup_${DEVICE}.bin` and `flash_backup_${DEVICE}.bin` in the root of this
repo. To extract these from your gnw system, see the [game and watch backup repo](https://github.com/ghidraninja/game-and-watch-backup).
For example, if we are patching the `mario` game and watch, we need the files
`internal_flash_backup_mario.bin` and `flash_backup_mario.bin` in the root
directory of this project.

Install python dependencies (>=python3.6 required) via:

```
pip3 install -r requirements.txt
```

Download STM32 Driver files:

```
make download_sdk
```

The default programmer interface is `stlink`, you can chose a different interface via the `ADAPTER` variable. For example, `ADAPTER=rpi`.

NOTE: if you are flashing a 64MB chip, add `LARGE_FLASH=1` to your `make` command!

I recommend pressing the power button at the same time you press enter. Note that the same configuration parameters have to be passed to each `make` command.

For additional configuration options, run `make help`.


### Retro Go (Mario)
Since most people are going to be using this with retro-go, want the minimum amount of external storage used, and don't care about the sleeping images or the mario song easter egg, here are the recommend commands. Note that this uses an undocumented 128KB of internal Bank 1 and requires a [patched version of openocd](https://github.com/kbeckmann/ubuntu-openocd-git-builder) installed.

```
# in this repo
make clean
make PATCH_PARAMS="--device=mario --internal-only" flash_patched

# in the retro-go repo
make clean
make -j8 INTFLASH_BANK=2 flash
```

### Retro Go (Zelda)

This assumes you have upgraded the external flash to something larger than 4MB. See  [the zelda document for using retro-go with the stock 4MB flash chip](docs/zelda.md).

```
# in this repo
make clean
# Note: only set the LARGE_FLASH=1 if you have a >=64MB chip!
make PATCH_PARAMS="--device=zelda" LARGE_FLASH=1 flash_patched

# in the retro-go repo
make clean
# In this example, I'm assuming you have a 64MB flash chip (60 = 64 - 4)
make -j8 EXTFLASH_SIZE_MB=60 EXTFLASH_OFFSET=4194304 INTFLASH_BANK=2 flash
```
## Build and flash using Docker

<details>
  <summary>
    If you are familiar with Docker and prefer a solution where you don't have to manually install toolchains and so on, expand this section and read on.
  </summary>
  To reduce the number of potential pitfalls in installation of various software, a Dockerfile is provided containing everything needed to compile and flash Custom Firmware (CFW) to your Nintendo® Game & Watch™ system.
  This Dockerfile is written tageting an x86-64 machine running Linux.

  Steps to flash from a docker container (running on Linux, e.g. Archlinux or Ubuntu):

  ```bash
  # Go into the docker directory of this repo.
  cd docker/

  # Pull the pre-built docker image.
  docker pull brianpugh/game-and-watch-patch:latest

  # When done, use the image to create a container with the attached docker-compose.yaml file.
  # You have to edit the compose file and set the path to the directory with your firmware backup (volumes section of the file).
  docker compose up -d

  # This will create and run a container game-and-watch-patch.
  # The firmware backup files will be mounted into /tmp/firmware of the container.
  # Now, go inside the container copy the backup files and proceed as described above in the Usage section.
  docker exec -it game-and-watch-patch /bin/bash
  ```

If you run into permission issues, [ensure that your user is in the docker group.](https://docs.docker.com/engine/install/linux-postinstall/)

</details>


# Troubleshooting/FAQ:

### `Error: FSIZE in DCR(1) doesn't match actual capacity.` while flashing.
If you receive this error, you can safely ignore it. It doesn't impact flashing or the final device at all.

### Unable to install python dependency `keystone-engine` on rpi3
If you are unable to install `keystone-engine` on a raspberry pi 3, try:
1. Update the GPU RAM to 16MB from `raspi-config`
2. Build and install keystone-engine from source (should take ~15 minutes):
```
git clone https://github.com/keystone-engine/keystone
cd keystone/bindings/python/
python3 -m pip install .
```

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
