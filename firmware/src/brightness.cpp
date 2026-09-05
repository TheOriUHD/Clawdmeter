#include "brightness.h"
#include "idle.h"
#include <Preferences.h>
#include <Arduino.h>

// PWR-button presets. 200 is the historical default (DISPLAY_DEFAULT_BRIGHTNESS).
static const uint8_t PRESETS[] = {64, 128, 200, 255};
#define PRESET_COUNT (sizeof(PRESETS) / sizeof(PRESETS[0]))
#define DEFAULT_LEVEL 200

static uint8_t cur_level = DEFAULT_LEVEL;

static uint8_t clamp_level(int v) {
    if (v < BRIGHTNESS_MIN_LEVEL) v = BRIGHTNESS_MIN_LEVEL;
    if (v > 255) v = 255;
    return (uint8_t)v;
}

void brightness_init(void) {
    Preferences prefs;
    prefs.begin("clawdmeter", true);
    uint8_t lvl = prefs.getUChar("brt_lvl", 0);
    uint8_t idx = prefs.getUChar("brt_idx", 0xFF);   // pre-slider firmware stored a preset index
    prefs.end();

    if (lvl >= BRIGHTNESS_MIN_LEVEL)   cur_level = lvl;
    else if (idx < PRESET_COUNT)       cur_level = PRESETS[idx];
    idle_set_awake_brightness(cur_level);
    Serial.printf("Brightness init: level=%u (%d%%)\n", cur_level, brightness_get_pct());
}

void brightness_preview_level(uint8_t level) {
    cur_level = clamp_level(level);
    idle_set_awake_brightness(cur_level);
}

void brightness_set_level(uint8_t level) {
    brightness_preview_level(level);
    Preferences prefs;
    prefs.begin("clawdmeter", false);
    prefs.putUChar("brt_lvl", cur_level);
    prefs.end();
    Serial.printf("Brightness set: level=%u (%d%%)\n", cur_level, brightness_get_pct());
}

void brightness_cycle(void) {
    // Next preset above the current level; wrap to the first.
    uint8_t next = PRESETS[0];
    for (size_t i = 0; i < PRESET_COUNT; i++) {
        if (PRESETS[i] > cur_level) { next = PRESETS[i]; break; }
    }
    brightness_set_level(next);
}

uint8_t brightness_get(void) { return cur_level; }

int brightness_get_pct(void) {
    int pct = (cur_level * 100 + 127) / 255;
    return pct < 5 ? 5 : pct;
}

uint8_t brightness_pct_to_level(int pct) {
    if (pct < 5) pct = 5;
    if (pct > 100) pct = 100;
    return clamp_level((pct * 255 + 50) / 100);
}
