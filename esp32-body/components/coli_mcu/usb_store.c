#include "coli_mcu.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "usb/msc_host.h"
#include "usb/msc_host_vfs.h"
#include "coli_generate.h"
#include "coli_gemma.h"
#include "coli_gemma_generate.h"
#include "coli_model.h"
#include "coli_olmoe.h"
#include "coli_spm.h"

#define COLI_MOUNT_PATH "/usb"
#define COLI_PROBE_FILE COLI_MOUNT_PATH "/model.bmoq"
#define COLI_TOKENIZER_FILE COLI_MOUNT_PATH "/tokenizer.ctok"
#define COLI_GEMMA_TOKENIZER_FILE COLI_MOUNT_PATH "/tokenizer.cspm"
#define COLI_PROMPT_FILE COLI_MOUNT_PATH "/prompt.txt"
#define COLI_OLMOE_KV_FILE COLI_MOUNT_PATH "/olmoe.kv"
#define COLI_GEMMA_MODEL_DIR \
    COLI_MOUNT_PATH "/neatobmo-models/gemma-3-270m-q8_0"
#define COLI_GEMMA_MODEL_FILE COLI_GEMMA_MODEL_DIR "/model.bmoq"
#define COLI_GEMMA_NESTED_TOKENIZER_FILE COLI_GEMMA_MODEL_DIR "/tokenizer.cspm"
#define COLI_GEMMA_PROMPT_FILE COLI_GEMMA_MODEL_DIR "/prompt.txt"
#define COLI_READ_BUFFER_BYTES (16u * 1024u)
#define COLI_BENCHMARK_BYTES (512u * 1024u)
#define COLI_DEMO_PROMPT_BYTES 192u
#define COLI_OLMOE_CONTEXT_TOKENS 4096u
#define COLI_GEMMA_CONTEXT_TOKENS 16u
#define COLI_DEMO_MAX_NEW_TOKENS 1u
#define COLI_DEMO_WORKSPACE_BYTES (768u * 1024u)
#define COLI_DEMO_DECODED_CHUNK_BYTES 256u
#define COLI_KV_PAGE_BYTES (64u * 1024u)

static const char *TAG = "coli_msc";

typedef enum {
    STORAGE_CONNECTED,
    STORAGE_DISCONNECTED,
} storage_event_kind_t;

typedef struct {
    storage_event_kind_t kind;
    union {
        uint8_t address;
        msc_host_device_handle_t handle;
    } device;
} storage_event_t;

typedef struct {
    msc_host_device_handle_t device;
    msc_host_vfs_handle_t vfs;
} mounted_device_t;

static QueueHandle_t s_events;
static volatile bool s_removed;
static TaskHandle_t s_generate_task;

static bool file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

static const char *model_file_path(void)
{
    if (file_exists(COLI_PROBE_FILE)) return COLI_PROBE_FILE;
    if (file_exists(COLI_GEMMA_MODEL_FILE)) return COLI_GEMMA_MODEL_FILE;
    return NULL;
}

static const char *gemma_tokenizer_file_path(void)
{
    if (file_exists(COLI_GEMMA_TOKENIZER_FILE))
        return COLI_GEMMA_TOKENIZER_FILE;
    if (file_exists(COLI_GEMMA_NESTED_TOKENIZER_FILE))
        return COLI_GEMMA_NESTED_TOKENIZER_FILE;
    return COLI_GEMMA_TOKENIZER_FILE;
}

static void msc_event_callback(const msc_host_event_t *event, void *arg)
{
    (void)arg;
    storage_event_t message;
    if (event->event == MSC_DEVICE_CONNECTED) {
        message.kind = STORAGE_CONNECTED;
        message.device.address = event->device.address;
    } else if (event->event == MSC_DEVICE_DISCONNECTED) {
        s_removed = true;
        message.kind = STORAGE_DISCONNECTED;
        message.device.handle = event->device.handle;
    } else {
        return;
    }
    if (xQueueSend(s_events, &message, 0) != pdTRUE)
        ESP_LOGW(TAG, "dropping MSC event because storage queue is full");
}

static void log_device_info(msc_host_device_handle_t device)
{
    msc_host_device_info_t info;
    esp_err_t err = msc_host_get_device_info(device, &info);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "device info failed: %s", esp_err_to_name(err));
        return;
    }
    uint64_t capacity = (uint64_t)info.sector_count * info.sector_size;
    ESP_LOGI(TAG,
             "MSC BOT device vid=%04x pid=%04x sectors=%" PRIu32
             " sector_bytes=%" PRIu32 " capacity_bytes=%" PRIu64,
             info.idVendor, info.idProduct, info.sector_count, info.sector_size,
             capacity);
    err = msc_host_print_descriptors(device);
    if (err != ESP_OK)
        ESP_LOGW(TAG, "descriptor print failed: %s", esp_err_to_name(err));
}

