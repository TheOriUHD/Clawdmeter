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
#define KEY_FLIP    "flp"
#define KEY_FACE    "fac"
#define KEY_GLOW    "glw"
#define KEY_CHIME   "chm"
#define KEY_VOLUME  "vol"

static Settings S = {
    .clock        = CLOCK_AUTO,
    .show_battery = true,
    .show_mascot  = true,
    .show_status  = true,
    .sleep        = SLEEP_30M,
    .face_flip    = FLIP_7S,
    .face_default = 0,
    .alert_glow   = true,
    .alert_chime  = CHIME_ALL,
    .volume       = 40,
};

static const uint32_t FLIP_MS[FLIP_MODE_COUNT] = { 0, 3000, 5000, 7000, 10000, 15000, 30000 };

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
    uint8_t flp = p.getUChar(KEY_FLIP,    0xFF);
    uint8_t fac = p.getUChar(KEY_FACE,    0xFF);
    uint8_t glw = p.getUChar(KEY_GLOW,    0xFF);
    uint8_t chm = p.getUChar(KEY_CHIME,   0xFF);
    uint8_t vol = p.getUChar(KEY_VOLUME,  0xFF);
    p.end();

    if (clk < CLOCK_MODE_COUNT) S.clock = clk;
    if (bat != 0xFF)            S.show_battery = bat != 0;
    if (mas != 0xFF)            S.show_mascot  = mas != 0;
    if (sta != 0xFF)            S.show_status  = sta != 0;
    if (slp < SLEEP_MODE_COUNT) S.sleep = slp;
    if (flp < FLIP_MODE_COUNT)  S.face_flip = flp;
    if (fac != 0xFF && fac <= 4) S.face_default = fac;
    if (glw != 0xFF)            S.alert_glow  = glw != 0;
    if (chm < CHIME_MODE_COUNT) S.alert_chime = chm;
    if (vol <= 100)             S.volume = vol;

    apply_sleep();
    Serial.printf("Settings: clock=%s battery=%d mascot=%d status=%d sleep=%s flip=%s face=%u "
                  "glow=%d chime=%s volume=%u\n",
        settings_clock_label(S.clock), S.show_battery, S.show_mascot, S.show_status,
        settings_sleep_label(S.sleep), settings_face_flip_label(S.face_flip), S.face_default,
        S.alert_glow, settings_alert_chime_label(S.alert_chime), S.volume);
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

void settings_set_face_flip(uint8_t mode) {
    if (mode >= FLIP_MODE_COUNT) mode = FLIP_7S;
    S.face_flip = mode;
    put_u8(KEY_FLIP, mode);
}

void settings_set_face_default(uint8_t face) {
    if (face > 4) face = 0;
    S.face_default = face;
    put_u8(KEY_FACE, face);
}

void settings_set_alert_glow(bool v)  { S.alert_glow  = v; put_u8(KEY_GLOW,   v ? 1 : 0); }

void settings_set_alert_chime(uint8_t mode) {
    if (mode >= CHIME_MODE_COUNT) mode = CHIME_ALL;
    S.alert_chime = mode;
    put_u8(KEY_CHIME, mode);
}

void settings_set_volume(uint8_t pct) {
    if (pct > 100) pct = 100;
    S.volume = pct;
    put_u8(KEY_VOLUME, pct);
}

const char* settings_alert_chime_label(uint8_t mode) {
    switch (mode) {
    case CHIME_OFF:       return "Off";
    case CHIME_NEEDS_YOU: return "Needs you";
    case CHIME_ALL:       return "All";
    default:              return "?";
    }
}

uint32_t settings_face_flip_ms(void) {
    return S.face_flip < FLIP_MODE_COUNT ? FLIP_MS[S.face_flip] : FLIP_MS[FLIP_7S];
}

const char* settings_face_flip_label(uint8_t mode) {
    switch (mode) {
    case FLIP_OFF: return "Off";
    case FLIP_3S:  return "3 s";
    case FLIP_5S:  return "5 s";
    case FLIP_7S:  return "7 s";
    case FLIP_10S: return "10 s";
    case FLIP_15S: return "15 s";
    case FLIP_30S: return "30 s";
    default:       return "?";
    }
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
