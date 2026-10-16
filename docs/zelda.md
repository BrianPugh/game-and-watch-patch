# Stock 4MB Flash Chip

In order to fit other ROMs alongside the stock firmware data, a few sacrifices have to be made, namely:

1. Remove all sleeping images.
2. Remove all languages of Link's Awakening.

With these concessions, the patched firmware uses 1449984 bytes of external flash, which leaves 2744320 bytes free at the end of the chip. 2 of the following 3 will fit at the same time:

1. Oracle of Ages
2. Oracle of Seasons
3. Link's Awakening (DX or non-DX)

With further development, more ROMs will be able to fit in the stock flash, but this is what we have for now.

### Patch Command

Run the following in this repo:

```bash
make clean
make PATCH_PARAMS="--device=zelda --no-la --no-sleep-images" flash
```

### Retro-Go Command

`EXTFLASH_OFFSET` is the "External Firmware Used" value the patcher prints at the end of its run; everything after it is free.

```bash
make clean
make -j8 INTFLASH_BANK=2 EXTFLASH_SIZE=2744320 EXTFLASH_OFFSET=1449984 ADAPTER=stlink GNW_TARGET=zelda flash
```


# Bank Stacking
If you want to run additional homebrew, such as [Zelda3 (LttP)](https://github.com/marian-m12l/game-and-watch-zelda3), in Bank 2, it's possible to have *both* retro-go and the stock firmware in Bank 1.

### Patch Command

Run the following in this repo:

```bash
make clean
make PATCH_PARAMS="--device=zelda --no-la --no-sleep-images --triple-boot" flash
```

This will setup storage in the following way:
1. INTFLASH BANK 1 - First 128KB - Stock Firmware
2. INTFLASH Bank 1 - Second 128KB - Free (put Retro-Go intflash here)
3. INTFLASH BANK 2 - Full 256KB - Free (put Zelda3 intflash here)
4. EXTFLASH OFFSET 0; 1449984 - Stock Firmware
5. EXTFLASH OFFSET 1449984; 1703936 (put Zelda3 extflash here)
6. EXTFLASH OFFSET 3153920; 1040384 Free (put retro-go extflash here)

### Retro-go Command
Run the following in the [sylverb/game-and-watch-retro-go](https://github.com/sylverb/game-and-watch-retro-go) repo:

```bash
make clean
make -j8 INTFLASH_ADDRESS=0x08020000 EXTFLASH_SIZE=1040384 EXTFLASH_OFFSET=3153920 GNW_TARGET=zelda BIG_BANK=0 flash
```

### Zelda3 Command
In the Zelda3 repo, after copying `zelda3.sfc` to `zelda3/tables/zelda3.sfc`, run the following commands:

```bash
make -j8 INTFLASH_BANK=2 EXTFLASH_SIZE=1703936 EXTFLASH_OFFSET=1449984 ADAPTER=stlink GNW_TARGET=zelda flash
```
