#include "../../hal/power_hal.h"
#include "board.h"
#include <Arduino.h>
#include <Wire.h>
#include <XPowersLib.h>

// AXP2101 PMU — identical chip and protocol to the S3 variant.

#define BATTERY_POLL_MS  2000
#define CHARGING_POLL_MS 500
#define PWR_POLL_MS      50

// The PMU instance is owned by board_init.cpp on this board — it has to
// come up before display_hal_init() to enable the LCD power rails. We
// reuse the same handle here for battery polling and PKEY IRQ wiring.
extern XPowersPMU board_pmu;
#define pmu board_pmu

static int      cached_pct        = -1;
static bool     cached_charging   = false;
static bool     cached_vbus       = false;
static bool     pwr_pressed_flag  = false;
static bool     pwr_long_flag     = false;
static bool     pwr_released_flag = false;
static uint32_t last_battery_ms   = 0;
static uint32_t last_charging_ms  = 0;
static uint32_t last_pwr_ms       = 0;

void power_hal_init(void) {
    // pmu.begin() already ran in board_init(); just configure battery +
    // IRQ wiring here.
    pmu.enableBattDetection();
    pmu.enableBattVoltageMeasure();
    // Without these two ADCs the VBUS and SYS readings are always 0 mV, which
    // hides exactly the sag that makes a weak supply reset the board.
    pmu.enableVbusVoltageMeasure();
    pmu.enableSystemVoltageMeasure();

    // This board ships without a cell, so VBUS *is* the system's only source:
    // nothing buffers the panel's inrush or a BLE transmit burst. The AXP2101
    // powers up with its input voltage limit (VINDPM) at 4.36 V and starts
    // throttling the input the moment VBUS dips there — on a thin cable or a
    // modest charger that is enough to collapse the rail, drop the board, and
    // start the boot-and-die loop. 4.04 V keeps a comfortable margin over the
    // ~3.4 V the 3.3 V ALDO rails need while tolerating a far weaker supply.
    // A good supply never reaches either threshold, so this costs nothing.
    pmu.setVbusVoltageLimit(XPOWERS_AXP2101_VBUS_VOL_LIM_4V04);
    pmu.setVbusCurrentLimit(XPOWERS_AXP2101_VBUS_CUR_LIM_1500MA);   // was already the default; pin it

    // Mirror the Waveshare XiaoZhi BSP charging config so the on-chip
    // fuel gauge has the right reference numbers. Without these,
    // getBatteryPercent() returns -1 / shows "---" on the UI.

    pmu.disableIRQ(XPOWERS_AXP2101_ALL_IRQ);
    pmu.clearIrqStatus();
    pmu.enableIRQ(XPOWERS_AXP2101_PKEY_SHORT_IRQ
                | XPOWERS_AXP2101_PKEY_LONG_IRQ
                | XPOWERS_AXP2101_PKEY_POSITIVE_IRQ);

    // Default press-off is 6s, only 2s after the pair gesture arms at ~3s and
    // disarms at ~6s. Bump to 8s so a slightly-too-long hold doesn't shut the
    // device down mid-gesture.
    pmu.setPowerKeyPressOffTime(XPOWERS_POWEROFF_8S);

    cached_charging = pmu.isCharging();
    cached_vbus     = pmu.isVbusIn();
    cached_pct = pmu.getBatteryPercent();
}

void power_hal_tick(void) {
    uint32_t now = millis(); 

    if (now - last_charging_ms >= CHARGING_POLL_MS) {
        last_charging_ms = now;
        cached_charging = pmu.isCharging();
        cached_vbus     = pmu.isVbusIn();
    }
    if (now - last_battery_ms >= BATTERY_POLL_MS) {
        last_battery_ms = now;
        cached_pct = pmu.getBatteryPercent();
    }
    if (now - last_pwr_ms >= PWR_POLL_MS) {
        last_pwr_ms = now;
        pmu.getIrqStatus();
        if (pmu.isPekeyShortPressIrq())    pwr_pressed_flag  = true;
        if (pmu.isPekeyLongPressIrq())     pwr_long_flag     = true;
        if (pmu.isPekeyPositiveIrq())      pwr_released_flag = true;
        pmu.clearIrqStatus();
    }
}

int  power_hal_battery_pct(void) { return cached_pct; }
bool power_hal_is_charging(void) { return cached_charging; }
bool power_hal_is_vbus_in(void)  { return cached_vbus; }

bool power_hal_pwr_pressed(void) {
    if (pwr_pressed_flag) { pwr_pressed_flag = false; return true; }
    return false;
}

bool power_hal_pwr_long_pressed(void) {
    if (pwr_long_flag) { pwr_long_flag = false; return true; }
    return false;
}

bool power_hal_pwr_released(void) {
    if (pwr_released_flag) { pwr_released_flag = false; return true; }
    return false;
}

// Serial `power`: everything the AXP2101 knows about where the board's
// energy comes from. Used to diagnose the "boots, then dies on a plain
// charger" symptom — the usual cause is a VBUS input current limit lower
// than the panel's inrush.
void power_hal_debug_dump(void) {
    static const uint16_t VBUS_LIM_MA[] = {100, 500, 900, 1000, 1500, 2000};
    const uint8_t lim = pmu.getVbusCurrentLimit();
    const uint8_t vlim = pmu.getVbusVoltageLimit();      // VINDPM: input collapses below this
    Serial.printf("power: vbus=%s %umV, ilim=%umA (opt %u), vlim=%umV (opt %u), sys=%umV\n",
                  pmu.isVbusIn() ? "in" : "absent", pmu.getVbusVoltage(),
                  lim < (sizeof(VBUS_LIM_MA) / sizeof(VBUS_LIM_MA[0])) ? VBUS_LIM_MA[lim] : 0,
                  lim, 3880 + (unsigned)vlim * 80, vlim, pmu.getSystemVoltage());
    Serial.printf("power: battery=%s %umV %d%%, charging=%d, chg_curr_opt=%u, sys_off=%umV\n",
                  pmu.isBatteryConnect() ? "present" : "none", pmu.getBattVoltage(),
                  pmu.getBatteryPercent(), (int)pmu.isCharging(),
                  pmu.getChargerConstantCurr(), pmu.getSysPowerDownVoltage());
}
