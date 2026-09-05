#pragma once
#include <stdint.h>
#include <stdbool.h>

// On-device user settings, persisted to NVS (Preferences namespace
// "clawdmeter", alongside brightness). Edited from the touch Settings page
// (ui.cpp) and read wherever the option applies. Storage only — applying a
// change (hiding the battery icon, re-arming the idle timer) is the
// caller's job, except the sleep timeout which is pushed to idle here so
// the two can never drift apart.
//
// Every value is a small integer so the sim's in-memory Preferences shim
// (UChar only) covers it too.

enum ClockMode : uint8_t {
    CLOCK_OFF = 0,     // title reads "Usage"
    CLOCK_AUTO,        // show the time in the host's hour format ("tf" field)
    CLOCK_12H,
    CLOCK_24H,
    CLOCK_MODE_COUNT,
};

enum SleepMode : uint8_t {
    SLEEP_5M = 0,
    SLEEP_15M,
    SLEEP_30M,         // default — matches the previous hard-coded IDLE_TIMEOUT_MS
    SLEEP_1H,
    SLEEP_NEVER,
    SLEEP_MODE_COUNT,
};

// Weekly card: how often it flips between its faces (all models / Fable …).
enum FaceFlip : uint8_t {
    FLIP_OFF = 0,      // stays on the default face; a tap picks (and keeps) a face
    FLIP_3S, FLIP_5S, FLIP_7S, FLIP_10S, FLIP_15S, FLIP_30S,
    FLIP_MODE_COUNT,
};

// Companion alerts: which "your turn" moments chime through the speaker.
enum AlertChime : uint8_t {
    CHIME_OFF = 0,     // never
    CHIME_NEEDS_YOU,   // permission prompts, questions, plan approvals
    CHIME_ALL,         // those plus the end of a long turn
    CHIME_MODE_COUNT,
};

// Companion: how long the Working page stays after Claude finishes before it
// slides home by itself (only after the device moved there on its own).
enum HomeDelay : uint8_t {
    HOME_15S = 0, HOME_30S, HOME_1M, HOME_2M, HOME_5M,
    HOME_STAY,         // never slide back by itself
    HOME_MODE_COUNT,
};

struct Settings {
    uint8_t clock;          // ClockMode
    bool    show_battery;   // battery glyph in the header (boards with a PMU)
    bool    show_mascot;    // corner Clawd in the header
    bool    show_status;    // the "✻ Thinking…" ticker at the bottom
    uint8_t sleep;          // SleepMode
    uint8_t face_flip;      // FaceFlip
    uint8_t face_default;   // Weekly card face shown first / when not flipping: 0 = all models, i = scoped limit i
    bool    auto_switch;    // slide to the Working page when Claude starts working
    bool    alert_glow;     // breathe the orange border when Claude needs you
    uint8_t alert_chime;    // AlertChime
    uint8_t volume;         // 0..100, every clip (chimes + reset bell)
    uint8_t home_delay;     // HomeDelay
};

void            settings_init(void);   // load from NVS, apply the sleep timeout
const Settings& settings_get(void);

void settings_set_clock(uint8_t mode);
void settings_set_show_battery(bool v);
void settings_set_show_mascot(bool v);
void settings_set_show_status(bool v);
void settings_set_sleep(uint8_t mode);
void settings_set_face_flip(uint8_t mode);
void settings_set_face_default(uint8_t face);
void settings_set_auto_switch(bool v);
void settings_set_alert_glow(bool v);
void settings_set_alert_chime(uint8_t mode);
void settings_set_volume(uint8_t pct);
void settings_set_home_delay(uint8_t mode);

uint32_t    settings_sleep_timeout_ms(void);   // 0 = never sleep
uint32_t    settings_face_flip_ms(void);       // 0 = no automatic flipping
const char* settings_clock_label(uint8_t mode);
const char* settings_sleep_label(uint8_t mode);
const char* settings_face_flip_label(uint8_t mode);
const char* settings_alert_chime_label(uint8_t mode);
uint32_t    settings_home_delay_ms(void);          // 0 = stay
const char* settings_home_delay_label(uint8_t mode);
