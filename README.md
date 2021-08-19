Custom firmware for the Game and Watch: Super Mario Bros. console.

# Features
* Works correctly with (retro-go)[https://github.com/kbeckmann/game-and-watch-retro-go] in internal flash bank 2.
* Press button combination (`LEFT` + `A` + `GAME`) to launch retro-go from internal flash bank 2.

# Usage
Place your `internal_flash_backup.bin` and `flash_backup.bin` in the root of this
repo. To extract these from your console, see the (game and watch backup repo)[https://github.com/ghidraninja/game-and-watch-backup]

To just build and flash, run:
```
make flash
```

# Advanced usage
Other potentially useful make targets:

```
make clean
make flash_stock_int
make flash_stock_ext
make flash_patch_int
make flash_patch_ext
```

# TODO
* Figure out safe place in RAM to store global/static variables. The current
  configuration described in the linker file is unsafe, but currently we have
  no global/static variables.
* Maybe slim external flash ROM (remove easter eggs, ROMs, etc) to make room
  for more homebrew.

# Journal
This is my first time ever developing patches for a closed source binary. [I documented my journey in hopes that it helps other people](docs/journal.md). If you have any recommendations, tips, tricks, or anything like that, please leave a github issue and I'll update the documentation!
