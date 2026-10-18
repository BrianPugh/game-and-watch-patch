######################################
# target
######################################
TARGET = gw_patch

#######################################
# paths
#######################################
# Build path
BUILD_DIR = build

######################################
# source
######################################

# C sources shared by both models. Model-only sources are added below; their
# names end in the model (the amalgam linker script places their RAM by name).
C_SOURCES =  \
Core/Src/game_select.c \
Core/Src/ips.c \
Core/Src/main.c \
Core/Src/system_stm32h7xx.c \
Core/lzma/LzmaDec.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_cortex.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_gpio.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_pwr.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_rcc.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_rcc_ex.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_rtc.c \
Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_rtc_ex.c \


# ASM sources
ASM_SOURCES =  \


#######################################
# binaries
#######################################
PREFIX = arm-none-eabi-
# The gcc compiler bin path can be either defined in make command via GCC_PATH variable (> make GCC_PATH=xxx)
# either it can be added to the PATH environment variable.
ifdef GCC_PATH
CC = $(GCC_PATH)/$(PREFIX)gcc
AS = $(GCC_PATH)/$(PREFIX)gcc -x assembler-with-cpp
CP = $(GCC_PATH)/$(PREFIX)objcopy
SZ = $(GCC_PATH)/$(PREFIX)size
else
CC = $(PREFIX)gcc
AS = $(PREFIX)gcc -x assembler-with-cpp
CP = $(PREFIX)objcopy
SZ = $(PREFIX)size
endif
HEX = $(CP) -O ihex
BIN = $(CP) -O binary -S
ECHO  = echo

FORCE_PYOCD ?= 0
GNWMANAGER_DEBUG ?= 0
GNWMANAGER_PATH ?= gnwmanager
GNWMANAGERVERBOSITY := $(if $(filter 1,$(GNWMANAGER_DEBUG)),--verbosity debug,)
ifeq ($(FORCE_PYOCD),1)
GNWMANAGER = $(GNWMANAGER_PATH) $(GNWMANAGERVERBOSITY) -b pyocd
else
GNWMANAGER = $(GNWMANAGER_PATH) $(GNWMANAGERVERBOSITY)
endif

PYTHON ?= python3

#######################################
# Detect OS
#######################################
UNAME := $(shell uname)

ifeq ($(UNAME), Darwin)
NOTIFY_COMMAND=say -v Samantha -r 300 \"flash complete\"
endif

ifeq ($(UNAME), Linux)
NOTIFY_COMMAND=echo -en "\007"
endif
######################################
# building variables
######################################

PATCH_PARAMS ?=

GNW_DEVICE := $(shell $(PYTHON) -m scripts.device_from_patch_params $(PATCH_PARAMS))
GNW_DEVICE_LOWER := $(shell echo "$(GNW_DEVICE)" | tr 'A-Z' 'a-z')

MARIO_SOURCES = Core/Src/game_select_mario.c Core/Src/mario.c
ZELDA_SOURCES = Core/Src/game_select_zelda.c Core/Src/zelda.c

# --device=amalgam: one image for both firmwares (patches/amalgam.py).
ifeq ($(GNW_DEVICE),AMALGAM)
C_DEFS += -DGNW_DEVICE_MARIO=1 -DGNW_DEVICE_ZELDA=1
# Its game-select DUAL BOOT entry is how you switch firmware; always on.
C_DEFS += -DGAME_SELECT_DUAL_BOOT
C_SOURCES += $(MARIO_SOURCES) $(ZELDA_SOURCES)
LDSCRIPT = STM32H7B0VBTx_FLASH_amalgam.ld
# Its linker script INCLUDEs the generated device.ld and amalgam_text.ld.
AMALGAM_LDFLAGS = -L$(BUILD_DIR)
# --sd-bootloader: a first link (build/pass1/) tells what our code is, and
# scripts/amalgam_holes.py packs it into freed stock code for the second.
ifneq (,$(findstring --sd-bootloader, $(PATCH_PARAMS)))
LDSCRIPT_DEPS = $(BUILD_DIR)/pass1/device.ld $(BUILD_DIR)/pass1/amalgam_text.ld \
	scripts/amalgam_holes.py patches/amalgam.py
