#include "chime.h"
#include <Arduino.h>
#include "ESP_I2S.h"
#include "es8311.h"
#include "bell_pcm.h"   // const uint8_t bell_pcm[] / bell_pcm_len — 44.1 kHz 16-bit stereo
#include <math.h>

// Shared ES8311 chime engine. See chime.h. Adapted from the original 2.16
// sound.cpp so the 2.16, 1.8 (and any future ES8311 board) share one copy of
// the codec setup, the embedded PCM, and the non-blocking playback task.

static I2SClass      i2s;
static ChimeConfig   cfg;
static bool          ready   = false;
static volatile bool playing = false;
static es8311_handle_t es_handle = nullptr;   // kept so the volume can be set per play
static int             vol_pct = 80;           // chime_set_volume(); applied when a clip starts

static bool es8311_setup(void) {
    es8311_handle_t es = es8311_create(0, cfg.es8311_addr);   // I2C port 0 (shared Wire bus)
    if (!es) return false;
    es_handle = es;
    // mclk_inverted, sclk_inverted, mclk_from_mclk_pin, mclk_frequency, sample_frequency
    const es8311_clock_config_t clk = {
        false, false, true, cfg.sample_rate * 256, cfg.sample_rate
    };
    if (es8311_init(es, &clk, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16) != ESP_OK) return false;
    es8311_sample_frequency_config(es, clk.mclk_frequency, clk.sample_frequency);
    es8311_microphone_config(es, false);
    es8311_voice_volume_set(es, cfg.volume, NULL);
    return true;
}

// The ES8311 DAC volume register is 0.5 dB per step with 0 dB at 191; the
// driver maps its 0..100 argument to that register as vol*256/100-1. Turn the
// user's percentage into dB first (-30 dB … 0 dB), then into the driver scale.
static int codec_volume_for(int pct) {
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    const float db = -30.0f + 0.30f * (float)pct;
    int reg = 191 + (int)lroundf(db * 2.0f);
    if (reg < 0) reg = 0;
    if (reg > 255) reg = 255;
    int vol = (int)lroundf((reg + 1) * 100.0f / 256.0f);
    if (vol < 1) vol = 1;
    if (vol > 100) vol = 100;
    return vol;
}

static void apply_volume(void) {
    if (es_handle) es8311_voice_volume_set(es_handle, codec_volume_for(vol_pct), NULL);
}

void chime_set_volume(int pct) {
    vol_pct = pct < 0 ? 0 : pct > 100 ? 100 : pct;
}

static void chime_task(void* arg) {
    (void)arg;
    apply_volume();
    if (cfg.amp_enable) cfg.amp_enable(true);
    delay(8);                                  // let the amp settle (avoids turn-on pop)
    i2s.write((uint8_t*)bell_pcm, bell_pcm_len);
    delay(20);
    if (cfg.amp_enable) cfg.amp_enable(false);
    playing = false;
    vTaskDelete(nullptr);
}

// ---- Synthesized alert chime -------------------------------------------------
// A small additive "marimba": fundamental + a quieter 2nd and 3rd partial, a
// 10 ms attack and an exponential decay. Fixed-point throughout (the C6 has no
// FPU): 32-bit phase accumulators index a 1024-entry Q15 sine table; the
// envelope is one Q15 multiply per block. Notes may overlap (the second starts
// while the first still rings), which is what makes it sound like an
// instrument rather than a beeper.
struct AlertNote { uint16_t freq_hz; uint16_t start_ms; uint16_t len_ms; uint8_t amp_q7; };
// amp_q7 64 = half of full scale per note; the two notes overlap while the
// first decays, so their sum stays below full scale and never clips.
static const AlertNote ALERT_NEEDS_YOU[] = { { 659, 0, 620, 64 }, { 880, 190, 760, 64 } };   // E5 → A5
static const AlertNote ALERT_DONE[]      = { { 523, 0, 720, 56 } };                          // C5
#define ALERT_BLOCK       256     // frames per generated block (256 × 2 ch × 16 bit = 1 KB)
#define ALERT_DECAY_MS    260     // envelope time constant

static int16_t sine_q15[1024];
static bool    sine_ready = false;
static volatile int alert_kind = 0;

static void build_sine(void) {
    if (sine_ready) return;
    for (int i = 0; i < 1024; i++)
        sine_q15[i] = (int16_t)(sinf((float)i * 6.28318530718f / 1024.0f) * 32767.0f);
    sine_ready = true;
}

static inline int32_t sine_at(uint32_t phase) { return sine_q15[phase >> 22]; }   // top 10 bits