static void benchmark_file(const char *path)
{
    static const size_t request_sizes[] = {4096, 16 * 1024};
    uint8_t *buffer = malloc(COLI_READ_BUFFER_BYTES);
    if (!buffer) {
        ESP_LOGE(TAG, "no memory for %u-byte benchmark buffer",
                 COLI_READ_BUFFER_BYTES);
        return;
    }

    for (size_t test = 0; test < sizeof(request_sizes) / sizeof(request_sizes[0]); ++test) {
        if (s_removed) break;
        FILE *file = fopen(path, "rb");
        if (!file) {
            ESP_LOGW(TAG, "no %s benchmark file: %s", path, strerror(errno));
            break;
        }
        setvbuf(file, NULL, _IOFBF, COLI_READ_BUFFER_BYTES);
        const size_t request = request_sizes[test];
        size_t total = 0;
        int64_t started = esp_timer_get_time();
        while (total < COLI_BENCHMARK_BYTES && !s_removed) {
            size_t wanted = COLI_BENCHMARK_BYTES - total;
            if (wanted > request) wanted = request;
            size_t got = fread(buffer, 1, wanted, file);
            total += got;
            if (got != wanted) break;
            /* USB transactions stay bounded; CDC and safety tasks get a turn. */
            taskYIELD();
        }
        int64_t elapsed_us = esp_timer_get_time() - started;
        fclose(file);
        if (elapsed_us > 0) {
            uint64_t bytes_per_second = (uint64_t)total * 1000000u / elapsed_us;
            ESP_LOGI(TAG,
                     "sequential_read request=%u bytes total=%u elapsed_us=%" PRId64
                     " bytes_per_second=%" PRIu64,
                     (unsigned)request, (unsigned)total, elapsed_us,
                     bytes_per_second);
        }
    }
    free(buffer);
}

static void probe_bmoq(const char *path)
{
    if (s_removed) return;
    coli_store_t *store = NULL;
    coli_status_t status = coli_store_open_file(path, &store);
    if (status != COLI_OK) return;
    coli_model_t model;
    status = coli_model_open(store, &model);
    if (status == COLI_OK) {
        ESP_LOGI(TAG, "BMOQ ready: tensors=%" PRIu32 " file_bytes=%" PRIu64,
                 model.tensor_count, coli_store_size(store));
        if (model.tensor_count) {
            const bmoq_tensor_t *first = &model.tensors[0];
            ESP_LOGI(TAG,
                     "first tensor id=%" PRIu32 " name=%.*s bytes=%" PRIu64,
                     first->tensor_id, BMOQ_TENSOR_NAME_BYTES, first->name,
                     first->byte_length);
        }
        coli_model_close(&model);
    } else {
        ESP_LOGW(TAG, "model.bmoq rejected by parser: %d", status);
    }
    coli_store_close(store);
}

static bool generate_cancelled(void *context)
{
    (void)context;
    return s_removed;
}

static void generate_yield(void *context)
{
    (void)context;
    taskYIELD();
}

static void generate_log_chunk(void *context, const uint8_t *bytes,
                               size_t byte_count)
{
    (void)context;
    ESP_LOGI(TAG, "offline model chunk: %.*s", (int)byte_count,
             (const char *)bytes);
}

static coli_status_t gemma_spm_encode(
    void *context, coli_store_t *store, const uint8_t *prompt,
    size_t prompt_bytes, uint32_t *token_ids, size_t token_capacity,
    size_t *out_token_count)
{
    (void)store;
    return coli_spm_encode((const coli_spm_t *)context, prompt, prompt_bytes,
                           token_ids, token_capacity, out_token_count,
                           COLI_SPM_ENCODE_ADD_BOS);
}

static coli_status_t gemma_spm_decode(
    void *context, coli_store_t *store, const uint32_t *token_ids,
    size_t token_count, uint8_t *text, size_t text_capacity,
    size_t *out_text_bytes)
{
    (void)store;
    return coli_spm_decode((const coli_spm_t *)context, token_ids, token_count,
                           text, text_capacity, out_text_bytes);
}

static size_t read_prompt(uint8_t *prompt, size_t capacity)
{
    FILE *file = fopen(COLI_PROMPT_FILE, "rb");
    if (!file) file = fopen(COLI_GEMMA_PROMPT_FILE, "rb");
    if (!file) {
        static const char fallback[] = "BMO:";
        size_t bytes = sizeof(fallback) - 1u;
        if (bytes > capacity) bytes = capacity;
        memcpy(prompt, fallback, bytes);
        return bytes;
    }
    size_t bytes = fread(prompt, 1, capacity, file);
    fclose(file);
    return bytes;
}

