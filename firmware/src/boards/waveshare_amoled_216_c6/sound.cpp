#include "../../hal/sound_hal.h"
#include "board.h"
#include <Arduino.h>
#include "../../chime.h"

// C6 AMOLED-2.16: ES8311 codec + speaker on the shared I2C/I2S pins (board.h).
// No GPIO amp enable on this board (Waveshare's audio example runs with
// pa: -1), so the chime engine gets a null hook. All codec/I2S/playback work
// lives in the shared engine (../../chime.cpp).

void sound_hal_init(void) {
    const ChimeConfig cfg = {
        SND_I2S_MCLK, SND_I2S_BCLK, SND_I2S_WS, SND_I2S_DOUT, SND_I2S_DIN,
        SND_SAMPLE_RATE, SND_ES8311_ADDR, 65, nullptr
    };
    chime_init(cfg);
}

void sound_hal_play_reset(void)     { chime_play(); }
void sound_hal_play_alert(int kind) { chime_play_alert(kind); }
void sound_hal_set_volume(int pct)   { chime_set_volume(pct); }
void sound_hal_tick(void)           { chime_tick(); }
