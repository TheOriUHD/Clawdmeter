#include "../../hal/board_caps.h"
#include "board.h"

static const BoardCaps caps = {
    .name = BOARD_NAME,
    .width = LCD_WIDTH,
    .height = LCD_HEIGHT,
    .button_count = 1,
    .has_rotation = false,
    .has_battery = true,
    .has_imu = true,
    .corner_radius = 0,   // not measured yet: glow rings stay square-cornered
};

const BoardCaps& board_caps(void) { return caps; }
