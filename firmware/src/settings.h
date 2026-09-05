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

struct Settings {
    uint8_t clock;          // ClockMode
    bool    show_battery;   // battery glyph in the header (boards with a PMU)
    bool    show_mascot;    // corner Clawd in the header
    bool    show_status;    // the "✻ Thinking…" ticker at the bottom
    uint8_t sleep;          // SleepMode
};

void            settings_init(void);   // load from NVS, apply the sleep timeout
const Settings& settings_get(void);

void settings_set_clock(uint8_t mode);
void settings_set_show_battery(bool v);
void settings_set_show_mascot(bool v);
void settings_set_show_status(bool v);
void settings_set_sleep(uint8_t mode);

uint32_t    settings_sleep_timeout_ms(void);   // 0 = never sleep
const char* settings_clock_label(uint8_t mode);
const char* settings_sleep_label(uint8_t mode);
