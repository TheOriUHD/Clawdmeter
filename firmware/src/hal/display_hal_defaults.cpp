#include "display_hal.h"

// Default for boards whose display_hal_draw_bitmap() returns only after the
// pixels are on the panel: nothing to wait for. A board with an asynchronous
// (DMA) flush defines its own, strong display_hal_wait() in its display.cpp.
__attribute__((weak)) void display_hal_wait(void) {}

__attribute__((weak)) void display_hal_set_swap_on_flush(bool on) { (void)on; }
__attribute__((weak)) bool display_hal_swap_on_flush(void) { return false; }
__attribute__((weak)) void display_hal_set_async(bool on) { (void)on; }

