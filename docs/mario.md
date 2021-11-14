# Graphical mods

## SMB1 Rom Hacks
The clock uses the SMB1 ROM for game logic and most graphics. Changing the SMB1 ROM with a ROM Hack that changes these graphics will also influence the clock.

Run `make` to dump the SMB1 ROM to `build/smb1.nes`. Most ROM hacks are provided as an IPS patch file and need to be applied to the ROM via a [ROM patcher](https://www.marcrobledo.com/RomPatcher.js/).

Pass in the path to your patched SMB1 ROM file using the `--smb1` argument.

NOTE: No ROM's or romhack patches will be hosted in this repo.

## Other Clock Graphics Mods
Run `make` to dump the clock tileset to `build/tileset.png`. You can copy and edit this file using any image editing tool. To use your modified tileset, pass in the path via the `--clock-tileset` argument.