static void offline_generate_task(void *arg)
{
    (void)arg;
    uint8_t prompt[COLI_DEMO_PROMPT_BYTES];
    size_t prompt_bytes = read_prompt(prompt, sizeof(prompt));
    ESP_LOGI(TAG, "offline generation starting prompt_bytes=%u max_new_tokens=%u",
             (unsigned)prompt_bytes, COLI_DEMO_MAX_NEW_TOKENS);
    coli_store_t *model_store = NULL;
    coli_store_t *tokenizer_store = NULL;
    const char *model_path = model_file_path();
    coli_status_t status = model_path
                               ? coli_store_open_file(model_path, &model_store)
                               : COLI_ERR_IO;
    uint32_t architecture = 0;
    if (status == COLI_OK) {
        coli_model_t model;
        status = coli_model_open(model_store, &model);
        if (status == COLI_OK) {
            architecture = model.config.arch;
            coli_model_close(&model);
        }
    }

    if (status == COLI_OK && architecture == COLI_GEMMA3_ARCH_ID) {
        status = coli_store_open_file(gemma_tokenizer_file_path(),
                                      &tokenizer_store);
        coli_spm_t spm;
        bool spm_open = false;
        if (status == COLI_OK) {
            status = coli_spm_open(tokenizer_store, &spm);
            spm_open = status == COLI_OK;
        }
        coli_gemma_generate_result_t result = {0};
        if (status == COLI_OK) {
            const coli_gemma_tokenizer_t tokenizer = {
                .encode = gemma_spm_encode,
                .decode = gemma_spm_decode,
                .context = &spm,
            };
            const coli_gemma_generate_config_t config = {
                .prompt = prompt,
                .prompt_bytes = prompt_bytes,
                .context_tokens = COLI_GEMMA_CONTEXT_TOKENS,
                .max_prompt_tokens =
                    COLI_GEMMA_CONTEXT_TOKENS - COLI_DEMO_MAX_NEW_TOKENS,
                .max_new_tokens = COLI_DEMO_MAX_NEW_TOKENS,
                .workspace_bytes = COLI_DEMO_WORKSPACE_BYTES,
                .decoded_chunk_bytes = COLI_DEMO_DECODED_CHUNK_BYTES,
                .should_cancel = generate_cancelled,
                .yield = generate_yield,
                .log_chunk = generate_log_chunk,
                .callback_context = NULL,
            };
            status = coli_gemma_generate_with_tokenizer(
                model_store, tokenizer_store, &tokenizer, &config, &result);
        }
        if (spm_open) coli_spm_close(&spm);
        if (status == COLI_OK) {
            ESP_LOGI(TAG,
                     "offline Gemma done prompt_tokens=%u generated=%u "
                     "decoded=%u kv=%u workspace=%u last_token=%" PRIu32,
                     (unsigned)result.prompt_tokens,
                     (unsigned)result.generated_tokens,
                     (unsigned)result.decoded_bytes,
                     (unsigned)result.kv_cache_bytes,
                     (unsigned)result.workspace_bytes, result.last_token_id);
        } else if (status != COLI_ERR_REMOVED) {
            ESP_LOGW(TAG, "offline Gemma unavailable stage=%d status=%d",
                     result.stage, status);
        }
    } else if (status == COLI_OK && architecture == BMOQ_MODEL_ARCH_OLMOE) {
        status = coli_store_open_file(COLI_TOKENIZER_FILE, &tokenizer_store);
        coli_generate_result_t result = {0};
        if (status == COLI_OK) {
            const coli_generate_config_t config = {
                .prompt = prompt,
                .prompt_bytes = prompt_bytes,
                .context_tokens = COLI_OLMOE_CONTEXT_TOKENS,
                .max_prompt_tokens =
                    COLI_OLMOE_CONTEXT_TOKENS - COLI_DEMO_MAX_NEW_TOKENS,
                .max_new_tokens = COLI_DEMO_MAX_NEW_TOKENS,
                .workspace_bytes = COLI_DEMO_WORKSPACE_BYTES,
                .decoded_chunk_bytes = COLI_DEMO_DECODED_CHUNK_BYTES,
                .kv_cache_path = COLI_OLMOE_KV_FILE,
                .kv_page_bytes = COLI_KV_PAGE_BYTES,
                .should_cancel = generate_cancelled,
                .yield = generate_yield,
                .log_chunk = generate_log_chunk,
                .callback_context = NULL,
            };
            status = coli_generate_olmoe_greedy(model_store, tokenizer_store,
                                                &config, &result);
        }
        if (status == COLI_OK) {
            ESP_LOGI(TAG,
                     "offline OLMoE done prompt_tokens=%u generated=%u "
                     "decoded=%u kv=%u resident_kv=%u workspace=%u "
                     "last_token=%" PRIu32,
                     (unsigned)result.prompt_tokens,
                     (unsigned)result.generated_tokens,
                     (unsigned)result.decoded_bytes,
                     (unsigned)result.kv_cache_bytes,
                     (unsigned)result.kv_cache_resident_bytes,
                     (unsigned)result.workspace_bytes, result.last_token_id);
        } else if (status != COLI_ERR_REMOVED) {
            ESP_LOGW(TAG, "offline OLMoE unavailable stage=%d status=%d",
                     result.stage, status);
        }
    } else if (status == COLI_OK) {
        status = COLI_ERR_FORMAT;
        ESP_LOGW(TAG, "offline model architecture unsupported: 0x%08" PRIx32,
                 architecture);
    }

    if (tokenizer_store) coli_store_close(tokenizer_store);
    if (model_store) coli_store_close(model_store);
    if (status == COLI_ERR_REMOVED)
        ESP_LOGW(TAG, "offline generation cancelled on storage removal");
    s_generate_task = NULL;
    vTaskDelete(NULL);
}