LINK_PASS1 = $(CC) $(OBJECTS) -L$(BUILD_DIR)/pass1 $(LDFLAGS) -o $(BUILD_DIR)/pass1/$(TARGET).elf && \
	$(PYTHON) -m scripts.amalgam_holes $(BUILD_DIR)/$(TARGET).map $(BUILD_DIR)/amalgam_text.ld
else
LDSCRIPT_DEPS = $(BUILD_DIR)/amalgam_text.ld
endif
else
C_DEFS += -DGNW_DEVICE_$(GNW_DEVICE)=1
C_SOURCES += $($(GNW_DEVICE)_SOURCES)
LDSCRIPT = STM32H7B0VBTx_FLASH.ld
endif

# Smaller (~1.8KB), slower LZMA decoder; internal flash matters more than speed.
C_DEFS += -D_LZMA_SIZE_OPT

ifneq (,$(findstring --clock-only, $(PATCH_PARAMS)))
	C_DEFS += -DCLOCK_ONLY
endif

ifneq (,$(findstring --smb1-graphics, $(PATCH_PARAMS)))
	C_DEFS += -DENABLE_SMB1_GRAPHIC_MODS
endif

ifneq (,$(findstring --debug, $(PATCH_PARAMS)))
	DEBUG = 1
	C_DEFS += -DDEBUG
endif

ifneq (,$(findstring --triple-boot, $(PATCH_PARAMS)))
	C_DEFS += -DTRIPLE_BOOT
endif

ifneq (,$(findstring --game-select-dual-boot, $(PATCH_PARAMS)))
	C_DEFS += -DGAME_SELECT_DUAL_BOOT
endif

ifneq (,$(findstring --sd-bootloader, $(PATCH_PARAMS)))
	C_DEFS += -DSD_BOOTLOADER
endif


#######################################
# CFLAGS
#######################################
# cpu
CPU = -mcpu=cortex-m7

# fpu
FPU = -mfpu=fpv5-d16

# float-abi
FLOAT-ABI = -mfloat-abi=hard

# mcu
MCU = $(CPU) -mthumb $(FPU) $(FLOAT-ABI)

# macros for gcc
# AS defines
AS_DEFS =

# C defines
C_DEFS +=  \
-DUSE_HAL_DRIVER \
-DSTM32H7B0xx \

# AS includes
AS_INCLUDES =

# C includes
C_INCLUDES =  \
-ICore/Inc \
-ICore/lzma \
-IDrivers/STM32H7xx_HAL_Driver/Inc \
-IDrivers/STM32H7xx_HAL_Driver/Inc/Legacy \
-IDrivers/CMSIS/Device/ST/STM32H7xx/Include \
-IDrivers/CMSIS/Include


# compile gcc flags
ASFLAGS = $(MCU) $(AS_DEFS) $(AS_INCLUDES) $(OPT) -Wall -fdata-sections -ffunction-sections

CFLAGS = $(MCU) $(C_DEFS) $(C_INCLUDES) $(OPT) -Wall -fdata-sections -ffunction-sections

ifeq ($(DEBUG), 1)
CFLAGS += -g -gdwarf-2 -O0
else
CFLAGS += -Os
endif

# Generate dependency information
CFLAGS += -MMD -MP -MF"$(@:%.o=%.d)"

