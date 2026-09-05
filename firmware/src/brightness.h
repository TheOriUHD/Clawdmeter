#pragma once
#include <stdint.h>

// User-controlled display brightness, persisted to NVS. The middle (PWR)
// button short-press cycles through the levels via brightness_cycle(); the
// touch Settings page sets a level directly via brightness_set_index().
// idle owns the actual panel brightness, so this routes the chosen level
// through idle_set_awake_brightness().
void        brightness_init(void);              // load saved level from NVS and apply
void        brightness_cycle(void);             // advance to next level, save, apply
void        brightness_set_index(uint8_t idx);  // pick a level, save, apply
uint8_t     brightness_get_index(void);
uint8_t     brightness_level_count(void);
const char* brightness_label(uint8_t idx);      // "25%" … "100%"
uint8_t     brightness_get(void);               // current PWM level (0..255)
