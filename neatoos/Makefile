CROSS_COMPILE ?= arm-none-eabi-
CC := $(CROSS_COMPILE)gcc
OBJCOPY := $(CROSS_COMPILE)objcopy
OBJDUMP := $(CROSS_COMPILE)objdump

BUILD_DIR := build
TARGET := $(BUILD_DIR)/neatoos-raw
ELF := $(TARGET).elf
BIN := $(TARGET).bin
MAP := $(TARGET).map
LST := $(TARGET).lst

CFLAGS := -mcpu=arm926ej-s -marm -ffreestanding -fno-builtin -fno-stack-protector
CFLAGS += -Wall -Wextra -Werror -Os -Iinclude
ASFLAGS := -mcpu=arm926ej-s -marm
LDFLAGS := -nostdlib -Wl,--build-id=none -Wl,-Map,$(MAP)
LDFLAGS += -T linker/linker.ld $(LDFLAGS_EXTRA)

SOURCES := src/startup.S src/main.c
OBJECTS := $(BUILD_DIR)/startup.o $(BUILD_DIR)/main.o

.PHONY: all clean probe-selftest test toolchain-check

all: toolchain-check $(ELF) $(BIN) $(LST)

toolchain-check:
	@command -v $(CC) >/dev/null || { \
		echo "missing $(CC); install an ARM EABI toolchain or set CROSS_COMPILE"; \
		exit 127; \
	}

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/startup.o: src/startup.S | $(BUILD_DIR)
	$(CC) $(ASFLAGS) -c $< -o $@

$(BUILD_DIR)/main.o: src/main.c include/at91sam9xe_min.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJECTS) linker/linker.ld
	$(CC) $(CFLAGS) $(LDFLAGS) $(OBJECTS) -o $@

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(LST): $(ELF)
	$(OBJDUMP) -d $< > $@

probe-selftest:
	python3 tools/probe_generator.py --self-test

test: probe-selftest
	python3 -m pytest ../tests/test_neatoos_phase_a.py

clean:
	rm -rf $(BUILD_DIR)
