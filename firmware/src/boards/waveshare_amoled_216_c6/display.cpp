#include "../../hal/display_hal.h"
#include "board.h"
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <driver/spi_master.h>

// C6 AMOLED-2.16 uses a CO5300 AMOLED panel (per the Waveshare
// ESP32-C6-Touch-AMOLED-2.16 spec) — the same controller as the S3
// AMOLED-2.16 sibling, so we drive it with Arduino_CO5300 and reuse that
// class's vendor-correct init rather than the SH8601 class + a hand-patched
// sequence. LCD reset is not wired to any MCU GPIO; the panel boots from its
// internal power-on reset (rst = GFX_NOT_DEFINED). Rotation is disabled (no
// PSRAM headroom for a rotation strip).
//
// Pixel path. The QSPI flush is the transition bottleneck on this single-core,
// PSRAM-less board (~17 ms of a frame at 80 MHz), so pixels don't go through
// Arduino_GFX's blocking writePixels() (which also byte-swaps every pixel into
// a bounce buffer): LVGL already renders in the panel's byte order
// (BoardCaps.be_pixels), the bus is opened as a *shared* interface and a
// second SPI device on the same host takes the strips as queued DMA
// transactions. display_hal_draw_bitmap() sets the address window
// (Arduino_GFX, polling), queues the strip and returns; LVGL renders the next
// strip meanwhile and calls display_hal_wait() right before it needs the
// buffer again. Command framing mirrors the library's (0x32 / 0x003C00 memory
// write continue, CS held low across the burst).

static Arduino_DataBus* bus = nullptr;
static Arduino_CO5300*  gfx = nullptr;

// ---- Async pixel device ----
static spi_device_handle_t   pix_dev = nullptr;
static spi_transaction_ext_t pix_tx[4];        // ≤ 4 chunks in flight (queue_size)
static uint8_t               pix_pending = 0;  // queued, not yet collected
static bool                  pix_cs_low = false;

#define PIX_QUEUE     4
// The C6's SPI DMA takes at most 32 KB per transaction (an 18-bit bit-length
// field); the bus max_transfer_sz (platformio.ini) is sized to match.
#define PIX_CHUNK_MAX 32768
static bool swap_on_flush = true;    // LVGL renders plain RGB565; we swap before the bus. LVGL 9.5's
                                     // RGB565_SWAPPED render target blits RGB565 canvases without
                                     // swapping (purple creatures), so the swap lives here.
static bool async_enabled = true;    // false: the library's synchronous pixel path (debug A/B)

static inline void pix_cs(bool low) {
    digitalWrite(LCD_CS, low ? LOW : HIGH);
    pix_cs_low = low;
}

static void pix_dev_init(void) {
    spi_device_interface_config_t devcfg = {};
    devcfg.command_bits   = 8;
    devcfg.address_bits   = 24;
    devcfg.dummy_bits     = 0;
    devcfg.mode           = 0;
    devcfg.clock_source   = SPI_CLK_SRC_DEFAULT;
    devcfg.clock_speed_hz = ESP32QSPI_FREQUENCY;
    devcfg.spics_io_num   = -1;                 // CS is the same GPIO the library drives
    devcfg.flags          = SPI_DEVICE_HALFDUPLEX;
    devcfg.queue_size     = PIX_QUEUE;
    esp_err_t err = spi_bus_add_device(ESP32QSPI_SPI_HOST, &devcfg, &pix_dev);
    if (err != ESP_OK) {
        Serial.printf("display: async pixel device failed (%d) — falling back to blocking writes\n", (int)err);
        pix_dev = nullptr;
    }
}

// Collect every queued transaction, then release CS.
void display_hal_wait(void) {
    while (pix_pending) {
        spi_transaction_t* done = nullptr;
        spi_device_get_trans_result(pix_dev, &done, portMAX_DELAY);
        pix_pending--;
    }
    if (pix_cs_low) pix_cs(false);
}

void display_hal_init(void) {
    // Shared interface: the library acquires the bus only around its own
    // (command) transactions, leaving room for the async pixel device.
    bus = new Arduino_ESP32QSPI(
        LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3, true /* shared */);
    // CO5300 constructor: (bus, rst, rotation, w, h, col_off1..2, row_off1..2).
    // No reset GPIO on this board; the 480-wide panel is full-width so all
    // offsets are 0 — matches the S3 AMOLED-2.16 instantiation.
    gfx = new Arduino_CO5300(
        bus, GFX_NOT_DEFINED, 0 /* rotation disabled */,
        LCD_WIDTH, LCD_HEIGHT, 0, 0, 0, 0);
}

// Arduino_CO5300::begin() already issues SLPOUT, SPI-mode control, pixel
// format, brightness-control, DISPON and a default MADCTL. The ONLY thing it
// does not set is this panel's manufacturer page-0x20 driving-voltage
// registers (0x19/0x1C) — without them the panel stays black even with the
// rails up. Set just those; everything else the SH8601-era hack also wrote
// (0xC4/0x53/0x51/0x63/0x29) is now covered by the class init, and we override
// MADCTL below to fix orientation.
// The CO5300 class default (rotation-0, MADCTL 0x00) leaves the panel
// sideways on this board — confirmed on hardware. Restore the MV+ML
// transpose (MADCTL 0x30) that the pre-CO5300-rebase SH8601-hack version
// used to write; touch mapping in touch.cpp is calibrated to match.
static void send_panel_driving_init(Arduino_DataBus* b) {
    b->beginWrite();
    b->writeC8D8(0xFE, 0x20);    // enter manufacturer command page 0x20
    b->writeC8D8(0x19, 0x10);    // panel driving voltage
    b->writeC8D8(0x1C, 0xA0);    // panel driving voltage
    b->writeC8D8(0xFE, 0x00);    // back to user command page
    b->writeC8D8(0x36, 0x30);    // MADCTL: MV transpose + ML (orientation fix)
    b->endWrite();
    delay(20);
}

