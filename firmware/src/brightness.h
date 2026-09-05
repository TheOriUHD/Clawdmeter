#pragma once
#include <stdint.h>

// User-controlled display brightness, persisted to NVS. Two ways in: the
// Settings slider sets any level (previewed live while dragging, persisted on
// release), and the middle (PWR) button short-press steps through four
// presets. idle owns the actual panel brightness, so this routes the chosen
// level through idle_set_awake_brightness().
#define BRIGHTNESS_MIN_LEVEL 13      // ~5%: never let the panel go fully dark

void    brightness_init(void);                    // load saved level from NVS and apply
void    brightness_set_level(uint8_t level);      // apply + persist
void    brightness_preview_level(uint8_t level);  // apply only (slider drag)
void    brightness_cycle(void);                   // next preset (PWR button), persist
uint8_t brightness_get(void);                     // current PWM level (0..255)
int     brightness_get_pct(void);                 // 5..100
uint8_t brightness_pct_to_level(int pct);
