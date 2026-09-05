#include "../../hal/sound_hal.h"
#include <stdio.h>

void sound_hal_init(void) {}
void sound_hal_tick(void) {}
void sound_hal_play_reset(void) { printf("[sim] chime! (session reset)\n"); }
void sound_hal_play_alert(int kind) { printf("[sim] alert chime %d (%s)\n", kind, kind ? "done" : "needs you"); }
void sound_hal_set_volume(int pct) { printf("[sim] volume %d%%\n", pct); }
