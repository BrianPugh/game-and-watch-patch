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

# C sources
C_SOURCES =  \
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
GNWMANAGER ?= gnwmanager
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

C_DEFS += -DGNW_DEVICE_$(GNW_DEVICE)=1

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
# link script
LDSCRIPT = STM32H7B0VBTx_FLASH.ld

# libraries
LIBS = -lc -lm -lnosys
LIBDIR =
LDFLAGS = $(MCU) -specs=nano.specs -T$(LDSCRIPT) $(LIBDIR) $(LIBS) -Wl,-Map=$(BUILD_DIR)/$(TARGET).map,--cref \
		  -Wl,--gc-sections \
		  -Wl,--undefined=HardFault_Handler \
		  -Wl,--undefined=NMI_Handler \
		  -Wl,--undefined=SMB1_GRAPHIC_MODS \
		  -Wl,--undefined=SMB1_ROM \
		  -Wl,--undefined=bootloader \
		  -Wl,--undefined=bss_rwdata_init \
		  -Wl,--undefined=memcpy_inflate \
		  -Wl,--undefined=prepare_clock_rom \
		  -Wl,--undefined=read_buttons \
		  -Wl,--undefined=rwdata_inflate \

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

$(BUILD_DIR)/%.o: %.c Makefile $(BUILD_DIR)/env | $(BUILD_DIR)
	$(CC) -c $(CFLAGS) -Wa,-a,-ad,-alms=$(BUILD_DIR)/$(notdir $(<:.c=.lst)) $< -o $@

$(BUILD_DIR)/%.o: %.s Makefile $(BUILD_DIR)/env | $(BUILD_DIR)
	$(AS) -c $(CFLAGS) $< -o $@

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) Makefile
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
$(BUILD_DIR)/internal_flash_patched.bin $(BUILD_DIR)/external_flash_patched.bin &: $(BUILD_DIR)/$(TARGET).bin patch.py $(shell find patches -type f)
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
