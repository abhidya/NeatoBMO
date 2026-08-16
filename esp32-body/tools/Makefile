# Host builds for the coli_mcu test harnesses.
#
#   make -C tools        build all six test binaries into tools/build/
#   make -C tools test   build and run them (all are self-contained; each
#                        generates its own deterministic fixture in a temp
#                        file and needs no arguments)
#
# Mirrors the flags the firmware is held to: -Wall -Wextra -Werror.

CC ?= cc
CFLAGS := -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
          -Wall -Wextra -Werror -I../components/coli_mcu/include
LDLIBS := -lm

MCU := ../components/coli_mcu
BUILD := build

TESTS := test_bmoq test_q4_matvec test_coli_ops test_coli_moe \
         test_coli_olmoe_layer test_coli_generate test_coli_tokenizer \
         test_coli_spm test_coli_gemma test_coli_gemma_generate \
         test_coli_kv_cache test_coli_kv_attention
BINS := $(addprefix $(BUILD)/,$(TESTS))

all: $(BINS)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/test_bmoq: test_bmoq.c $(MCU)/model_format.c $(MCU)/store_file.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_q4_matvec: test_q4_matvec.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/q4_matvec.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_ops: test_coli_ops.c $(MCU)/ops.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_moe: test_coli_moe.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/q4_matvec.c $(MCU)/moe.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_olmoe_layer: test_coli_olmoe_layer.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/q4_matvec.c $(MCU)/ops.c $(MCU)/moe.c \
		$(MCU)/kv_cache.c $(MCU)/olmoe.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_generate: test_coli_generate.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/q4_matvec.c $(MCU)/ops.c $(MCU)/moe.c \
		$(MCU)/kv_cache.c $(MCU)/olmoe.c $(MCU)/tokenizer.c \
		$(MCU)/generate.c $(MCU)/runtime.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_tokenizer: test_coli_tokenizer.c $(MCU)/store_file.c \
		$(MCU)/tokenizer.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_spm: test_coli_spm.c $(MCU)/store_file.c \
		$(MCU)/spm.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_gemma: test_coli_gemma.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/q4_matvec.c $(MCU)/ops.c \
		$(MCU)/gemma.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_gemma_generate: test_coli_gemma_generate.c \
		$(MCU)/model_format.c $(MCU)/store_file.c $(MCU)/q4_matvec.c \
		$(MCU)/ops.c $(MCU)/gemma.c $(MCU)/gemma_generate.c \
		$(MCU)/spm.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_kv_cache: test_coli_kv_cache.c $(MCU)/kv_cache.c \
		$(MCU)/ops.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_kv_attention: test_coli_kv_attention.c \
		$(MCU)/kv_cache.c $(MCU)/ops.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

test: $(BINS)
	@for t in $(BINS); do echo "== $$t"; $$t || exit 1; done

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
