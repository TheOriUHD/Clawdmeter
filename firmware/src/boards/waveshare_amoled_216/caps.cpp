#include "../../hal/board_caps.h"
#include "board.h"

static const BoardCaps caps = {
    .name = BOARD_NAME,
    .width = LCD_WIDTH,
    .height = LCD_HEIGHT,
    .button_count = 2,
    .has_rotation = true,
    .has_battery = true,
    .has_imu = true,
    .corner_radius = 70,   // rounded glass: measured on the C6 2.16 with the `corners on` overlay (2026-09-05)
};

const BoardCaps& board_caps(void) { return caps; }
