/**
 * Mario-only code. In the amalgam build, the RAM of every *mario.o lives in
 * Mario's free RAM (STM32H7B0VBTx_FLASH_amalgam.ld).
 */
#include "main.h"
#include "stock_firmware.h"
#include <string.h>
#include "ips.h"

/**
 * This will most likely be overriden by the patcher.
 */
const uint8_t * const SMB1_ROM = (const uint8_t *)0x90001e60;

uint8_t mario_lzma_heap[LZMA_BUF_SIZE];

#if ENABLE_SMB1_GRAPHIC_MODS
#define SMB1_GRAPHIC_MODS_MAX 8
const uint8_t * const SMB1_GRAPHIC_MODS[SMB1_GRAPHIC_MODS_MAX] = { 0 };
static volatile uint8_t smb1_graphics_idx = 0;

uint8_t * prepare_clock_rom(void *mario_rom, size_t len){
    const uint8_t *patch = NULL;

    if(smb1_graphics_idx > SMB1_GRAPHIC_MODS_MAX){
        smb1_graphics_idx = 0;
    }
    if(smb1_graphics_idx){
        patch = SMB1_GRAPHIC_MODS[smb1_graphics_idx - 1];
    }

    memcpy(smb1_clock_working, mario_rom, len);

    if(patch) {
        // Load custom graphics
        if(IPS_PATCH_WRONG_HEADER == ips_patch(smb1_clock_working, patch)){
            // Attempt a direct graphics override
            memcpy_inflate(smb1_clock_graphics_working, patch, 0x1ec0);
        }
    }
    else{
        smb1_graphics_idx = 0;
    }

    return stock_mario_prepare_clock_rom(smb1_clock_working, len);
}

bool is_menu_open(){
    return *ui_draw_status_addr == 5;
}

gnw_mode_t get_gnw_mode(){
    uint8_t val = *gnw_mode_addr;
    if(val == 0x20) return GNW_MODE_SMB2;
    else if(val == 0x10) return GNW_MODE_SMB1;
    else if(val == 0x08) return GNW_MODE_BALL;
    else return GNW_MODE_CLOCK;
}

void smb1_graphics_buttons(gamepad_t gamepad){
    static gamepad_t gamepad_last = 0;

    gnw_mode_t mode = get_gnw_mode();
    if(mode == GNW_MODE_CLOCK && !is_menu_open()){
        // Actions to only perform on the clock screen
        if((gamepad & GAMEPAD_DOWN) && !(gamepad_last &GAMEPAD_DOWN)){
            // TODO: detect if menu is up or not
            smb1_graphics_idx++;
            // Force a reload
            *(uint8_t *)0x2000103d = 1; // Not sure the difference between setting 1 or 2.
        }
    }

    gamepad_last = gamepad;
}
#endif

/*
 * The clock settings overlay gets a 4th row, TICK ON/OFF, for the clock's
 * once-a-second tick. The setting is bit 0 of nvram id 5 (set: off);
 * clock_tick_sound's only caller goes through tick_sound, which drops the tick
 * while it's off.
 */

#define NES_NOISE_SOUND_QUEUE 0xfd

#define TILE_SPACE 0x24
#define TILE_SPEAKER 0x30
#define TILE_CURSOR 0x3b
// Font tiles: 0-9 are 0x00-0x09, A-Z 0x0a-0x23.
#define T(c) (0x0a + (c) - 'A')
static const uint8_t TICK_ON[] = {
    TILE_SPEAKER, TILE_SPACE, T('T'), T('I'), T('C'), T('K'), TILE_SPACE, T('O'), T('N'),
};
static const uint8_t TICK_OFF[] = {
    TILE_SPEAKER, TILE_SPACE, T('T'), T('I'), T('C'), T('K'), TILE_SPACE, T('O'), T('F'), T('F'),
};
#undef T

// Rows are 32 lines of the 320-wide framebuffer apart.
#define SETTINGS_ROW_STEP (32 * 320)
#define SETTINGS_TICK_ROW 3

static bool tick_off(){
    return stock_mario_nvram_get(MARIO_NVRAM_TICK) & 1;
}

/**
 * Replaces main loop's call to clock_tick_sound. A tick it queued while the
 * setting is off is put back to what was queued before.
 */
void tick_sound(uint8_t *nes_ram){
    uint8_t queued = nes_ram[NES_NOISE_SOUND_QUEUE];
    stock_mario_clock_tick_sound(nes_ram);
    if(tick_off()){
        nes_ram[NES_NOISE_SOUND_QUEUE] = queued;
    }
}

/**
 * Row 3's input. A, LEFT and RIGHT toggle the setting; B closes the overlay
 * and saves.
 */
void tick_input(gamepad_t held, gamepad_t pressed){
    if(pressed & (GAMEPAD_A | GAMEPAD_LEFT | GAMEPAD_RIGHT)){
        stock_mario_nvram_set(MARIO_NVRAM_TICK, stock_mario_nvram_get(MARIO_NVRAM_TICK) ^ 1);
    }
    if(pressed & GAMEPAD_B){
        stock_mario_ui_close_overlay_save_settings();
    }
}

/**
 * settings_clock_handle_input's path for a row past 2 (0x0800c904) branches
 * here, inside its push {r4, r5, r6, lr}: r4 holds the held buttons, r5 the
 * pressed ones.
 */
void __attribute__((naked)) mario_settings_row_input(void){
    __asm("                         \n\
          mov r0, r4                \n\
          mov r1, r5                \n\
          pop {r4, r5, r6, lr}      \n\
          b tick_input              \n\
    ");
}

/**
 * Replaces settings_clock_draw's last tail call (0x0800cea2, after its pop),
 * which draws the SET TIME text, or the time on the SET TIME page. Draws that
 * and then the 4th row.
 *
 * settings_game_draw branches into the same tail (0x0800cffc) to draw RESET
 * GAME, so the row is only drawn on the clock's overlay: the gnw_mode test
 * ui_render_overlay picks settings_clock_draw with.
 */
void tick_draw(uint8_t *dst, const uint8_t *tiles, uint32_t n){
    stock_mario_ui_draw_tile_string(dst, tiles, n);
    if((*gnw_mode_addr & 0xc2) != 0x02 || *ui_settings_editing_time){
        return;
    }
    uint8_t *row = dst - 16 + SETTINGS_ROW_STEP;  // The text starts 16 px in.
    if(*ui_settings_cursor == SETTINGS_TICK_ROW && !((*ui_blink_counter >> 5) & 1)){
        stock_mario_ui_draw_tile16(row, stock_mario_ui_tile_ptr(TILE_CURSOR));
    }
    if(tick_off()){
        stock_mario_ui_draw_tile_string(row + 16, TICK_OFF, sizeof(TICK_OFF));
    }
    else{
        stock_mario_ui_draw_tile_string(row + 16, TICK_ON, sizeof(TICK_ON));
    }
}
