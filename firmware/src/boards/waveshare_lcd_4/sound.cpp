#include "../../hal/sound_hal.h"

// LCD-4 has a passive buzzer on the wiki BOM but the pin is not wired up
// in this port. Session-reset chime is a no-op.

void sound_hal_init(void) {}
void sound_hal_tick(void) {}
void sound_hal_play_reset(void) {}
void sound_hal_play_alert(int kind) { (void)kind; }
void sound_hal_set_volume(int pct) { (void)pct; }
