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

# Ghidra and adding button macros

Any more advanced functionality is going to be near-impossible without some reverse-engineering software. [Ghidra](https://github.com/NationalSecurityAgency/ghidra) is a suite that will decompile the binary into assembly code, make an attempt to convert it into un-annotated C-code, and in general just provides a convenient way of jumping around the code. By un-annotated C-code, I mean that none of the variables have meaningful names, and some of program flow might look weird or non-standard because ghidra simply made its best attempt at guessing datatypes and patterns that look like loops, if-statements, and the like.

Everyone should watch and follow along [stacksmashing's tutorial](https://www.youtube.com/watch?v=q4CxE5P6RUE).

After completing the tutorial, we can move on to the game and watch binary. [We need to use the SVD files provided by STMicro](https://www.st.com/resource/en/svd/stm32h7_svd.zip). Our device is the `STM32H7B0x.svd`.



## Search for primary input handler

Like tim's firmware, I want to be able to launch a secondary app via a "secret" button combination (LEFT + A + GAME).

So I began clicking through all areas that read from `PORTD.IDR` . A lot of the gamepad buttons are using `PORTC` and `PORTD` ([see pinout here](https://github.com/Upcycle-Electronics/game-and-watch-hardware)). Eventually I came to a function at address `0x08010d48` that was reading and bitshift a lot from these registers. It's currently being called from address `0x08006b52` After looking at it for a while, you can see that it's remapping these inputs into a bitfield and returning it. It seems like an assembly pattern for checking a single bit X in a 32-bit register is to:

1. left bitshift the register (31 - X) times. For example, if you are interested in bit 5, you bitshift left 26 bits.
2. The bit-of-interest is now in the most-significant-bit (MSb) of the register.
3. If we interpret the register as an `int`, the MSb represents the sign-bit, where `0` means positive (including zero) and `1` means negative. We can then make a statement like `if(-1 < my_shifted_val)` to take action depending whether or not the bit-of-interest was set.

I didn't bother looking to see if the registers were configured with pullups or pull downs, but I'm just going to make the  reasonable assumption that the output of this function has bits set to `1` if its corresponding button was pressed; we can revisit this assumption later if needed. By knowing which buttons are connected to which physical pins, which bits of the input register are being checked, and which bits on the output are being set, we can now correct interpret the returned reading. Something else of note: the end of this decompiled function checks if `UP` is pressed, and if so, to clear `DOWN`. It also checks if `RIGHT` is pressed, to clear `LEFT`. These button combinations should be physically impossible, but its nice to see some confirmation in software that our current interpretation is making sense.

Currently it seems like our best bet is to intercept the call to this function.



## Journey to patching in custom code to stock_read_buttons

My first simple attempt didn't work:

```c
/* C Land */
typedef uint32_t gamepad_t;
gamepad_t (* const stock_read_buttons)(void) = 0x08010d48;
gamepad_t read_buttons() {
    return stock_read_buttons();
}
```

```python
# My Python patch definition
patches.append(0x6b52, "read_buttons")
```

The buttons on the device (besides the power button) stopped working while the rest of the system didn't crash, so I'm definitely poking at something relevant, however its not executing as expected. Currently I'm assuming that the button bitmask is a `uint32_t`, but it could easily be a `uint16_t`. However, since the system is little-endian and the registers are 32bits, it __shouldn't__ matter since the values of interest would occupy the exact same bits (the nice thing about little-endian!). I've got some other theories:

1. Maybe the processor is in Thumb/Normal mode when it should be in the other mode.
2. Maybe the jump is being performed wrong
3. Maybe something super simple.

I need to improve my toolset/skills so I can actually step through and see what the processor is doing. I should probably get a debugger running, but just for fun, I'm going to put my patched ROM into ghidra and see if it thinks my current binary is doing something sensible.

And.... it turns out i'm not doing something sensible. In ghidra I went to address `0x08006b52` expecting there to be a branch instruction to my custom `read_buttons` function. However, I just see the address of my custom function (which happens to be `0x080193ed`) in there being interpretted as instructions. My patch code was simply putting the destination address there instead of a proper branch `bl` command. So I was totally patching the incorrect location and was lucky (unlucky?) enough that this address gets interpretted as commands that seem benign in the current application.

So, with this knowledge, lets be a bit more careful. We are trying to patch the address we're jumping to via a `bl` command at `0x08006b52`. However, now obviously, the actual address isn't going to be stored right there; the `bl` instruction actually does a relative jump from the current `PC`.



To confirm our knowledge, lets look at the instruction before we patch it: `0A F0 F9 F8`. In binary:

```
00001010 11110000 11111001 11111000
```

Looking at [section 4.4 of the arm instruction set](https://iitd-plos.github.io/col718/ref/arm-instructionset.pdf) doesn't make much sense. It says that for this instruciton, the first 4 bits are condition: should be `0b1110` (`0xE`) and the next 4 bits should be `0b1011` (`0xB`) since we're branching with a link. This doesn't look like us at all. Oh! It's because we are in Thumb mode (Instruction Info window in ghidra):

```
Instr Context:
   LRset(01,01)        == 0x0
   TMode(00,00)        == 0x1
```

Looking at [slide 11 of this powerpoint](https://apt.cs.manchester.ac.uk/ftp/pub/apt/peve/PEVE05/Slides/05_Thumb.pdf), branches execute in 2 stages, which makes sense because instructions are 2-bytes long in Thumb, so we're actually looking at two instructions that together make up a `bl` command. So lets just look at the first command:

```
00001010 11110000
```

Remember that we're little endian, so `11110000` is the MSB. This looks better, the first 4 bits are `1111`, which agrees with the docs. The `H` bit is `0`, and the three MSb's of the 11-bit offset are `000`. So this instruction says `LR := PC + signextend(offset << 12)` this instruction is located at `0x08006b52`;

> In Thumb state:
>
> - For B, BL, CBNZ, and CBZ instructions, the value of the PC is the address of the current instruction plus 4 bytes.

So `pc = 0x08006b52 + 0x4 = 0x08006b56`, which agrees with the Instruction Info window. The 11-bit offset here is `0x00A` So `LR := 0x08006b56 + 0xA000 = 0x8010b56 `.

Now lets do the second stage:

```
11111001 11111000
```

the first 5 bytes are `11111`, as expected (`H=1`). So now we are computing `PC := LR + (offset << 1)`. The 11-bit offset here is `0x0F9`, and bitshifting by one results in `0x1F2`. So `PC = 0x08010b56 + 0x1F2 = 0x8010d48 `. This is the address of the function its calling, so we successfully interpretted this `bl` instruction! This is important because now I can write a function in python to aid in this computation. The python implementation just computes this automatically from  the offset and the address of the function to jump to.

Now that I fixed my `bl` patching, let's try it again! Still no dice. The final step that finally got it working is to indicate that the `stock_read_button` is a thumb function (ghidra tells us it's a thumb function in the Instruction Info window) by setting the LSb to `1`:

```c
gamepad_t (* const stock_read_buttons)(void) = 0x08010d48;  // Doesn't work, not Thumb
gamepad_t (* const stock_read_buttons)(void) = 0x08010d49;  // Works! Thumb
```



Now that I have the button inputs exposed, I can launch retro-go using something like:

```c
gamepad_t read_buttons() {
    gamepad_t gamepad = 0;
    gamepad = stock_read_buttons();
    if((gamepad & GAMEPAD_LEFT) && (gamepad & GAMEPAD_A) && (gamepad & GAMEPAD_GAME)){
        start_bank_2();
    }
    return gamepad;
}
```



# Misc Assembly Notes

## Instructions ending in "S"

[[From stackoverflow](https://reverseengineering.stackexchange.com/a/4264): The extra `s` character added to the ARM instruction mean that the **APSR** (Application Processor Status Register) will be updated depending on the outcome of the instruction.

- `N == 0`: The result is greater or equal to 0, which is considered positive, and so the `N`(negative) bit is set to 0.
- `Z == 1`: The result is 0, so the `Z` (zero) bit is set to 1.
- `C == 1`: We lost some data because the result did not fit into 32 bits, so the processor indicates this by setting `C` (carry) to 1.
- `V = 0`: From a two's complement signed-arithmetic viewpoint, 0xffffffff really means -1, so the operation we did was really (-1) + 1 = 0. That operation clearly does not overflow, so `V` (overflow) is set to 0.