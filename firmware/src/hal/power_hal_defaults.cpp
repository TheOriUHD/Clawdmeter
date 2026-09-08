#include "power_hal.h"
#include <Arduino.h>

// Boards without a PMU (or without a diagnostic worth printing) get this.
// A board with real power hardware defines a strong one in its power.cpp.
__attribute__((weak)) void power_hal_debug_dump(void) {
    Serial.println("power: no PMU on this board");
}
