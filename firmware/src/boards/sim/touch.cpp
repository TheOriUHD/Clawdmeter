#include "board.h"
#include "../../hal/touch_hal.h"
#include <SDL.h>
#include <stdlib.h>
#include <stdio.h>
#include <Arduino.h>

void touch_hal_init(void) {}

// Scripted finger for headless QA:
//   SIM_DRAG="x0,y0,x1,y1,start_ms,dur_ms[,hold_ms]"
// presses at (x0,y0) at start_ms, moves linearly to (x1,y1) over dur_ms,
// holds there for hold_ms (default 0), then releases. Combine with
// SIM_AUTOSHOT_MS inside the hold window to photograph a page mid-drag, or
// after release to check where it snapped. While the script is armed the
// mouse is ignored.
static bool  script_parsed = false, script_on = false;
static int   sx0, sy0, sx1, sy1;
static long  s_start, s_dur, s_hold;

static void parse_script(void) {
    script_parsed = true;
    const char* v = getenv("SIM_DRAG");
    if (!v) return;
    s_hold = 0;
    int n = sscanf(v, "%d,%d,%d,%d,%ld,%ld,%ld", &sx0, &sy0, &sx1, &sy1, &s_start, &s_dur, &s_hold);
    if (n < 6) { fprintf(stderr, "[sim] bad SIM_DRAG '%s'\n", v); return; }
    if (s_dur < 1) s_dur = 1;
    script_on = true;
    printf("[sim] scripted drag (%d,%d)->(%d,%d) at %ldms over %ldms, hold %ldms\n",
           sx0, sy0, sx1, sy1, s_start, s_dur, s_hold);
}

// Mouse position + left button = one finger. Events are pumped every loop
// by sim_main.cpp, so SDL_GetMouseState is always fresh here.
void touch_hal_read(uint16_t* x, uint16_t* y, bool* pressed) {
    if (!script_parsed) parse_script();
    if (script_on) {
        const long t = (long)millis();
        if (t >= s_start && t < s_start + s_dur + s_hold) {
            long f = t - s_start;
            if (f > s_dur) f = s_dur;
            *x = (uint16_t)(sx0 + (sx1 - sx0) * f / s_dur);
            *y = (uint16_t)(sy0 + (sy1 - sy0) * f / s_dur);
            *pressed = true;
        } else {
            *x = (uint16_t)sx1;
            *y = (uint16_t)sy1;
            *pressed = false;
        }
        return;
    }
    int mx, my;
    uint32_t b = SDL_GetMouseState(&mx, &my);
    if (mx < 0) mx = 0;
    if (mx >= LCD_WIDTH) mx = LCD_WIDTH - 1;
    if (my < 0) my = 0;
    if (my >= LCD_HEIGHT) my = LCD_HEIGHT - 1;
    *x = (uint16_t)mx;
    *y = (uint16_t)my;
    *pressed = (b & SDL_BUTTON(SDL_BUTTON_LEFT)) != 0;
}
