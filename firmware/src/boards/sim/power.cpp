#include "../../hal/power_hal.h"
#include "sim_platform.h"
#include <stdlib.h>

void power_hal_init(void) {}
void power_hal_tick(void) {}

// SIM_NO_BATTERY=1 mimics a board without a cell (PMU reports -1), so the
// header falls back to the host battery from the payload.
int  power_hal_battery_pct(void) {
    static int no_cell = -1;
    if (no_cell < 0) no_cell = getenv("SIM_NO_BATTERY") ? 1 : 0;
    return no_cell ? -1 : sim_battery_pct();
}
bool power_hal_is_charging(void) { return sim_charging(); }
bool power_hal_is_vbus_in(void)  { return sim_charging(); }

bool power_hal_pwr_pressed(void)      { return sim_take_pwr_pressed(); }
bool power_hal_pwr_long_pressed(void) { return sim_take_pwr_long(); }
bool power_hal_pwr_released(void)     { return sim_take_pwr_released(); }