static void alert_task(void* arg) {
    (void)arg;
    const AlertNote* notes = alert_kind == 0 ? ALERT_NEEDS_YOU : ALERT_DONE;
    const int n_notes = alert_kind == 0 ? 2 : 1;
    const uint32_t sr = (uint32_t)cfg.sample_rate;
    uint32_t total_frames = 0;
    for (int i = 0; i < n_notes; i++) {
        const uint32_t end = (uint32_t)(notes[i].start_ms + notes[i].len_ms) * sr / 1000;
        if (end > total_frames) total_frames = end;
    }
    total_frames += sr / 20;                        // 50 ms of silence to settle the amp

    // Per-block decay factor in Q15: exp(-block_ms / DECAY_MS).
    const float block_ms = 1000.0f * ALERT_BLOCK / (float)sr;
    const int32_t decay_q15 = (int32_t)(expf(-block_ms / (float)ALERT_DECAY_MS) * 32768.0f);
    const uint32_t attack_frames = sr / 100;        // 10 ms

    uint32_t phase[2][3] = {};
    uint32_t inc[2][3];
    int32_t  env[2] = { 0, 0 };                     // Q15 envelope per note
    for (int i = 0; i < n_notes; i++)
        for (int h = 0; h < 3; h++)
            inc[i][h] = (uint32_t)(((uint64_t)notes[i].freq_hz * (h + 1) << 32) / sr);

    static int16_t block[ALERT_BLOCK * 2];
    apply_volume();
    if (cfg.amp_enable) cfg.amp_enable(true);
    delay(8);
    uint32_t frame = 0;
    while (frame < total_frames) {
        for (int f = 0; f < ALERT_BLOCK; f++, frame++) {
            int32_t mix = 0;
            for (int i = 0; i < n_notes; i++) {
                const uint32_t start = (uint32_t)notes[i].start_ms * sr / 1000;
                const uint32_t stop  = start + (uint32_t)notes[i].len_ms * sr / 1000;
                if (frame < start || frame >= stop) continue;
                const uint32_t t = frame - start;
                int32_t e;                                   // Q15 envelope at this frame
                if (t < attack_frames) { e = (int32_t)(32767u * t / attack_frames); env[i] = 32767; }
                else                   { e = env[i]; }
                int32_t s = sine_at(phase[i][0]) * 100 + sine_at(phase[i][1]) * 34 + sine_at(phase[i][2]) * 11;
                s /= 145;                                    // normalise the partial mix
                s = (s * e) >> 15;
                s = (s * notes[i].amp_q7) >> 7;
                mix += s;
                for (int h = 0; h < 3; h++) phase[i][h] += inc[i][h];
            }
            if (mix > 32767) mix = 32767; else if (mix < -32768) mix = -32768;
            block[2 * f] = block[2 * f + 1] = (int16_t)mix;
        }
        for (int i = 0; i < n_notes; i++) env[i] = (env[i] * decay_q15) >> 15;   // once per block
        i2s.write((uint8_t*)block, sizeof(block));
    }
    delay(20);
    if (cfg.amp_enable) cfg.amp_enable(false);
    playing = false;
    vTaskDelete(nullptr);
}

void chime_play_alert(int kind) {
    if (!ready || playing) return;
    build_sine();
    alert_kind = kind ? 1 : 0;
    playing = true;
    if (xTaskCreatePinnedToCore(alert_task, "alert", 4096, nullptr, 1, nullptr, 0) != pdPASS)
        playing = false;
}

bool chime_init(const ChimeConfig& c) {
    cfg = c;
    if (cfg.amp_enable) cfg.amp_enable(false);   // amp off until we play

    i2s.setPins(cfg.bclk, cfg.ws, cfg.dout, cfg.din, cfg.mclk);
    if (!i2s.begin(I2S_MODE_STD, cfg.sample_rate, I2S_DATA_BIT_WIDTH_16BIT,
                   I2S_SLOT_MODE_STEREO, I2S_STD_SLOT_BOTH)) {
        Serial.println("chime: I2S init failed");
        return false;
    }
    if (!es8311_setup()) {
        Serial.println("chime: ES8311 init failed");
        return false;
    }
    ready = true;
    Serial.println("chime: ES8311 ready");
    return true;
}

void chime_play(void) {
    if (!ready || playing) return;
    playing = true;
    if (xTaskCreatePinnedToCore(chime_task, "chime", 4096, nullptr, 1, nullptr, 0) != pdPASS)
        playing = false;   // couldn't spawn — stay silent rather than wedge the flag
}

void chime_tick(void) {}   // playback runs in chime_task; nothing to poll