void display_hal_begin(void) {
    gfx->begin();
    send_panel_driving_init(bus);   // panel-specific regs the class init omits
    gfx->fillScreen(0x0000);
    gfx->setBrightness(200);
    pix_dev_init();
}

void display_hal_set_brightness(uint8_t level) {
    display_hal_wait();
    if (gfx) gfx->setBrightness(level);
}

void display_hal_fill_screen(uint16_t color) {
    display_hal_wait();
    if (gfx) gfx->fillScreen(color);
}

// Strips arrive already in the panel's byte order (BoardCaps.be_pixels: LVGL
// renders RGB565_SWAPPED, the splash swaps its palette), so nothing is touched
// on the way out: set the window, queue the DMA, return.
void display_hal_draw_bitmap(int32_t x, int32_t y, int32_t w, int32_t h,
                             const uint16_t* pixels) {
    if (!gfx) return;
    display_hal_wait();                          // previous strip must be done first

    uint16_t* buf = const_cast<uint16_t*>(pixels);
    const size_t n = (size_t)w * (size_t)h;

    if (swap_on_flush) {                         // LVGL rendered little-endian: make it bus order here
        uint32_t* p32 = (uint32_t*)buf;          // two pixels per step; strips are always even-sized
        const size_t n32 = n / 2;
        for (size_t i = 0; i < n32; i++) {
            const uint32_t v = p32[i];
            p32[i] = ((v & 0xFF00FF00u) >> 8) | ((v & 0x00FF00FFu) << 8);
        }
        if (n & 1) buf[n - 1] = (uint16_t)((buf[n - 1] << 8) | (buf[n - 1] >> 8));
    }
    if (!pix_dev || !async_enabled) {            // no async device: library path (data already in bus order)
        gfx->draw16bitBeRGBBitmap(x, y, buf, w, h);
        return;
    }

    gfx->startWrite();
    gfx->writeAddrWindow(x, y, w, h);            // polling, releases the bus after
    gfx->endWrite();

    pix_cs(true);
    uint8_t* data = (uint8_t*)buf;
    uint32_t len  = (uint32_t)n * 2;
    bool first = true;
    while (len) {
        const uint32_t l = len > PIX_CHUNK_MAX ? PIX_CHUNK_MAX : len;
        if (pix_pending >= PIX_QUEUE) {          // keep ≤ queue_size in flight
            spi_transaction_t* done = nullptr;
            spi_device_get_trans_result(pix_dev, &done, portMAX_DELAY);
            pix_pending--;
        }
        spi_transaction_ext_t* t = &pix_tx[pix_pending];
        memset(t, 0, sizeof(*t));
        if (first) {
            t->base.flags = SPI_TRANS_MODE_QIO;
            t->base.cmd   = 0x32;                // QSPI "memory write continue"
            t->base.addr  = 0x003C00;
            first = false;
        } else {
            t->base.flags = SPI_TRANS_MODE_QIO | SPI_TRANS_VARIABLE_CMD |
                            SPI_TRANS_VARIABLE_ADDR | SPI_TRANS_VARIABLE_DUMMY;
            t->command_bits = 0;
            t->address_bits = 0;
            t->dummy_bits   = 0;
        }
        t->base.tx_buffer = data;
        t->base.length    = l * 8;
        const esp_err_t err = spi_device_queue_trans(pix_dev, (spi_transaction_t*)t, portMAX_DELAY);
        if (err != ESP_OK) {
            // Never count a transaction that was not queued (a wait would hang
            // forever). Finish this strip the slow way and disable the fast path.
            Serial.printf("display: queue_trans failed (%d) — async path disabled\n", (int)err);
            display_hal_wait();
            pix_dev = nullptr;
            gfx->draw16bitBeRGBBitmap(x + (int32_t)(((data - (uint8_t*)buf) / 2) % w),
                                      y + (int32_t)(((data - (uint8_t*)buf) / 2) / w),
                                      (uint16_t*)data, w, (int32_t)(len / 2 / w));
            return;
        }
        pix_pending++;
        data += l;
        len  -= l;
    }
    // Return with the burst in flight; display_hal_wait() collects it.
}

void display_hal_tick(void) {
    // No rotation cycle on this board.
}

// CO5300 requires even-aligned flush regions.
void display_hal_round_area(int32_t* x1, int32_t* y1, int32_t* x2, int32_t* y2) {
    *x1 = *x1 & ~1;
    *y1 = *y1 & ~1;
    *x2 = *x2 | 1;
    *y2 = *y2 | 1;
}

void display_hal_set_swap_on_flush(bool on) { swap_on_flush = on; }
bool display_hal_swap_on_flush(void)        { return swap_on_flush; }
void display_hal_set_async(bool on)         { display_hal_wait(); async_enabled = on; }

