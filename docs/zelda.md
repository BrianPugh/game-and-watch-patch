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
make PATCH_PARAMS="--device=zelda --extended --no-la --no-sleep-images --extended" flash
```



### Retro-Go Command

Run the following in the retro-go repo:

```bash
make clean
make -j8 INTFLASH_BANK=2 EXTFLASH_SIZE=1802240 EXTFLASH_OFFSET=851968 GNW_TARGET=zelda EXTENDED=1 flash
```
