#pragma once
#include <stdint.h>

// Runtime board description consumed by board-agnostic code (UI, main loop).
// Each board provides a single BoardCaps instance via board_caps().
//
// Compile-time-only facts (pin numbers, library choice) belong in
// boards/<name>/board.h and never leak into shared code. Anything the UI or
// main loop needs at runtime — display size, optional-feature presence —
// goes here so shared code stays free of #ifdef BOARD_*.
struct BoardCaps {
    const char* name;        // human-readable, e.g. "Waveshare AMOLED 2.16"

    int16_t width;           // active display width in pixels
    int16_t height;          // active display height in pixels

    uint8_t button_count;    // 1 = primary (BOOT) only; 2 = primary + secondary
    bool    has_rotation;    // IMU-driven CPU rotation in the flush callback
    bool    has_battery;     // AXP2101 battery measurement is meaningful
    bool    has_imu;         // QMI8658 (or compatible) is populated

    // The display bus wants big-endian RGB565. When true, LVGL renders
    // LV_COLOR_FORMAT_RGB565_SWAPPED and the splash swaps its palette, so the
    // strips handed to display_hal_draw_bitmap() go out untouched — no
    // per-pixel swap anywhere on the hot path. Boards leave it false unless
    // their display.cpp is written for it (the C6 2.16 is).
    bool    be_pixels;

    // Radius, in px, of the glass's rounded corners — the active area is a
    // rounded rectangle on the AMOLEDs and anything drawn hugging the edge
    // (the alert glow) must follow it or it looks cut off. 0 = square panel.
    int16_t corner_radius;
};

const BoardCaps& board_caps(void);
