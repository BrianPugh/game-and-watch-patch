The goal of this document is to record my journey in attempting to add features to the stock nintendo game and watch firmware. This is my first time writing code to patch firmware like this, and there aren't really too many tutorials online on how to go about this. This document will mostly be in chronological order. I won't really be describing the python parts, but the python scripts essentially just merge together the binaries as we describe. This won't be comprehensive, but it'll attempt to outline enough of the techniques that you could either apply them to this project or another project.



# Intro and Bare-bones Bootloader

I have the 128KB (131,072 byte) internal firmware dumped and ready to be patched. It has the following layout:

```
       0x0 +----------------------+
           |                      |
           |  Nintendo Firmware   |
           |   (103,168 bytes)    |
           |                      |
0x00019300 +----------------------+
           |                      |
           |  Empty Space (0xFF)  |
           |    (27,904 bytes)    |
           |                      |
0x00020000 +----------------------+
```

Officially, this flash bank has 128KB of storage, but unoficially there's actually 256KB. Using the full 256KB is kind of annoying (involves patching openocd), so lets try and stick to the official 128KB.

When analyzing the binary, remember that the STM32H7B0 is a little endiant cortex-m7 device.

Addresses of note that we know right off the bat:

   * `0x0` contains the 4-byte initial Main Stack Pointer (MSP) `0x20011330`. I'll set my MSP to be the same in the linker script, but I'm not sure if this is aboslutely necessary.
   * `0x4` contains the 4-byte address of the Reset Handlers  `0x0801a001`. The flash gets mapped in starting at address `0x08000000`, so this is really pointing to offset `0x1a001` of the binary. Note that the least-significant-bit (LSb) must be `1`. We are going to overwrite this later with  a pointer to our function.

Our first goal is to just get some of our own code running. The easiest way to to overwrite the reset handler address to run our own reset handler. We're going to be inserting our custom code into the empty space of the firmware `0x019300`. 



The general technique of how we patch features in:

1. Functions are called by putting arguments onto the stack, and then jumping to an address. We can instead jump to our function by replacing the address.
2. We can perform whatever actions we want now that we have control.
3. At the end of our actions, we probably want to call the original function (possibly with modified arguments) so that we're just adding functionality.

```
Stock execution:
+----------+       +---------------+
|  Caller  | ----> | some function |
+----------+       +---------------+

Patched execution
+----------+       +--------------------------------------+  probably   +---------------+
|  Caller  | ----> | our function in the empty space area |-----------> | some function |
+----------+       +--------------------------------------+             +---------------+
```



## Linker modifications part 1

We don't exactly have an entry point since we're going to be hopping in and out of our code a lot. So lets remove the entrypoint and the `isr_vector` table from the linker script. I also set the beginning address of our `.text` section to `0x019300` so that it won't collide with the stock rom when we patch them together.

To use linker symbols in our C code, we have to declare things like `extern  uint32_t ._etext` .

Typical memory layout has the stack pointer at the end of ram and it grows towards the RAM base address `0x20000000`.

## bootloader

I'm first going to create a function `void bootloader(void)` that allows us to execute code that's not at `0x08000000` on startup. So I'm first going to define the function `void bootloader(void)` and add the linker flag `-Wl,--undefined=bootloader` in my `Makefile`so that the linker doesn't remove our "unused" function from the final binary.

Mimicing tim's bootloader, this is going to be accomplished by checking for a magic byte and an address to jump to at the last 8 bytes of RAM. If address`0x2001FFF8` contains the arbitray 4-byte magic byte `0x544F4F42` (in ascii, "BOOT"), then jump to the address stored at `0x2001FFFC` .

See the bootloader function in `Core/Src/main.c`. This is heavily mimic'd by the excellent tutorial ["From Zero to main(): Bare metal C"](https://interrupt.memfault.com/blog/zero-to-main-1l.). One thing that i had to debug was that the `.data` wasn't stored directly after the `.text` as it was in the tutorial. I found this out because my global/static variables wern't what I initialized to. I was able to get to the bottom of this by examining symbols via `arm-none-eabi-objdump -xDSs gw_patch.elf > dump.txt` and then searching through the dump (and verifying it against the `gw_patch.bin`).

# Ghidra

Any more advanced functionality is going to be near-impossible without some reverse-engineering software. Ghidra is a suite that will decompile the binary into assembly code, make an attempt to convert it into unannotated C-code, and in general just provides a convenient way of jumping around the code.

Everyone should watch and follow along (stacksmashing's tutorial)[https://www.youtube.com/watch?v=q4CxE5P6RUE].

After completing the tutorial, we can move on to the game and watch binary. [We need to use the SVD files provided by STMicro](https://www.st.com/resource/en/svd/stm32h7_svd.zip). Our device is the `STM32H7B0x.svd`.