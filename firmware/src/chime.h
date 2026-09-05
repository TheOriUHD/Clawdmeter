#pragma once
#include <stdint.h>

// Board-agnostic ES8311 + I2S chime engine. A board's sound.cpp fills in a
// ChimeConfig (its I2S pins, codec address, volume, and a power-amp enable
// hook) and calls chime_init() once; chime_play() then streams the embedded
// reset clip (bell_pcm.h) to the codec in a one-shot FreeRTOS task so the LVGL
// loop never blocks. The embedded PCM is 44.1 kHz / 16-bit / stereo, so
// cfg.sample_rate must be 44100 for correct-pitch playback.
//
// amp_enable is the only part that differs structurally between boards: the
// 2.16 toggles a GPIO, the 1.8 drives an XCA9554 expander line. May be null.

typedef void (*amp_enable_fn)(bool on);

struct ChimeConfig {
    int          mclk, bclk, ws, dout, din;  // I2S GPIOs
    int          sample_rate;                // must match the embedded PCM (44100)
    uint8_t      es8311_addr;                // codec I2C address (0x18)
    uint8_t      volume;                     // 0..100
    amp_enable_fn amp_enable;                // power-amp on/off hook (nullable)
};

// Bring up I2S + the ES8311 codec. Returns true on success; false leaves the
// engine inert (chime_play becomes a no-op). Wire/I2C must already be up.
bool chime_init(const ChimeConfig& cfg);

// Queue one playback of the embedded clip (non-blocking). No-op if not ready
// or already playing.
void chime_play(void);

// Playback loudness for both clips, 0..100. Linear in dB on the codec: 0 → -30 dB,
// 100 → 0 dB DAC gain (the analog stage after it is fixed by the board). The
// driver's own 0..100 "volume" is a raw register scale on which 46 meant -37 dB
// — the reason the first alerts were inaudible.
void chime_set_volume(int pct);

// Companion alert: a soft synthesized chime, generated on the fly (no PCM in
// flash, no big buffer — a 1 KB block at a time from a fixed-point sine table,
// so the single-core C6 barely notices). kind 0 = "needs you": two rising
// marimba-like notes (E5 → A5); kind 1 = "done": one gentle note (C5). Quieter
// than the reset bell on purpose. Non-blocking; no-op if not ready or busy.
void chime_play_alert(int kind);

// Currently a no-op (playback runs in its own task); kept for HAL symmetry.
void chime_tick(void);
