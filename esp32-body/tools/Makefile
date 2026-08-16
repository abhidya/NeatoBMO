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

# The benchmark driver is the same runtime, built to actually finish. -O2 is
# worth ~2.2x over the unoptimized default and was verified to leave the Q4
# matvec output bit-identical; -ffp-contract=off keeps it that way by denying
# the compiler FMA contraction the ESP32 build would not perform.
# _DARWIN_C_SOURCE re-exposes mmap under the strict _POSIX_C_SOURCE above.
HOST_TOOL_CFLAGS := $(CFLAGS) -O2 -ffp-contract=off -D_DARWIN_C_SOURCE

MCU := ../components/coli_mcu
BUILD := build

TESTS := test_bmoq test_q4_matvec test_coli_ops test_coli_moe \
         test_coli_olmoe_layer test_coli_generate test_coli_tokenizer \
         test_coli_spm test_coli_gemma test_coli_gemma_generate \
         test_coli_kv_cache test_coli_kv_attention test_coli_glm52 \
         test_coli_inkling test_bmoq_eval_olmoe
BINS := $(addprefix $(BUILD)/,$(TESTS))
HOST_TOOLS := bmoq-eval-olmoe ctok-encode
HOST_TOOL_BINS := $(addprefix $(BUILD)/,$(HOST_TOOLS))

all: $(BINS) $(HOST_TOOL_BINS)

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
		$(MCU)/glm52.c $(MCU)/glm52_generate.c $(MCU)/generate.c \
		$(MCU)/runtime.c | $(BUILD)
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

$(BUILD)/test_coli_glm52: test_coli_glm52.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/kv_cache.c $(MCU)/ops.c $(MCU)/q4_matvec.c \
		$(MCU)/moe.c $(MCU)/olmoe.c $(MCU)/glm52.c \
		$(MCU)/glm52_generate.c $(MCU)/tokenizer.c $(MCU)/generate.c \
		$(MCU)/runtime.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_coli_inkling: test_coli_inkling.c $(MCU)/model_format.c \
		$(MCU)/store_file.c $(MCU)/kv_cache.c $(MCU)/ops.c \
		$(MCU)/q4_matvec.c $(MCU)/inkling.c \
		$(MCU)/inkling_generate.c | $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

# store_mmap.c replaces store_file.c for the host benchmark only: it serves the
# identical bytes through one memcpy instead of an fseeko+fread syscall pair,
# without loading the model or widening any workspace. The firmware keeps
# store_file.c / usb_store.c and its bounded SSD paging.
$(BUILD)/bmoq-eval-olmoe: bmoq_eval_olmoe.c $(MCU)/model_format.c \
		$(MCU)/store_mmap.c $(MCU)/q4_matvec.c $(MCU)/ops.c \
		$(MCU)/moe.c $(MCU)/kv_cache.c $(MCU)/olmoe.c | $(BUILD)
	$(CC) $(HOST_TOOL_CFLAGS) $^ -o $@ $(LDLIBS)

# Encodes stdin with an exported .ctok so its token ids can be diffed against
# the reference tokenizer. This is how the OLMoE pre-tokenizer divergence
# documented in bmoq_eval/README.md was found.
$(BUILD)/ctok-encode: ctok_encode.c $(MCU)/store_file.c $(MCU)/tokenizer.c \
		| $(BUILD)
	$(CC) $(HOST_TOOL_CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/test_bmoq_eval_olmoe: test_bmoq_eval_olmoe.c \
		$(MCU)/model_format.c $(MCU)/store_file.c $(MCU)/q4_matvec.c \
		$(MCU)/ops.c $(MCU)/moe.c $(MCU)/kv_cache.c $(MCU)/olmoe.c | $(BUILD)
	$(CC) $(CFLAGS) -Wno-unused-function $^ -o $@ $(LDLIBS)

test: $(BINS)
	@for t in $(BINS); do echo "== $$t"; $$t || exit 1; done

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
