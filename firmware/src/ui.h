#pragma once
#include "data.h"
#include "ble.h"

// Screens. Splash ⇄ Usage toggle on tap (as always); Usage → Settings → About
// are pages reached by swiping left (swipe right goes back). See ui.cpp.
enum screen_t {
    SCREEN_SPLASH,
    SCREEN_USAGE,
    SCREEN_SETTINGS,
    SCREEN_ABOUT,
    SCREEN_COUNT,
};

void ui_init(void);
void ui_update(const UsageData* data);
void ui_tick_anim(void);
void ui_show_screen(screen_t screen);
void ui_toggle_splash(void);
screen_t ui_get_current_screen(void);
void ui_update_ble_status(ble_state_t state, const char* name, const char* mac);
void ui_update_battery(int percent, bool charging);

// Re-read settings + brightness into the Settings page rows and re-apply the
// header/status visibility. Called after any setting changes outside the page
// (e.g. the PWR button cycling brightness).
void ui_refresh_settings(void);
