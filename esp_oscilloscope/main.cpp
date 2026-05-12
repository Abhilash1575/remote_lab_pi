#include <driver/i2s.h>
#include <Arduino.h>

#define I2S_SAMPLE_RATE    (40000)          // 40kHz fits comfortably in 921600 baud
#define ADC_INPUT          (ADC1_CHANNEL_5)
#define ADC_CHANNEL_NUM    (5)
#define DMA_BUF_COUNT      (8)              // more buffers = more headroom
#define DMA_BUF_LEN        (256)
#define SAMPLES_PER_PACKET (256)

// ── Shared ring buffer between Core 1 (ADC) and Core 0 (Serial) ──
#define RING_SIZE (SAMPLES_PER_PACKET * 32) // 32 packets of headroom
static uint16_t  ringBuf[RING_SIZE];
static volatile uint32_t ringWrite = 0;     // only written by Core 1
static volatile uint32_t ringRead  = 0;     // only written by Core 0

inline bool     ringFull()  { return ((ringWrite + 1) % RING_SIZE) == ringRead; }
inline uint32_t ringAvail() { return (ringWrite - ringRead + RING_SIZE) % RING_SIZE; }

static uint16_t dmaBuffer[DMA_BUF_LEN];
static size_t   bytes_read;

// ── Packet sender ─────────────────────────────────────────────────
void sendPacket(uint16_t* data, uint16_t count) {
    Serial.write(0xAA);
    Serial.write(0x55);
    Serial.write((uint8_t*)&count, 2);
    Serial.write((uint8_t*)data, count * 2);
    Serial.flush();                          // wait until fully sent
}

// ── Core 0 task — only job is sending serial ──────────────────────
void serialTask(void* param) {
    static uint16_t sendBuf[SAMPLES_PER_PACKET];
    for (;;) {
        if (ringAvail() >= SAMPLES_PER_PACKET) {
            for (int i = 0; i < SAMPLES_PER_PACKET; i++) {
                sendBuf[i]  = ringBuf[ringRead];
                ringRead     = (ringRead + 1) % RING_SIZE;
            }
            sendPacket(sendBuf, SAMPLES_PER_PACKET);
        } else {
            vTaskDelay(1);  // yield — don't busy-spin when no data
        }
    }
}

// ── I2S init ──────────────────────────────────────────────────────
void i2sInit() {
    i2s_config_t cfg = {
        .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_ADC_BUILT_IN),
        .sample_rate          = I2S_SAMPLE_RATE,
        .bits_per_sample      = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format       = I2S_CHANNEL_FMT_ONLY_RIGHT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count        = DMA_BUF_COUNT,
        .dma_buf_len          = DMA_BUF_LEN,
        .use_apll             = true,
        .tx_desc_auto_clear   = false,
        .fixed_mclk           = 0
    };
    i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    i2s_set_adc_mode(ADC_UNIT_1, ADC_INPUT);
    adc1_config_channel_atten(ADC_INPUT, ADC_ATTEN_DB_11);
    i2s_adc_enable(I2S_NUM_0);
}

void setup() {
    Serial.begin(921600);
    setCpuFrequencyMhz(240);
    i2sInit();

    // Serial sender runs permanently on Core 0
    // Arduino loop() runs on Core 1 — fully separate
    xTaskCreatePinnedToCore(
        serialTask,     // function
        "SerialTask",   // name
        4096,           // stack size
        NULL,           // param
        2,              // priority (2 = above idle, below Arduino)
        NULL,           // handle
        0               // Core 0
    );
}

// ── Core 1 — only job is reading ADC, never touches Serial ────────
void loop() {
    i2s_read(I2S_NUM_0, dmaBuffer, sizeof(dmaBuffer), &bytes_read, portMAX_DELAY);

    int n = bytes_read / sizeof(uint16_t);
    for (int i = 0; i < n; i++) {
        uint8_t  ch  = (dmaBuffer[i] >> 12) & 0xF;
        uint16_t val = dmaBuffer[i] & 0x0FFF;

        if (ch != ADC_CHANNEL_NUM) continue;

        if (!ringFull()) {
            ringBuf[ringWrite] = val;
            ringWrite = (ringWrite + 1) % RING_SIZE;
        }
        // if ring somehow fills up, drop the sample silently
        // this should not happen with RING_SIZE = 32 packets
    }
}