#include "settings.h"
#include "idle.h"
#include "idle_cfg.h"
#include <Preferences.h>
#include <Arduino.h>

// Same NVS namespace brightness.cpp uses, so every user preference lives in
// one place. Keys are ≤ 15 chars (NVS limit).
#define PREFS_NS "clawdmeter"
#define KEY_CLOCK   "clk"
#define KEY_BATTERY "bat"
#define KEY_MASCOT  "mas"
#define KEY_STATUS  "sta"
#define KEY_SLEEP   "slp"

static Settings S = {
    .clock        = CLOCK_AUTO,
    .show_battery = true,
    .show_mascot  = true,
    .show_status  = true,
    .sleep        = SLEEP_30M,
};

static const uint32_t SLEEP_MS[SLEEP_MODE_COUNT] = {
    5UL * 60UL * 1000UL,
    15UL * 60UL * 1000UL,
    30UL * 60UL * 1000UL,
    60UL * 60UL * 1000UL,
    0,                       // never
};

static void put_u8(const char* key, uint8_t v) {
    Preferences p;
    p.begin(PREFS_NS, false);
    p.putUChar(key, v);
    p.end();
}

static void apply_sleep(void) {
    idle_set_timeout_ms(settings_sleep_timeout_ms());
}

void settings_init(void) {
    Preferences p;
    p.begin(PREFS_NS, true);
    uint8_t clk = p.getUChar(KEY_CLOCK,   0xFF);
    uint8_t bat = p.getUChar(KEY_BATTERY, 0xFF);
    uint8_t mas = p.getUChar(KEY_MASCOT,  0xFF);
    uint8_t sta = p.getUChar(KEY_STATUS,  0xFF);
    uint8_t slp = p.getUChar(KEY_SLEEP,   0xFF);
    p.end();

    if (clk < CLOCK_MODE_COUNT) S.clock = clk;
    if (bat != 0xFF)            S.show_battery = bat != 0;
    if (mas != 0xFF)            S.show_mascot  = mas != 0;
    if (sta != 0xFF)            S.show_status  = sta != 0;
    if (slp < SLEEP_MODE_COUNT) S.sleep = slp;

    apply_sleep();
    Serial.printf("Settings: clock=%s battery=%d mascot=%d status=%d sleep=%s\n",
        settings_clock_label(S.clock), S.show_battery, S.show_mascot, S.show_status,
        settings_sleep_label(S.sleep));
}

const Settings& settings_get(void) { return S; }

void settings_set_clock(uint8_t mode) {
    if (mode >= CLOCK_MODE_COUNT) mode = CLOCK_OFF;
    S.clock = mode;
    put_u8(KEY_CLOCK, mode);
}

void settings_set_show_battery(bool v) { S.show_battery = v; put_u8(KEY_BATTERY, v ? 1 : 0); }
void settings_set_show_mascot(bool v)  { S.show_mascot  = v; put_u8(KEY_MASCOT,  v ? 1 : 0); }
void settings_set_show_status(bool v)  { S.show_status  = v; put_u8(KEY_STATUS,  v ? 1 : 0); }

void settings_set_sleep(uint8_t mode) {
    if (mode >= SLEEP_MODE_COUNT) mode = SLEEP_30M;
    S.sleep = mode;
    put_u8(KEY_SLEEP, mode);
    apply_sleep();
}

uint32_t settings_sleep_timeout_ms(void) {
    return S.sleep < SLEEP_MODE_COUNT ? SLEEP_MS[S.sleep] : IDLE_TIMEOUT_MS;
}

const char* settings_clock_label(uint8_t mode) {
    switch (mode) {
    case CLOCK_OFF:  return "Off";
    case CLOCK_AUTO: return "Auto";
    case CLOCK_12H:  return "12h";
    case CLOCK_24H:  return "24h";
    default:         return "?";
    }
}

const char* settings_sleep_label(uint8_t mode) {
    switch (mode) {
    case SLEEP_5M:    return "5 min";
    case SLEEP_15M:   return "15 min";
    case SLEEP_30M:   return "30 min";
    case SLEEP_1H:    return "1 hour";
    case SLEEP_NEVER: return "Never";
    default:          return "?";
    }
}