static void maybe_start_offline_generate(void)
{
    if (s_generate_task || s_removed) return;
    if (!model_file_path()) {
        ESP_LOGI(TAG,
                 "offline generation waiting for %s or %s", COLI_PROBE_FILE,
                 COLI_GEMMA_MODEL_FILE);
        return;
    }
    if (xTaskCreate(offline_generate_task, "coli_generate", 8192, NULL, 2,
                    &s_generate_task) != pdPASS) {
        s_generate_task = NULL;
        ESP_LOGE(TAG, "offline OLMoE task allocation failed");
    }
}

static esp_err_t mount_device(uint8_t address, mounted_device_t *mounted)
{
    esp_err_t err = msc_host_install_device(address, &mounted->device);
    if (err != ESP_OK) return err;
    log_device_info(mounted->device);

    const esp_vfs_fat_mount_config_t config = {
        .format_if_mount_failed = false,
        .max_files = 2,
        .allocation_unit_size = 4096,
    };
    err = msc_host_vfs_register(mounted->device, COLI_MOUNT_PATH, &config,
                                &mounted->vfs);
    if (err != ESP_OK) {
        msc_host_uninstall_device(mounted->device);
        mounted->device = NULL;
    }
    return err;
}

static void unmount_device(mounted_device_t *mounted)
{
    if (mounted->vfs) {
        esp_err_t err = msc_host_vfs_unregister(mounted->vfs);
        if (err != ESP_OK)
            ESP_LOGW(TAG, "unmount failed: %s", esp_err_to_name(err));
        mounted->vfs = NULL;
    }
    if (mounted->device) {
        esp_err_t err = msc_host_uninstall_device(mounted->device);
        if (err != ESP_OK)
            ESP_LOGW(TAG, "device uninstall failed: %s", esp_err_to_name(err));
        mounted->device = NULL;
    }
}

static void storage_task(void *arg)
{
    (void)arg;
    mounted_device_t mounted = {0};
    storage_event_t event;
    while (true) {
        if (xQueueReceive(s_events, &event, portMAX_DELAY) != pdTRUE) continue;
        if (event.kind == STORAGE_CONNECTED) {
            if (mounted.device) {
                ESP_LOGW(TAG, "ignoring additional MSC device");
                continue;
            }
            s_removed = false;
            esp_err_t err = mount_device(event.device.address, &mounted);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "MSC mount failed: %s", esp_err_to_name(err));
                continue;
            }
            ESP_LOGI(TAG, "MSC mounted at %s", COLI_MOUNT_PATH);
            const char *model_path = model_file_path();
            if (model_path) {
                benchmark_file(model_path);
                probe_bmoq(model_path);
            }
            maybe_start_offline_generate();
        } else if (mounted.device == event.device.handle) {
            ESP_LOGW(TAG, "MSC removed; cancelling storage work");
            s_removed = true;
            unmount_device(&mounted);
        }
    }
}

esp_err_t coli_mcu_start(void)
{
    if (s_events) return ESP_ERR_INVALID_STATE;
    s_events = xQueueCreate(4, sizeof(storage_event_t));
    if (!s_events) return ESP_ERR_NO_MEM;

    const msc_host_driver_config_t config = {
        .create_backround_task = true,
        .task_priority = 5,
        .stack_size = 4096,
        .core_id = tskNO_AFFINITY,
        .callback = msc_event_callback,
        .callback_arg = NULL,
    };
    esp_err_t err = msc_host_install(&config);
    if (err != ESP_OK) {
        vQueueDelete(s_events);
        s_events = NULL;
        return err;
    }
    if (xTaskCreate(storage_task, "coli_store", 4096, NULL, 3, NULL) != pdPASS) {
        msc_host_uninstall();
        vQueueDelete(s_events);
        s_events = NULL;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "MSC class client installed; shared USB host remains app-owned");
    return ESP_OK;
}
