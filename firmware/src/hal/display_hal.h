#pragma once
#include <stdint.h>

// Display abstraction. The board provides the QSPI bus, panel driver, and any
// CPU-side rotation. Shared code (main.cpp, LVGL glue) never sees the GFX
// driver type. Dimensions are not declared here — query board_caps().

// Construct bus + driver objects. Safe to call before display_hal_begin().
// On boards with an IO expander gating the LCD reset, the board's
// implementation is responsible for ensuring the expander has released the
// reset before talking to the panel.
void display_hal_init(void);

// Bring the panel out of reset, clear it, and apply default brightness.
void display_hal_begin(void);

void display_hal_set_brightness(uint8_t level);  // 0..255 (driver-defined scale)
void display_hal_fill_screen(uint16_t color565);

// Write a w×h RGB565 bitmap at (x, y). Boards with software rotation
// (e.g. CO5300) transform (x, y, w, h) and the pixel buffer here before
// pushing to the panel. Shared LVGL flush_cb just calls this — no #ifdef.
void display_hal_draw_bitmap(int32_t x, int32_t y, int32_t w, int32_t h,
                             const uint16_t* pixels);

// Block until any panel transfer started by display_hal_draw_bitmap() has
// completed. Boards that push pixels synchronously leave the weak default
// (a no-op, hal/display_hal_defaults.cpp). Boards that hand the strip to DMA
// and return early implement it — LVGL calls it (flush_wait_cb) right before
// the next strip is flushed, which is what lets rendering overlap the flush.
// After it returns the caller may reuse or modify the pixel buffer.
void display_hal_wait(void);

// Per-loop housekeeping for rotation-aware boards: detects orientation
// changes from the IMU, blanks the panel, invalidates LVGL, and ramps
// brightness back up. No-op on boards without rotation.
void display_hal_tick(void);

// LVGL flush regions must be even-aligned on the CO5300; harmless on others.
void display_hal_round_area(int32_t* x1, int32_t* y1, int32_t* x2, int32_t* y2);

// ---- Debug toggles (serial "render plain|swapped", "dma on|off") ----
// The C6 2.16 renders big-endian pixels straight into the DMA stream. To tell a
// renderer artifact from a bus artifact without a reflash, the HAL can swap
// bytes itself on flush (LVGL then renders plain RGB565) and can fall back to
// the library's synchronous pixel path. Weak defaults: no-ops / false.
void display_hal_set_swap_on_flush(bool on);
bool display_hal_swap_on_flush(void);
void display_hal_set_async(bool on);

