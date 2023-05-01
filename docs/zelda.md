# Stock 4MB Flash Chip

In order to fit other ROMs alongside the stock firmware data, a few sacrifices have to be made, namely:

1. Remove all sleeping images.
2. Remove all languages of Link's Awakening.

With these consessions, 2 of the following 3 will fit at the same time:

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


```bash
make clean
make -j8 INTFLASH_BANK=2 EXTFLASH_SIZE=1794336 EXTFLASH_OFFSET=860160 ADAPTER=stlink GNW_TARGET=zelda flash
```


# Bank Stacking
If you want to run additional homebrew, such as [Zelda3 (LttP)](https://github.com/marian-m12l/game-and-watch-zelda3), in Bank 2, it's possible to have *both* retro-go and the stock firmware in Bank 1.

### Patch Command

Run the following in this repo:

```bash
make clean
make PATCH_PARAMS="--device=zelda --no-la --no-sleep-images" flash
```

This will setup storage in the following way:
1. INTFLASH BANK 1 - First 128KB - Stock Firmware
2. INTFLASH Bank 1 - Second 128KB - Free (put Retro-Go intflash here)
3. INTFLASH BANK 2 - Full 256KB - Free (put Zelda3 intflash here)
4. EXTFLASH OFFSET 3301376; 794624 Free (put retro-go extflash here)
5. EXTFLASH OFFSET 860160; 1794336 Free (put Zelda3 extflash here)

### Retro-go Command
Run the following in the [sylverb/game-and-watch-retro-go](https://github.com/sylverb/game-and-watch-retro-go) repo:

```bash
make clean
make -j8 INTFLASH_ADDRESS=0x08020000 EXTFLASH_SIZE=794624 EXTFLASH_OFFSET=3301376 GNW_TARGET=zelda BIG_BANK=0 flash
```

### Zelda3 Command
In the Zelda3 repo, after copying `zelda3.sfc` to `zelda3/tables/zelda3.sfc`, run the following commands:

```bash
cd zelda3
make tables/zelda3_assets.dat
cd ..
python3 ./scripts/bundle_all_assets.py
python3 ./scripts/update_all_assets.py
make -j8 INTFLASH_BANK=2 EXTFLASH_SIZE=1703936 EXTFLASH_OFFSET=868352 ADAPTER=stlink GNW_TARGET=zelda flash
```
