/*
 * Linker script for the amalgam build (--device=amalgam): one ELF serving
 * both stock firmwares in bank 1.
 *
 * The firmwares boot into each other through a reset, so each owns all RAM
 * while it runs and our RAM can't be shared between them:
 *   - *mario.o (mario.c, game_select_mario.c) keep their .data/.bss in
 *     Mario's free RAM, *zelda.o in Zelda's;
 *   - code both run (main.o, game_select.o, ips.o, LzmaDec.o) must have no
 *     RAM; it picks the running firmware's at runtime (MODEL_SELECT);
 *   - everything else (HAL, CMSIS system, crt) runs only in the bootloader,
 *     before either firmware, so its RAM sits in Mario's region.
 * Each firmware's bss_rwdata_init initializes only its own region.
 */

ENTRY(Reset_Handler)

/* Both from the -L path (build/, or build/pass1/ for the first of the 2
 * --sd-bootloader links; scripts/device_from_patch_params.py). device.ld
 * defines FLASH: all of bank 1, or with --sd-bootloader what's left past
 * Zelda's code, and the freed stock code our code goes into. */
INCLUDE device.ld

MEMORY
{
  MARIO_RAM (xrw) : ORIGIN = __MARIO_RAM_ORIGIN__, LENGTH = __MARIO_RAM_LENGTH__
  ZELDA_RAM (xrw) : ORIGIN = __ZELDA_RAM_ORIGIN__, LENGTH = __ZELDA_RAM_LENGTH__
}

SECTIONS
{
  /* STM32H7B0VBTx_FLASH_text.ld, after the freed stock code with --sd-bootloader */
  INCLUDE amalgam_text.ld

  .shared_ram (NOLOAD) :
  {
    *main.o(.data .data* .bss .bss* COMMON)
    *game_select.o(.data .data* .bss .bss* COMMON)
    *ips.o(.data .data* .bss .bss* COMMON)
    *LzmaDec.o(.data .data* .bss .bss* COMMON)
  } >MARIO_RAM
  ASSERT(SIZEOF(.shared_ram) == 0, "code shared by both firmwares can't have RAM (see STM32H7B0VBTx_FLASH_amalgam.ld)")

  _zelda_sidata = LOADADDR(.zelda_data);
  .zelda_data :
  {
    . = ALIGN(4);
    _zelda_sdata = .;
    *zelda.o(.data .data*)
    . = ALIGN(4);
    _zelda_edata = .;
  } >ZELDA_RAM AT> FLASH

  _mario_sidata = LOADADDR(.mario_data);
  .mario_data :
  {
    . = ALIGN(4);
    _mario_sdata = .;
    *mario.o(.data .data*)
    *(.data .data*)    /* bootloader-only */
    . = ALIGN(4);
    _mario_edata = .;
  } >MARIO_RAM AT> FLASH

  .fill :
  {
    __flash_end__ = .;  /* end of our code/data; the patcher places data from here */
    FILL(0x00);
    . = ORIGIN(FLASH) + LENGTH(FLASH) - 1;
    BYTE(0x00)
  } >FLASH

  .zelda_bss (NOLOAD) :
  {
    . = ALIGN(4);
    _zelda_sbss = .;
    *zelda.o(.bss .bss* COMMON)
    . = ALIGN(4);
    _zelda_ebss = .;
  } >ZELDA_RAM

  .mario_bss (NOLOAD) :
  {
    . = ALIGN(4);
    _mario_sbss = .;
    *mario.o(.bss .bss* COMMON)
    *(.bss .bss* COMMON)    /* bootloader-only */
    . = ALIGN(4);
    _mario_ebss = .;
  } >MARIO_RAM

  /DISCARD/ :
  {
    libc.a ( * )
    libm.a ( * )
    libgcc.a ( * )
  }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}