#######################################
# LDFLAGS
#######################################
# libraries
LIBS = -lc -lm -lnosys
LIBDIR =
LDFLAGS = $(MCU) -specs=nano.specs -T$(LDSCRIPT) $(LIBDIR) $(LIBS) -Wl,-Map=$(BUILD_DIR)/$(TARGET).map,--cref \
		  -Wl,--gc-sections,--emit-relocs \
		  -Wl,--undefined=HardFault_Handler \
		  -Wl,--undefined=NMI_Handler \
		  -Wl,--undefined=SMB1_GRAPHIC_MODS \
		  -Wl,--undefined=SMB1_ROM \
		  -Wl,--undefined=bootloader \
		  -Wl,--undefined=bss_rwdata_init \
		  -Wl,--undefined=settings_row_input \
		  -Wl,--undefined=settings_draw_tail \
		  -Wl,--undefined=mario_settings_row_input \
		  -Wl,--undefined=tick_draw \
		  -Wl,--undefined=tick_sound \
		  -Wl,--undefined=MARIO_GAME_SELECT_DUAL_BOOT_ICON \
		  -Wl,--undefined=mario_game_select_draw \
		  -Wl,--undefined=mario_game_select_handle_input \
		  -Wl,--undefined=mario_game_select_next \
		  -Wl,--undefined=mario_game_select_prev \
		  -Wl,--undefined=ZELDA_GAME_SELECT_DUAL_BOOT_ICON \
		  -Wl,--undefined=zelda_game_select_draw \
		  -Wl,--undefined=zelda_game_select_handle_input \
		  -Wl,--undefined=zelda_game_select_row_step \
		  -Wl,--undefined=loz2_set_chr \
		  -Wl,--undefined=LOZ2_CHR \
		  -Wl,--undefined=memcpy_inflate \
		  -Wl,--undefined=prepare_clock_rom \
		  -Wl,--undefined=read_buttons \
		  -Wl,--undefined=rwdata_inflate \
		  $(AMALGAM_LDFLAGS) \

# These flags all imply --no-sleep-images, leaving sleep_image_inflate unused.
ifeq (,$(filter --no-sleep-images --slim --internal-only --clock-only,$(PATCH_PARAMS)))
LDFLAGS += -Wl,--undefined=sleep_image_inflate
LDFLAGS += -Wl,--undefined=backdrop_inflate
endif

# default action: build all
all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).hex $(BUILD_DIR)/$(TARGET).bin $(BUILD_DIR)/internal_flash_patched.bin


include Makefile.sdk


#######################################
# build the application
#######################################
# list of objects
OBJECTS = $(addprefix $(BUILD_DIR)/,$(notdir $(C_SOURCES:.c=.o)))
vpath %.c $(sort $(dir $(C_SOURCES)))
# list of ASM program objects
OBJECTS += $(addprefix $(BUILD_DIR)/,$(notdir $(ASM_SOURCES:.s=.o)))
vpath %.s $(sort $(dir $(ASM_SOURCES)))
# Stock function addresses (generated by scripts/device_from_patch_params.py).
OBJECTS += $(BUILD_DIR)/stock_symbols.o

$(BUILD_DIR)/%.o: %.c Makefile $(BUILD_DIR)/env | $(BUILD_DIR)
	$(CC) -c $(CFLAGS) -Wa,-a,-ad,-alms=$(BUILD_DIR)/$(notdir $(<:.c=.lst)) $< -o $@

$(BUILD_DIR)/%.o: %.s Makefile $(BUILD_DIR)/env | $(BUILD_DIR)
	$(AS) -c $(CFLAGS) $< -o $@

$(BUILD_DIR)/stock_symbols.o: $(BUILD_DIR)/stock_symbols.s Makefile | $(BUILD_DIR)
	$(AS) -c $(CFLAGS) $< -o $@

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) Makefile $(LDSCRIPT) STM32H7B0VBTx_FLASH_text.ld $(BUILD_DIR)/device.ld $(LDSCRIPT_DEPS)
	$(LINK_PASS1)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@
	$(SZ) $@

$(BUILD_DIR)/%.hex: $(BUILD_DIR)/%.elf | $(BUILD_DIR)
	$(HEX) $< $@

