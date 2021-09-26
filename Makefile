######################################
# target
######################################
TARGET = gw_patch


######################################
# building variables
######################################

PATCH_PARAMS ?=

ifneq (,$(findstring --clock-only, $(PATCH_PARAMS)))
	C_DEFS += -DCLOCK_ONLY
endif

ifneq (,$(findstring --debug, $(PATCH_PARAMS)))
	DEBUG = 1
	C_DEFS += -DDEBUG
endif

ADAPTER ?= stlink
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
Core/Src/main.c \
Core/lzma/LzmaDec.c \

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
OPENOCD ?= openocd
FLASHAPP ?= scripts/flashloader.sh
GDB ?= $(PREFIX)gdb
PYTHON ?= python3

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
C_DEFS =  \
-DUSE_HAL_DRIVER \
-DSTM32H7B0xx \

ifneq (,$(findstring --clock-only, $(PATCH_PARAMS)))
	C_DEFS += -DCLOCK_ONLY
endif

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
		  -Wl,--undefined=bootloader \
		  -Wl,--undefined=memcpy_inflate \
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
	$(PYTHON) scripts/check_env_vars.py $@ $(PATCH_PARAMS)

FORCE: ;


.EXPORT_ALL_VARIABLES:

reset:
	$(OPENOCD) -f openocd/interface_$(ADAPTER).cfg -c "init; reset; exit"
.PHONY: reset

erase_int:
	$(OPENOCD) -f openocd/interface_$(ADAPTER).cfg -c "init; halt; flash erase_address 0x08000000 131072; resume; exit"
.PHONY: erase_int

$(BUILD_DIR)/dummy.bin:
	$(PYTHON) -c "with open('$@', 'wb') as f: f.write(b'\xFF'*1048576)"

erase_ext: $(BUILD_DIR)/dummy.bin
	$(FLASHAPP) $(ADAPTER) $<
	make reset
.PHONY: erase_ext

dump_ext:
	$(OPENOCD) -f openocd/interface_$(ADAPTER).cfg -c "init; halt; dump_image \"dump_ext.bin\" 0x90000000 0x100000; resume; exit;"
.PHONY: dump_ext

flash_stock_int: internal_flash_backup.bin
	$(OPENOCD) -f openocd/interface_"$(ADAPTER)".cfg \
		-c "init; halt;" \
		-c "program $< 0x08000000 verify;" \
		-c "reset; exit;"
.PHONY: flash_stock_int

flash_stock_ext: flash_backup.bin
	$(FLASHAPP) $(ADAPTER) $<
	make reset
.PHONY: flash_stock_ext

flash_stock: flash_stock_int flash_stock_ext reset
.PHONY: flash_stock

$(BUILD_DIR)/internal_flash_patched.bin $(BUILD_DIR)/external_flash_patched.bin &: $(BUILD_DIR)/$(TARGET).bin patch.py $(shell find patches -type f)
	$(PYTHON) patch.py $(PATCH_PARAMS)

patch: $(BUILD_DIR)/internal_flash_patched.bin $(BUILD_DIR)/external_flash_patched.bin
.PHONY: patch

flash_patched_int: build/internal_flash_patched.bin
	$(OPENOCD) -f openocd/interface_"$(ADAPTER)".cfg \
		-c "init; halt;" \
		-c "program $< 0x08000000 verify;" \
		-c "reset; exit;"
.PHONY: flash_patched_int

flash_patched_ext: build/external_flash_patched.bin
	$(FLASHAPP) $(ADAPTER) $<
	make reset
.PHONY: flash_patched_ext

flash_patched: flash_patched_int flash_patched_ext reset
.PHONY: flash_patched

flash: flash_patched
.PHONY: flash

dump:
	arm-none-eabi-objdump -xDSs build/gw_patch.elf > dump.txt && vim dump.txt
.PHONY: dump

# Starts openocd and attaches to the target. To be used with 'flash_intflash_nc' and 'gdb'
openocd:
	$(OPENOCD) -f openocd/interface_$(ADAPTER).cfg -c "init; halt"
.PHONY: openocd

gdb: $(BUILD_DIR)/$(TARGET).elf
	$(GDB) $< -ex "target extended-remote :3333"
.PHONY: gdb

start_bank_2:
	$(OPENOCD) -f openocd/interface_$(ADAPTER).cfg \
		-c 'init; reset halt' \
		-c 'set MSP 0x[string range [mdw 0x08100000] 12 19]' \
		-c 'set PC 0x[string range [mdw 0x08100004] 12 19]' \
		-c 'echo "Setting MSP -> $$MSP"' \
		-c 'echo "Setting PC -> $$PC"' \
		-c 'reg msp $$MSP' \
		-c 'reg pc $$PC' \
		-c 'resume;exit'
.PHONY: start_bank_2

help:
	@$(PYTHON) patch.py --help
	@echo ""
	@echo "Commandline arguments:"
	@echo "    PATCH_PARAMS - Options to pass to the python patching utility."
	@echo "                   Most options go here and will start with two dashes."
	@echo "    ADAPTER - One of {stlink, jlink, rpi}. Defaults to stlink."
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