$(BUILD_DIR)/%.bin: $(BUILD_DIR)/%.elf | $(BUILD_DIR)
	$(BIN) $< $@

$(BUILD_DIR):
	mkdir -p $@

# Rebuild if PATCH_PARAMS doesn't match the values when last ran
$(BUILD_DIR)/env: $(BUILD_DIR) scripts/check_env_vars.py FORCE
	$(PYTHON) scripts/check_env_vars.py "$(MAKECMDGOALS)" $@ "$(PATCH_PARAMS)"

FORCE: ;


.EXPORT_ALL_VARIABLES:

##################
# PATCH BUILDING #
##################
# patch.py writes both images, internal first. Grouped targets (&:) need make
# 4.3, and macOS ships 3.81, so the external image hangs off the internal one:
# it's rebuilt when missing or older (e.g. a run died between the 2 writes).
PATCH_DEPS = $(BUILD_DIR)/$(TARGET).bin patch.py \
	$(shell find patches -type f -not -path '*/__pycache__/*') \
	$(wildcard tools/re/*.syms)

$(BUILD_DIR)/internal_flash_patched.bin: $(PATCH_DEPS)
	$(PYTHON) patch.py $(PATCH_PARAMS)

$(BUILD_DIR)/external_flash_patched.bin: $(BUILD_DIR)/internal_flash_patched.bin
	$(PYTHON) patch.py $(PATCH_PARAMS)

patch: $(BUILD_DIR)/internal_flash_patched.bin $(BUILD_DIR)/external_flash_patched.bin
.PHONY: patch

##################
# STOCK FLASHING #
##################
flash_stock_int: internal_flash_backup_$(GNW_DEVICE_LOWER).bin
	$(GNWMANAGER) flash bank1 $< -- start bank1
.PHONY: flash_stock_int

flash_stock_ext: flash_backup_$(GNW_DEVICE_LOWER).bin
	$(GNWMANAGER) flash ext $< -- start bank1
.PHONY: flash_stock_ext

flash_stock: internal_flash_backup_$(GNW_DEVICE_LOWER).bin flash_backup_$(GNW_DEVICE_LOWER).bin
	$(GNWMANAGER) flash ext flash_backup_$(GNW_DEVICE_LOWER).bin \
		-- flash bank1 internal_flash_backup_$(GNW_DEVICE_LOWER).bin \
		-- start bank1
.PHONY: flash_stock

##################
# PATCH FLASHING #
##################
flash_patched_int: build/internal_flash_patched.bin
	$(GNWMANAGER) flash bank1 $< -- start bank1
.PHONY: flash_patched_int

flash_patched_ext: build/external_flash_patched.bin
	if [ -s $< ]; then \
		$(GNWMANAGER) flash ext $< -- start bank1 \
	fi
.PHONY: flash_patched_ext

flash_patched: build/internal_flash_patched.bin build/external_flash_patched.bin
	$(GNWMANAGER) flash ext build/external_flash_patched.bin \
		-- flash bank1 build/internal_flash_patched.bin \
		-- start bank1
.PHONY: flash_patched

flash: flash_patched
.PHONY: flash

# Useful when developing and you get distracted easily
notify:
	@$(NOTIFY_COMMAND)
.PHONY: notify

flash_notify: flash notify
.PHONY: flash_notify

flash_stock_notify: flash_stock notify
.PHONY: flash_stock_notify

help:
	@$(PYTHON) patch.py --help
	@echo ""
	@echo "Commandline arguments:"
	@echo "    PATCH_PARAMS - Options to pass to the python patching utility."
	@echo "                   Most options go here and will start with two dashes."
	@echo ""
	@echo "Example:"
	@echo "    make PATCH_PARAMS=\"--sleep-time=120 --slim\" flash_patched_ext"

#######################################
# clean up
#######################################
clean:
	-rm -fR $(BUILD_DIR)

#######################################
# dependencies
#######################################
-include $(wildcard $(BUILD_DIR)/*.d)

# *** EOF ***
