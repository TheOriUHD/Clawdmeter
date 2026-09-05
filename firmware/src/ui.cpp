#include "ui.h"
#include "splash.h"
#include "settings.h"
#include "brightness.h"
#include "version.h"
#include <lvgl.h>
#include <time.h>
#include <stdint.h>
#include <string.h>
#include <esp_heap_caps.h>
#include "logo.h"
#include "clawd_still.h"
#include "icons.h"
#include "hal/board_caps.h"

// Custom fonts (scaled for 314 PPI, ~1.9x from original 165 PPI)
LV_FONT_DECLARE(font_tiempos_56);
LV_FONT_DECLARE(font_tiempos_34);
LV_FONT_DECLARE(font_styrene_48);
LV_FONT_DECLARE(font_styrene_28);
LV_FONT_DECLARE(font_styrene_24);
LV_FONT_DECLARE(font_styrene_20);
LV_FONT_DECLARE(font_styrene_16);
LV_FONT_DECLARE(font_styrene_14);
LV_FONT_DECLARE(font_styrene_12);
LV_FONT_DECLARE(font_mono_32);
LV_FONT_DECLARE(font_mono_18);

// Layout values computed from the active board's geometry. Populated once
// in ui_init() and treated as const for the rest of the program. Adding a
// new display size means extending compute_layout() with another
// breakpoint — never editing the screen-builder functions below.
struct Layout {
    int16_t scr_w, scr_h;
    int16_t margin;
    int16_t title_y;
    int16_t content_y;
    int16_t content_w;

    // Usage screen
    int16_t usage_panel_h;
    int16_t usage_panel_gap;
    int16_t usage_bar_y;
    int16_t usage_reset_y;
    int16_t bar_h;
    int16_t panel_pad_x, panel_pad_y;
    int16_t pill_pad_x, pill_pad_y;
    const lv_font_t* title_font;     // screen title / clock
    const lv_font_t* pct_font;       // big percentage number
    const lv_font_t* ent_pct_font;   // enterprise spending number
    const lv_font_t* pill_font;      // "Current" / "Weekly" pill
    const lv_font_t* reset_font;     // "Resets in ..." line
    const lv_font_t* pace_font;      // enterprise "Under/On/Over pace" line
    const lv_font_t* anim_font;      // animated status line
    int16_t anim_y;                  // status line offset from bottom
    bool    small_icons;             // 40px logo + 24px battery (vs 80/48) on small screens
    int16_t title_nudge;             // title x-shift balancing the corner logo
    int16_t logo_y;                  // logo top edge
    int16_t batt_y;                  // battery icon top edge
    int16_t batt_w;                  // battery icon width, for position math

    // Weekly card faces: indicator dots on the reset row (right-aligned)
    int16_t face_dot_y;
    int16_t dot_size, dot_gap;

    // Settings: 2×2 tile pages. Tiles are the buttons — sized for a fingertip
    // on a 39 mm panel (≥ 13 mm tall), label on top, value large below.
    int16_t tiles_bottom;            // bottom edge of the tile grid
    int16_t tile_gap, tile_radius;
    int16_t tile_pad_x, tile_pad_y;
    const lv_font_t* tile_label_font;
    const lv_font_t* tile_value_font;
    int16_t dots_y;                  // page indicator dots (top edge)

    // About page (key/value rows + footer)
    int16_t about_row_h;
    const lv_font_t* about_key_font;
    const lv_font_t* about_val_font;
    const lv_font_t* about_hint_font;
    int16_t about_hint_y;            // footer offset from bottom

    // Pairing hint / idle screen
    int16_t pair_y1, pair_y2, pair_y3;
    int16_t idle_px;                 // sleeping-creature size on the idle screen

    // Bluetooth screen
    int16_t bt_info_panel_h;
    int16_t bt_reset_zone_h;
    const lv_font_t* bt_title_font;
    const lv_font_t* bt_status_font;
    const lv_font_t* bt_device_font;
    const lv_font_t* bt_credit_1_font;
    const lv_font_t* bt_credit_2_font;
};
static Layout L = {};

// Pick layout values from the active board's pixel dimensions. The two
// existing boards happen to land on the two breakpoints below; new ports
// inherit the closer one — visually OK, may need a polish pass for
// pixel-perfect alignment but never blocks the port from booting.
static void compute_layout(const BoardCaps& c) {
    L.scr_w = c.width;
    L.scr_h = c.height;
    L.margin = 20;
    L.title_y = 30;

    // Values shared by the two original breakpoints; the small branch below
    // overrides them wholesale.
    L.bar_h = 24;
    L.panel_pad_x = 16;
    L.panel_pad_y = 12;
    L.pill_pad_x = 18;
    L.pill_pad_y = 6;
    L.title_font   = &font_tiempos_56;
    L.pct_font     = &font_styrene_48;
    L.ent_pct_font = &font_tiempos_56;
    L.pill_font    = &font_styrene_28;
    L.reset_font   = &font_styrene_28;
    L.pace_font    = &font_styrene_16;
    L.anim_font    = &font_mono_32;
    L.anim_y = -15;
    L.small_icons = false;
    L.title_nudge = 16;
    L.logo_y = L.title_y - 10;
    L.batt_y = L.title_y;
    L.batt_w = ICON_BATTERY_W;
    L.pair_y1 = 40;
    L.pair_y2 = 120;
    L.pair_y3 = 160;
    L.idle_px = 160;

    if (c.height >= 460) {
        // Large layout — tuned for 480x480 (AMOLED-2.16).
        L.content_y = 100;
        L.usage_panel_h = 150;
        L.usage_panel_gap = 16;
        L.usage_bar_y = 56;
        L.usage_reset_y = 94;
        L.face_dot_y = L.usage_reset_y + 13;   // centred on the reset line
        L.dot_size = 8;
        L.dot_gap = 10;
        L.tiles_bottom = 432;                  // 2 rows of 160 px tiles from y=100
        L.tile_gap = 12;
        L.tile_radius = 10;
        L.tile_pad_x = 16;
        L.tile_pad_y = 14;
        L.tile_label_font = &font_styrene_20;
        L.tile_value_font = &font_styrene_28;
        L.dots_y = 446;
        L.about_row_h = 38;
        L.about_key_font  = &font_styrene_20;
        L.about_val_font  = &font_styrene_20;
        L.about_hint_font = &font_styrene_16;
        L.about_hint_y = -22;
        L.bt_info_panel_h = 160;
        L.bt_reset_zone_h = 110;
        L.bt_title_font    = &font_tiempos_56;
        L.bt_status_font   = &font_styrene_48;
        L.bt_device_font   = &font_styrene_28;
        L.bt_credit_1_font = &font_styrene_24;
        L.bt_credit_2_font = &font_styrene_20;
    } else if (c.height >= 300) {
        // Compact layout — tuned for 368x448 (AMOLED-1.8).
        L.content_y = 85;
        L.usage_panel_h = 130;
        L.usage_panel_gap = 12;
        L.usage_bar_y = 48;
        L.usage_reset_y = 78;
        L.face_dot_y = L.usage_reset_y + 13;
        L.dot_size = 7;
        L.dot_gap = 8;
        L.tiles_bottom = 414;
        L.tile_gap = 10;
        L.tile_radius = 10;
        L.tile_pad_x = 14;
        L.tile_pad_y = 12;
        L.tile_label_font = &font_styrene_16;
        L.tile_value_font = &font_styrene_24;
        L.dots_y = 428;
        L.about_row_h = 32;
        L.about_key_font  = &font_styrene_16;
        L.about_val_font  = &font_styrene_16;
        L.about_hint_font = &font_styrene_14;
        L.about_hint_y = -18;
        L.bt_info_panel_h = 140;
        L.bt_reset_zone_h = 90;
        L.bt_title_font    = &font_tiempos_34;
        L.bt_status_font   = &font_styrene_28;
        L.bt_device_font   = &font_styrene_20;
        L.bt_credit_1_font = &font_styrene_16;
        L.bt_credit_2_font = &font_styrene_14;
    } else {
        // Small layout — tuned for 240x240 (LCD-1.54 and similar square TFTs).
        // Everything shrinks: fonts two steps down, panels ~half height, and
        // the corner logo/battery switch to the 40px/24px small assets.
        L.margin = 8;
        L.title_y = 4;
        L.content_y = 44;
        L.usage_panel_h = 74;
        L.usage_panel_gap = 6;
        L.usage_bar_y = 30;
        L.usage_reset_y = 46;
        L.bar_h = 12;
        L.panel_pad_x = 10;
        L.panel_pad_y = 6;
        L.pill_pad_x = 8;
        L.pill_pad_y = 2;
        L.title_font   = &font_tiempos_34;
        L.pct_font     = &font_styrene_24;
        L.ent_pct_font = &font_tiempos_34;
        L.pill_font    = &font_styrene_14;
        L.reset_font   = &font_styrene_14;
        L.pace_font    = &font_styrene_12;
        L.anim_font    = &font_mono_18;
        // Center the status line in the strip below the weekly panel; flush
        // against the bottom edge it reads as unevenly spaced.
        L.anim_y = -10;
        L.small_icons = true;
        L.title_nudge = 8;
        L.logo_y = 2;
        L.batt_y = 10;
        L.batt_w = ICON_BATTERY_SMALL_W;
        L.face_dot_y = L.usage_reset_y + 6;
        L.dot_size = 5;
        L.dot_gap = 6;
        L.tiles_bottom = 222;
        L.tile_gap = 6;
        L.tile_radius = 6;
        L.tile_pad_x = 8;
        L.tile_pad_y = 6;
        L.tile_label_font = &font_styrene_12;
        L.tile_value_font = &font_styrene_16;
        L.dots_y = 229;
        L.about_row_h = 20;
        L.about_key_font  = &font_styrene_12;
        L.about_val_font  = &font_styrene_12;
        L.about_hint_font = &font_styrene_12;
        L.about_hint_y = -6;
        L.pair_y1 = 12;
        L.pair_y2 = 56;
        L.pair_y3 = 80;
        L.idle_px = 96;
        L.bt_info_panel_h = 90;
        L.bt_reset_zone_h = 60;
        L.bt_title_font    = &font_tiempos_34;
        L.bt_status_font   = &font_styrene_20;
        L.bt_device_font   = &font_styrene_14;
        L.bt_credit_1_font = &font_styrene_12;
        L.bt_credit_2_font = &font_styrene_12;
    }

    L.content_w = L.scr_w - 2 * L.margin;
}

// Anthropic brand palette — design tokens live in theme.h
#include "theme.h"
#define COL_BG        THEME_BG
#define COL_PANEL     THEME_PANEL
#define COL_TEXT      THEME_TEXT
#define COL_DIM       THEME_DIM
#define COL_ACCENT    THEME_ACCENT
#define COL_GREEN     THEME_GREEN
#define COL_AMBER     THEME_AMBER
#define COL_RED       THEME_RED
#define COL_BAR_BG    THEME_BAR_BG
#define COL_PRESSED   THEME_PANEL_PRESSED
#define COL_DOT_OFF   THEME_DOT_OFF

// ---- Usage screen widgets ----
static lv_obj_t* usage_container;
static lv_obj_t* lbl_title;
// Clock fed by the daemon: base epoch (local wall-clock seconds) + the lv_tick at
// which it landed, so the title ticks forward locally between 60s payloads.
// Whether it is SHOWN is the device's own Clock setting (settings.h).
static long     clock_base_epoch = 0;
static uint32_t clock_base_ms = 0;
static int      clock_fmt = 24;   // 12 or 24, the host's format from the daemon payload
static int      clock_last_min = -1;   // last rendered minute; avoids redrawing the title every tick
static bool     title_is_clock = false;
static lv_obj_t* usage_group;   // the two usage panels — shown when connected
static lv_obj_t* pair_group;    // pairing hint — shown when disconnected
static lv_obj_t* bar_session;
static lv_obj_t* lbl_session_pct;
static lv_obj_t* lbl_session_label;
static lv_obj_t* lbl_session_reset;
static lv_obj_t* bar_weekly;
static lv_obj_t* lbl_weekly_pct;
static lv_obj_t* lbl_weekly_label;
static lv_obj_t* lbl_weekly_reset;
static lv_obj_t* panel_session = nullptr;
static lv_obj_t* panel_weekly = nullptr;
// Enterprise-only widgets inside panel_session
static lv_obj_t* lbl_session_pct_sym = nullptr;  // "%" in smaller font
static lv_obj_t* lbl_spending_desc = nullptr;     // "of your monthly budget"
static lv_obj_t* lbl_spending_status = nullptr;   // "Under pace" / "On pace" / "Over pace"
static lv_obj_t* lbl_anim;      // status line: connection state + whimsical idle

// ---- Weekly card faces ----
// Some plans meter one model separately inside the weekly window (Fable on
// Max plans — the API reports it as a weekly_scoped limit). A third full-size
// card can't fit next to the two originals on a 480 px panel, so the Weekly
// card gets FACES instead: face 0 is the classic all-models card, face i the
// identical card for scoped model i — same number, pill, bar and reset line,
// so the extra limit looks exactly like the rest of the screen. Small dots on
// the reset row show how many faces there are; the card auto-advances every
// FACE_AUTO_MS and a tap on the card flips it at once (then holds a while).
// Plans without a scoped limit have one face, no dots, and the card is
// pixel-identical to the original.
static ScopedWeekly cached_scoped[MAX_SCOPED_WEEKLY];
static int       cached_scoped_count = 0;
static float     cached_weekly_pct = 0;
static int       cached_weekly_reset = -1;
static int       weekly_face = 0;           // 0 = all models, i = cached_scoped[i-1]
static uint32_t  face_next_auto_ms = 0;     // lv_tick of the next automatic flip
static lv_obj_t* face_dots[MAX_SCOPED_WEEKLY + 1];
#define FACE_AUTO_MS 7000
#define FACE_HOLD_MS 20000                  // after a tap, keep the chosen face this long

// ---- Settings: tile pages ----
// Two 2×2 pages of tiles; the whole tile is the button and tapping cycles the
// value. Page 1: Clock, Battery icon, Mascot, Status line. Page 2: Brightness,
// Sleep after, Pairing (two taps forget the bonded host), About (opens the
// About screen). Page dots at the bottom; swipe left/right pages.
static lv_obj_t* settings_container = nullptr;
enum SettingId {
    SET_CLOCK, SET_BATTERY, SET_MASCOT, SET_STATUS,
    SET_BRIGHTNESS, SET_SLEEP, SET_PAIRING, SET_ABOUT,
    SET_COUNT,
};
static const char* const SET_LABELS[SET_COUNT] = {
    "Clock", "Battery icon", "Mascot", "Status line",
    "Brightness", "Sleep after", "Pairing", "About",
};
#define SET_TILES_PER_PAGE 4
#define SET_PAGES ((SET_COUNT + SET_TILES_PER_PAGE - 1) / SET_TILES_PER_PAGE)
static lv_obj_t* settings_pages[SET_PAGES];
static lv_obj_t* tile_value[SET_COUNT];
static lv_obj_t* page_dots[SET_PAGES];
static int       settings_page = 0;
static uint32_t  pairing_confirm_ms = 0;   // >0 while "Confirm?" is armed
static uint32_t  pairing_cleared_ms = 0;   // >0 while "Cleared" is shown
#define PAIRING_CONFIRM_MS 4000
#define PAIRING_CLEARED_MS 3000

// ---- About page ----
static lv_obj_t* about_container = nullptr;
enum AboutRow {
    ABOUT_BOARD, ABOUT_FW, ABOUT_BLE, ABOUT_ADDR, ABOUT_BATTERY, ABOUT_RAM, ABOUT_UPTIME,
    ABOUT_COUNT,
};
static const char* const ABOUT_KEYS[ABOUT_COUNT] = {
    "Board", "Firmware", "Bluetooth", "Address", "Battery", "Free RAM", "Uptime",
};
static lv_obj_t* about_value[ABOUT_COUNT];
static uint32_t  about_last_refresh_ms = 0;
static int       batt_pct_cached = -1;
static bool      batt_charging_cached = false;

// ---- Gestures ----
// Swipe left/right pages Usage → Settings → About and back; a tap still
// toggles the splash. LVGL reports the gesture mid-press and then ALSO sends
// CLICKED on release, so clicks are ignored for a moment after any gesture.
static uint32_t  last_gesture_ms = 0;
#define GESTURE_CLICK_GUARD_MS 600

// ---- Battery indicator (shared, on top) ----
static lv_obj_t* battery_img;
static lv_obj_t* logo_img;
static lv_image_dsc_t battery_dscs[5];  // empty, low, medium, full, charging

// ---- Live-data freshness → which usage sub-view to show ----
// usage panels when data is flowing, an idle "Zzz" screen when the host is
// connected but no usage update landed within DATA_FRESH_MS, the pairing hint
// when BLE is down. Re-evaluated every loop in ui_tick_anim().
static lv_obj_t* idle_group;            // the "Zzz" idle screen
static uint32_t  last_data_ms = 0;      // lv_tick when the last valid usage update landed
static bool      data_received = false; // any valid update since boot
static bool      data_ok = true;        // last payload's ok flag; a {"ok":false} beat = "no fresh data"
static int       view_state = -1;       // -1 unknown / 0 pair / 1 idle / 2 usage
static const uint32_t DATA_FRESH_MS = 90000;  // usage counts as "live" within this window (daemon sends ~60s)

// ---- Shared ----
static lv_image_dsc_t logo_dsc;
static screen_t current_screen = SCREEN_USAGE;
static bool     s_ble_connected = false;   // cached BLE connection state
static uint32_t connected_at_ms = 0;       // when we last entered CONNECTED ("Connected" dwell)

// Animation state
static uint32_t anim_last_ms = 0;
static uint8_t anim_spinner_idx = 0;
static uint8_t anim_phase = 0;
static uint8_t anim_msg_idx = 0;
static uint32_t anim_msg_start = 0;
#define ANIM_MSG_MS     4000

static const char* const spinner_frames[] = {
    "\xC2\xB7", "\xE2\x9C\xBB", "\xE2\x9C\xBD",
    "\xE2\x9C\xB6", "\xE2\x9C\xB3", "\xE2\x9C\xA2",
};
#define SPINNER_COUNT 6
#define SPINNER_PHASES (2 * (SPINNER_COUNT - 1))  // 10: ping-pong 0..5..0

static const uint16_t spinner_ms[SPINNER_COUNT] = {
    260, 130, 130, 130, 130, 260,
};

static const char* const anim_messages[] = {
    "Accomplishing", "Elucidating", "Perusing",
    "Actioning", "Enchanting", "Philosophising",
    "Actualizing", "Envisioning", "Pondering",
    "Baking", "Finagling", "Pontificating",
    "Booping", "Flibbertigibbeting", "Processing",
    "Brewing", "Forging", "Puttering",
    "Calculating", "Forming", "Puzzling",
    "Cerebrating", "Frolicking", "Reticulating",
    "Channelling", "Generating", "Ruminating",
    "Churning", "Germinating", "Scheming",
    "Clauding", "Hatching", "Schlepping",
    "Coalescing", "Herding", "Shimmying",
    "Cogitating", "Honking", "Shucking",
    "Combobulating", "Hustling", "Simmering",
    "Computing", "Ideating", "Smooshing",
    "Concocting", "Imagining", "Spelunking",
    "Conjuring", "Incubating", "Spinning",
    "Considering", "Inferring", "Stewing",
    "Contemplating", "Jiving", "Sussing",
    "Cooking", "Manifesting", "Synthesizing",
    "Crafting", "Marinating", "Thinking",
    "Creating", "Meandering", "Tinkering",
    "Crunching", "Moseying", "Transmuting",
    "Deciphering", "Mulling", "Unfurling",
    "Deliberating", "Mustering", "Unravelling",
    "Determining", "Musing", "Vibing",
    "Discombobulating", "Noodling", "Wandering",
    "Divining", "Percolating", "Whirring",
    "Doing", "Wibbling",
    "Effecting", "Wizarding",
    "Working", "Wrangling",
};
#define ANIM_MSG_COUNT (sizeof(anim_messages) / sizeof(anim_messages[0]))

static lv_color_t pct_color(float pct) {
    if (pct >= 80.0f) return COL_RED;
    if (pct >= 50.0f) return COL_AMBER;
    return COL_GREEN;
}

static void format_reset_time(int mins, char* buf, size_t len) {
    if (mins < 0) {
        snprintf(buf, len, "---");
    } else if (mins < 60) {
        snprintf(buf, len, "Resets in %dm", mins);
    } else if (mins < 1440) {
        snprintf(buf, len, "Resets in %dh %dm", mins / 60, mins % 60);
    } else {
        snprintf(buf, len, "Resets in %dd %dh", mins / 1440, (mins % 1440) / 60);
    }
}

// Forward decls — callbacks defined near ui_show_screen below
static void global_click_cb(lv_event_t* e);
static void gesture_cb(lv_event_t* e);
static void settings_tile_cb(lv_event_t* e);
static void weekly_click_cb(lv_event_t* e);
static void refresh_settings_tiles(void);
static void show_settings_page(int page);
static void refresh_about(void);
static void apply_header_visibility(void);

static bool click_guarded(void) {
    return (lv_tick_get() - last_gesture_ms) < GESTURE_CLICK_GUARD_MS;
}

static lv_obj_t* make_panel(lv_obj_t* parent, int x, int y, int w, int h) {
    lv_obj_t* panel = lv_obj_create(parent);
    lv_obj_set_pos(panel, x, y);
    lv_obj_set_size(panel, w, h);
    lv_obj_set_style_bg_color(panel, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(panel, 8, 0);
    lv_obj_set_style_border_width(panel, 0, 0);
    lv_obj_set_style_pad_left(panel, L.panel_pad_x, 0);
    lv_obj_set_style_pad_right(panel, L.panel_pad_x, 0);
    lv_obj_set_style_pad_top(panel, L.panel_pad_y, 0);
    lv_obj_set_style_pad_bottom(panel, L.panel_pad_y, 0);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_EVENT_BUBBLE);
    return panel;
}

static lv_obj_t* make_bar(lv_obj_t* parent, int x, int y, int w, int h) {
    lv_obj_t* bar = lv_bar_create(parent);
    lv_obj_set_pos(bar, x, y);
    lv_obj_set_size(bar, w, h);
    lv_bar_set_range(bar, 0, 100);
    lv_bar_set_value(bar, 0, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(bar, COL_BAR_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(bar, 6, LV_PART_MAIN);
    lv_obj_set_style_bg_color(bar, COL_GREEN, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(bar, 6, LV_PART_INDICATOR);
    return bar;
}

static void init_icon_dsc_rgb565a8(lv_image_dsc_t* dsc, int w, int h, const uint8_t* data) {
    dsc->header.w = w;
    dsc->header.h = h;
    dsc->header.cf = LV_COLOR_FORMAT_RGB565A8;
    dsc->header.stride = w * 2;
    dsc->data = data;
    dsc->data_size = w * h * 3;
}

// A label drawn as a rounded pill — the UI's labelling device (card names).
static lv_obj_t* make_pill_styled(lv_obj_t* parent, const char* text, const lv_font_t* font,
                                  int pad_x, int pad_y) {
    lv_obj_t* lbl = lv_label_create(parent);
    lv_label_set_text(lbl, text);
    lv_obj_set_style_text_font(lbl, font, 0);
    lv_obj_set_style_text_color(lbl, COL_TEXT, 0);
    lv_obj_set_style_bg_color(lbl, COL_BAR_BG, 0);
    lv_obj_set_style_bg_opa(lbl, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(lbl, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_pad_left(lbl, pad_x, 0);
    lv_obj_set_style_pad_right(lbl, pad_x, 0);
    lv_obj_set_style_pad_top(lbl, pad_y, 0);
    lv_obj_set_style_pad_bottom(lbl, pad_y, 0);
    return lbl;
}

static lv_obj_t* make_pill(lv_obj_t* parent, const char* text) {
    return make_pill_styled(parent, text, L.pill_font, L.pill_pad_x, L.pill_pad_y);
}

// A small round indicator dot (face / page indicator).
static lv_obj_t* make_dot(lv_obj_t* parent) {
    lv_obj_t* d = lv_obj_create(parent);
    lv_obj_set_size(d, L.dot_size, L.dot_size);
    lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(d, 0, 0);
    lv_obj_set_style_pad_all(d, 0, 0);
    lv_obj_set_style_bg_color(d, COL_DOT_OFF, 0);
    lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
    lv_obj_clear_flag(d, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(d, LV_OBJ_FLAG_SCROLLABLE);
    return d;
}

// Full-screen transparent page root with the shared header title. Pages other
// than Usage get their own so the header stays identical across pages while
// the corner mascot + battery glyph (screen-level objects) persist.
static lv_obj_t* make_page(lv_obj_t* scr, const char* title) {
    lv_obj_t* page = lv_obj_create(scr);
    lv_obj_set_size(page, L.scr_w, L.scr_h);
    lv_obj_set_pos(page, 0, 0);
    lv_obj_set_style_bg_opa(page, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(page, 0, 0);
    lv_obj_set_style_pad_all(page, 0, 0);
    lv_obj_clear_flag(page, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t* t = lv_label_create(page);
    lv_label_set_text(t, title);
    lv_obj_set_style_text_font(t, L.title_font, 0);
    lv_obj_set_style_text_color(t, COL_TEXT, 0);
    lv_obj_align(t, LV_ALIGN_TOP_MID, L.title_nudge, L.title_y);
    return page;
}

static void init_battery_icons(void) {
    if (L.small_icons) {
        init_icon_dsc_rgb565a8(&battery_dscs[0], ICON_BATTERY_SMALL_W, ICON_BATTERY_SMALL_H, icon_battery_small_data);
        init_icon_dsc_rgb565a8(&battery_dscs[1], ICON_BATTERY_LOW_SMALL_W, ICON_BATTERY_LOW_SMALL_H, icon_battery_low_small_data);
        init_icon_dsc_rgb565a8(&battery_dscs[2], ICON_BATTERY_MEDIUM_SMALL_W, ICON_BATTERY_MEDIUM_SMALL_H, icon_battery_medium_small_data);
        init_icon_dsc_rgb565a8(&battery_dscs[3], ICON_BATTERY_FULL_SMALL_W, ICON_BATTERY_FULL_SMALL_H, icon_battery_full_small_data);
        init_icon_dsc_rgb565a8(&battery_dscs[4], ICON_BATTERY_CHARGING_SMALL_W, ICON_BATTERY_CHARGING_SMALL_H, icon_battery_charging_small_data);
        return;
    }
    init_icon_dsc_rgb565a8(&battery_dscs[0], ICON_BATTERY_W, ICON_BATTERY_H, icon_battery_data);
    init_icon_dsc_rgb565a8(&battery_dscs[1], ICON_BATTERY_LOW_W, ICON_BATTERY_LOW_H, icon_battery_low_data);
    init_icon_dsc_rgb565a8(&battery_dscs[2], ICON_BATTERY_MEDIUM_W, ICON_BATTERY_MEDIUM_H, icon_battery_medium_data);
    init_icon_dsc_rgb565a8(&battery_dscs[3], ICON_BATTERY_FULL_W, ICON_BATTERY_FULL_H, icon_battery_full_data);
    init_icon_dsc_rgb565a8(&battery_dscs[4], ICON_BATTERY_CHARGING_W, ICON_BATTERY_CHARGING_H, icon_battery_charging_data);
}

// ======== Usage Screen ========

static lv_obj_t* make_usage_panel(lv_obj_t* parent, int y, const char* pill_text,
                                  lv_obj_t** out_pct, lv_obj_t** out_pill,
                                  lv_obj_t** out_bar, lv_obj_t** out_reset) {
    lv_obj_t* panel = make_panel(parent, L.margin, y, L.content_w, L.usage_panel_h);

    *out_pct = lv_label_create(panel);
    lv_label_set_text(*out_pct, "---%");
    lv_obj_set_style_text_font(*out_pct, L.pct_font, 0);
    lv_obj_set_style_text_color(*out_pct, COL_TEXT, 0);
    lv_obj_set_pos(*out_pct, 0, 0);

    *out_pill = make_pill(panel, pill_text);
    lv_obj_align(*out_pill, LV_ALIGN_TOP_RIGHT, 0, 1);

    *out_bar = make_bar(panel, 0, L.usage_bar_y,
                        L.content_w - 2 * L.panel_pad_x, L.bar_h);

    *out_reset = lv_label_create(panel);
    lv_label_set_text(*out_reset, "---");
    lv_obj_set_style_text_font(*out_reset, L.reset_font, 0);
    lv_obj_set_style_text_color(*out_reset, COL_DIM, 0);
    lv_obj_set_pos(*out_reset, 0, L.usage_reset_y);

    return panel;
}

// Draw the Weekly card's current face from the cached payload. Face 0 is the
// classic all-models rendering; a scoped face swaps the number, the pill's
// text (the model's own label) and the bar — the reset line is shared because
// every weekly limit resets at the same instant. Flips snap (no bar slide);
// fresh payloads animate as they always did.
static void render_weekly_face(bool animate) {
    const int faces = 1 + cached_scoped_count;
    if (weekly_face >= faces) weekly_face = 0;
    const bool scoped = weekly_face > 0;
    const ScopedWeekly* sw = scoped ? &cached_scoped[weekly_face - 1] : nullptr;
    const float pct = scoped ? sw->pct : cached_weekly_pct;
    const int p = (int)(pct + 0.5f);

    lv_label_set_text(lbl_weekly_label, scoped ? sw->name : "Weekly");
    lv_label_set_text_fmt(lbl_weekly_pct, "%d%%", p);
    lv_bar_set_value(bar_weekly, p, animate ? LV_ANIM_ON : LV_ANIM_OFF);
    lv_obj_set_style_bg_color(bar_weekly, pct_color(pct), LV_PART_INDICATOR);
    char buf[48];
    format_reset_time(cached_weekly_reset, buf, sizeof(buf));
    lv_label_set_text(lbl_weekly_reset, buf);

    // Indicator dots, right-aligned on the reset row, face 0 leftmost. Hidden
    // entirely on single-face plans so the classic card stays untouched.
    for (int i = 0; i <= MAX_SCOPED_WEEKLY; i++) {
        if (!face_dots[i]) continue;
        if (faces > 1 && i < faces) {
            lv_obj_clear_flag(face_dots[i], LV_OBJ_FLAG_HIDDEN);
            lv_obj_align(face_dots[i], LV_ALIGN_TOP_RIGHT,
                         -(faces - 1 - i) * (L.dot_size + L.dot_gap), L.face_dot_y);
            lv_obj_set_style_bg_color(face_dots[i], i == weekly_face ? COL_TEXT : COL_DOT_OFF, 0);
        } else {
            lv_obj_add_flag(face_dots[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
}

static void flip_weekly_face(uint32_t hold_ms) {
    if (cached_scoped_count <= 0) return;
    weekly_face = (weekly_face + 1) % (1 + cached_scoped_count);
    face_next_auto_ms = lv_tick_get() + hold_ms;
    render_weekly_face(false);
}

// Tap on the Weekly card: flip to the next face and hold it a while. Only wired
// (bubbling disabled) when the plan actually has faces — otherwise the tap
// bubbles up and toggles the splash like anywhere else on the screen.
static void weekly_click_cb(lv_event_t* e) {
    (void)e;
    if (click_guarded()) return;
    if (cached_scoped_count <= 0 || view_state != 2) return;
    flip_weekly_face(FACE_HOLD_MS);
}

// Pairing hint — shown when disconnected so the screen isn't empty and the
// user knows how to (re)pair. Wording matches the 3-second release gesture.
static void build_pair_group(lv_obj_t* parent) {
    pair_group = lv_obj_create(parent);
    lv_obj_set_size(pair_group, L.scr_w, L.scr_h - L.content_y);
    lv_obj_set_pos(pair_group, 0, L.content_y);
    lv_obj_set_style_bg_opa(pair_group, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(pair_group, 0, 0);
    lv_obj_set_style_pad_all(pair_group, 0, 0);
    lv_obj_clear_flag(pair_group, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(pair_group, LV_OBJ_FLAG_EVENT_BUBBLE);

    lv_obj_t* l1 = lv_label_create(pair_group);
    lv_label_set_text(l1, "To pair");
    lv_obj_set_style_text_font(l1, L.bt_status_font, 0);
    lv_obj_set_style_text_color(l1, COL_TEXT, 0);
    lv_obj_align(l1, LV_ALIGN_TOP_MID, 0, L.pair_y1);

    lv_obj_t* l2 = lv_label_create(pair_group);
    lv_label_set_text(l2, "hold the power button");
    lv_obj_set_style_text_font(l2, L.bt_device_font, 0);
    lv_obj_set_style_text_color(l2, COL_DIM, 0);
    lv_obj_align(l2, LV_ALIGN_TOP_MID, 0, L.pair_y2);

    lv_obj_t* l3 = lv_label_create(pair_group);
    lv_label_set_text(l3, "for 3 seconds, then release");
    lv_obj_set_style_text_font(l3, L.bt_device_font, 0);
    lv_obj_set_style_text_color(l3, COL_DIM, 0);
    lv_obj_align(l3, LV_ALIGN_TOP_MID, 0, L.pair_y3);

    lv_obj_add_flag(pair_group, LV_OBJ_FLAG_HIDDEN);  // ui_update_ble_status decides
}

// Idle "Zzz" screen — shown when the host is connected but no usage update has
// landed recently (token expired, daemon down, host asleep…). Full-screen, like
// the pairing hint, so we never render hours-old numbers as if they were live.
static void build_idle_group(lv_obj_t* parent) {
    idle_group = lv_obj_create(parent);
    lv_obj_set_size(idle_group, L.scr_w, L.scr_h - L.content_y);
    lv_obj_set_pos(idle_group, 0, L.content_y);
    lv_obj_set_style_bg_opa(idle_group, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(idle_group, 0, 0);
    lv_obj_set_style_pad_all(idle_group, 0, 0);
    lv_obj_clear_flag(idle_group, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(idle_group, LV_OBJ_FLAG_EVENT_BUBBLE);

    // A shrunk-down resting creature (the official cloud-ride animation)
    // sits between the header and the status line; the animated "Listening…"
    // status line carries the words, so no extra text is needed here.
    lv_obj_t* creature = splash_mini_create(idle_group, "cloud", L.idle_px);
    if (creature) lv_obj_align(creature, LV_ALIGN_CENTER, 0, -20);

    lv_obj_add_flag(idle_group, LV_OBJ_FLAG_HIDDEN);  // update_view_state decides
}

static void init_usage_screen(lv_obj_t* scr) {
    usage_container = lv_obj_create(scr);
    lv_obj_set_size(usage_container, L.scr_w, L.scr_h);
    lv_obj_set_pos(usage_container, 0, 0);
    lv_obj_set_style_bg_opa(usage_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(usage_container, 0, 0);
    lv_obj_set_style_pad_all(usage_container, 0, 0);
    lv_obj_clear_flag(usage_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(usage_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    lbl_title = lv_label_create(usage_container);
    lv_label_set_text(lbl_title, "Usage");
    lv_obj_set_style_text_font(lbl_title, L.title_font, 0);
    lv_obj_set_style_text_color(lbl_title, COL_TEXT, 0);
    // The nudge balances the corner logo on the left; smaller on small
    // screens where the logo is 40px and the battery icon sits closer.
    lv_obj_align(lbl_title, LV_ALIGN_TOP_MID, L.title_nudge, L.title_y);

    // Usage panels (shown when connected) live in a transparent full-size group
    // so they can be toggled against the pairing hint as one unit.
    usage_group = lv_obj_create(usage_container);
    lv_obj_set_size(usage_group, L.scr_w, L.scr_h);
    lv_obj_set_pos(usage_group, 0, 0);
    lv_obj_set_style_bg_opa(usage_group, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(usage_group, 0, 0);
    lv_obj_set_style_pad_all(usage_group, 0, 0);
    lv_obj_clear_flag(usage_group, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(usage_group, LV_OBJ_FLAG_EVENT_BUBBLE);

    panel_session = make_usage_panel(usage_group, L.content_y, "Current",
                     &lbl_session_pct, &lbl_session_label,
                     &bar_session, &lbl_session_reset);

    // Enterprise-only overlays inside panel_session — hidden until enterprise data arrives
    lbl_session_pct_sym = lv_label_create(panel_session);
    lv_label_set_text(lbl_session_pct_sym, "%");
    lv_obj_set_style_text_font(lbl_session_pct_sym, L.reset_font, 0);
    lv_obj_set_style_text_color(lbl_session_pct_sym, COL_TEXT, 0);
    lv_obj_add_flag(lbl_session_pct_sym, LV_OBJ_FLAG_HIDDEN);

    lbl_spending_desc = lv_label_create(panel_session);
    lv_label_set_text(lbl_spending_desc, "of your monthly budget");
    lv_obj_set_style_text_font(lbl_spending_desc, L.reset_font, 0);
    lv_obj_set_style_text_color(lbl_spending_desc, COL_DIM, 0);
    lv_obj_set_pos(lbl_spending_desc, 0, L.usage_reset_y);
    lv_obj_add_flag(lbl_spending_desc, LV_OBJ_FLAG_HIDDEN);

    lbl_spending_status = lv_label_create(panel_session);
    lv_label_set_text(lbl_spending_status, "");
    lv_obj_set_style_text_font(lbl_spending_status, L.pace_font, 0);
    lv_obj_set_pos(lbl_spending_status, 0, L.usage_reset_y + 20);
    lv_obj_add_flag(lbl_spending_status, LV_OBJ_FLAG_HIDDEN);

    panel_weekly = make_usage_panel(usage_group,
                     L.content_y + L.usage_panel_h + L.usage_panel_gap, "Weekly",
                     &lbl_weekly_pct, &lbl_weekly_label,
                     &bar_weekly, &lbl_weekly_reset);
    // Recolor enabled so enterprise period box can color pace and reset separately
    lv_label_set_recolor(lbl_weekly_reset, true);

    // Face indicator dots on the reset row (hidden on single-face plans) and
    // the tap-to-flip handler. Bubbling to the splash toggle is switched off
    // in ui_update() only while faces exist.
    for (int i = 0; i <= MAX_SCOPED_WEEKLY; i++) {
        face_dots[i] = make_dot(panel_weekly);
        lv_obj_add_flag(face_dots[i], LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_add_event_cb(panel_weekly, weekly_click_cb, LV_EVENT_CLICKED, NULL);

    build_pair_group(usage_container);
    build_idle_group(usage_container);

    // Status line — always visible on the usage view. Driven by ui_tick_anim().
    lbl_anim = lv_label_create(usage_container);
    lv_label_set_text(lbl_anim, "");
    lv_obj_set_style_text_font(lbl_anim, L.anim_font, 0);
    lv_obj_set_style_text_color(lbl_anim, COL_ACCENT, 0);
    lv_obj_align(lbl_anim, LV_ALIGN_BOTTOM_MID, 0, L.anim_y);
}

// ======== Settings Screen ========
//
// 2×2 tiles per page. Each tile is one setting and one big button: tapping it
// cycles the value and persists it (settings.cpp / brightness.cpp), then the
// change is applied live. A pressed tile darkens for tactile feedback.

static lv_obj_t* make_transparent_group(lv_obj_t* parent) {
    lv_obj_t* g = lv_obj_create(parent);
    lv_obj_set_size(g, L.scr_w, L.scr_h);
    lv_obj_set_pos(g, 0, 0);
    lv_obj_set_style_bg_opa(g, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(g, 0, 0);
    lv_obj_set_style_pad_all(g, 0, 0);
    lv_obj_clear_flag(g, LV_OBJ_FLAG_SCROLLABLE);
    return g;
}

static void build_settings_screen(lv_obj_t* scr) {
    settings_container = make_page(scr, "Settings");

    const int area_h = L.tiles_bottom - L.content_y;
    const int tile_w = (L.content_w - L.tile_gap) / 2;
    const int tile_h = (area_h - L.tile_gap) / 2;

    for (int pg = 0; pg < SET_PAGES; pg++) {
        lv_obj_t* page = make_transparent_group(settings_container);
        settings_pages[pg] = page;
        for (int k = 0; k < SET_TILES_PER_PAGE; k++) {
            const int id = pg * SET_TILES_PER_PAGE + k;
            if (id >= SET_COUNT) break;
            const int col = k % 2, row = k / 2;

            lv_obj_t* tile = lv_obj_create(page);
            lv_obj_set_pos(tile, L.margin + col * (tile_w + L.tile_gap),
                                 L.content_y + row * (tile_h + L.tile_gap));
            lv_obj_set_size(tile, tile_w, tile_h);
            lv_obj_set_style_bg_color(tile, COL_PANEL, 0);
            lv_obj_set_style_bg_color(tile, COL_PRESSED, LV_STATE_PRESSED);
            lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, 0);
            lv_obj_set_style_radius(tile, L.tile_radius, 0);
            lv_obj_set_style_border_width(tile, 0, 0);
            lv_obj_set_style_pad_left(tile, L.tile_pad_x, 0);
            lv_obj_set_style_pad_right(tile, L.tile_pad_x, 0);
            lv_obj_set_style_pad_top(tile, L.tile_pad_y, 0);
            lv_obj_set_style_pad_bottom(tile, L.tile_pad_y, 0);
            lv_obj_clear_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_add_event_cb(tile, settings_tile_cb, LV_EVENT_CLICKED, (void*)(intptr_t)id);

            lv_obj_t* lbl = lv_label_create(tile);
            lv_label_set_text(lbl, SET_LABELS[id]);
            lv_obj_set_style_text_font(lbl, L.tile_label_font, 0);
            lv_obj_set_style_text_color(lbl, COL_DIM, 0);
            lv_obj_align(lbl, LV_ALIGN_TOP_LEFT, 0, 0);

            lv_obj_t* val = lv_label_create(tile);
            lv_label_set_text(val, "");
            lv_obj_set_style_text_font(val, L.tile_value_font, 0);
            lv_obj_set_style_text_color(val, COL_TEXT, 0);
            lv_obj_align(val, LV_ALIGN_BOTTOM_LEFT, 0, 0);
            tile_value[id] = val;
        }
        if (pg > 0) lv_obj_add_flag(page, LV_OBJ_FLAG_HIDDEN);
    }

    // Page indicator dots, centred under the grid.
    const int total_w = SET_PAGES * L.dot_size + (SET_PAGES - 1) * L.dot_gap;
    for (int pg = 0; pg < SET_PAGES; pg++) {
        page_dots[pg] = make_dot(settings_container);
        lv_obj_set_pos(page_dots[pg], (L.scr_w - total_w) / 2 + pg * (L.dot_size + L.dot_gap),
                       L.dots_y);
    }

    show_settings_page(0);
    refresh_settings_tiles();
    lv_obj_add_flag(settings_container, LV_OBJ_FLAG_HIDDEN);
}

static void show_settings_page(int page) {
    if (page < 0) page = 0;
    if (page >= SET_PAGES) page = SET_PAGES - 1;
    settings_page = page;
    for (int pg = 0; pg < SET_PAGES; pg++) {
        if (pg == page) lv_obj_clear_flag(settings_pages[pg], LV_OBJ_FLAG_HIDDEN);
        else            lv_obj_add_flag(settings_pages[pg], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_bg_color(page_dots[pg], pg == page ? COL_TEXT : COL_DOT_OFF, 0);
    }
}

// A tile's value reads bright when the feature is on and dim when it is off,
// so the state is visible at a glance, not just by reading the word.
static void set_tile_value(int id, const char* text, bool on) {
    lv_label_set_text(tile_value[id], text);
    lv_obj_set_style_text_color(tile_value[id], on ? COL_TEXT : COL_DIM, 0);
}

static void refresh_settings_tiles(void) {
    if (!settings_container) return;
    const Settings& s = settings_get();
    set_tile_value(SET_CLOCK, settings_clock_label(s.clock), s.clock != CLOCK_OFF);
    if (!board_caps().has_battery) set_tile_value(SET_BATTERY, "None", false);
    else set_tile_value(SET_BATTERY, s.show_battery ? "Shown" : "Hidden", s.show_battery);
    set_tile_value(SET_MASCOT, s.show_mascot ? "Shown" : "Hidden", s.show_mascot);
    set_tile_value(SET_STATUS, s.show_status ? "Shown" : "Hidden", s.show_status);
    set_tile_value(SET_BRIGHTNESS, brightness_label(brightness_get_index()), true);
    set_tile_value(SET_SLEEP, settings_sleep_label(s.sleep), s.sleep != SLEEP_NEVER);
    set_tile_value(SET_ABOUT, FW_VERSION, true);

    const char* pair_text = pairing_cleared_ms ? "Cleared"
                          : pairing_confirm_ms ? "Confirm?"
                          : "Forget host";
    lv_label_set_text(tile_value[SET_PAIRING], pair_text);
    lv_obj_set_style_text_color(tile_value[SET_PAIRING],
                                pairing_confirm_ms ? COL_ACCENT : COL_TEXT, 0);
}

static void settings_tile_cb(lv_event_t* e) {
    if (click_guarded()) return;
    const int id = (int)(intptr_t)lv_event_get_user_data(e);
    const Settings& s = settings_get();
    switch (id) {
    case SET_CLOCK:
        settings_set_clock((uint8_t)((s.clock + 1) % CLOCK_MODE_COUNT));
        clock_last_min = -1;   // re-render the title on the next usage tick
        break;
    case SET_BATTERY:
        if (board_caps().has_battery) settings_set_show_battery(!s.show_battery);
        break;
    case SET_MASCOT:     settings_set_show_mascot(!s.show_mascot); break;
    case SET_STATUS:     settings_set_show_status(!s.show_status); break;
    case SET_BRIGHTNESS: brightness_cycle(); break;
    case SET_SLEEP:
        settings_set_sleep((uint8_t)((s.sleep + 1) % SLEEP_MODE_COUNT));
        break;
    case SET_PAIRING:
        // Two taps within PAIRING_CONFIRM_MS: forget the bonded host and
        // re-advertise (same effect as the hold-power gesture).
        if (pairing_cleared_ms) break;
        if (pairing_confirm_ms) {
            ble_clear_bonds();
            pairing_confirm_ms = 0;
            pairing_cleared_ms = lv_tick_get();
        } else {
            pairing_confirm_ms = lv_tick_get();
        }
        break;
    case SET_ABOUT:
        ui_show_screen(SCREEN_ABOUT);
        return;
    default: break;
    }
    ui_refresh_settings();
}

// ======== About Screen ========

static void build_about_screen(lv_obj_t* scr) {
    about_container = make_page(scr, "About");

    const int rows_h = ABOUT_COUNT * L.about_row_h;
    lv_obj_t* panel = make_panel(about_container, L.margin, L.content_y, L.content_w,
                                 rows_h + 2 * L.panel_pad_y);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_EVENT_BUBBLE);
    for (int i = 0; i < ABOUT_COUNT; i++) {
        const int y = i * L.about_row_h;
        lv_obj_t* key = lv_label_create(panel);
        lv_label_set_text(key, ABOUT_KEYS[i]);
        lv_obj_set_style_text_font(key, L.about_key_font, 0);
        lv_obj_set_style_text_color(key, COL_DIM, 0);
        lv_obj_set_pos(key, 0, y);

        about_value[i] = lv_label_create(panel);
        lv_label_set_text(about_value[i], "");
        lv_obj_set_style_text_font(about_value[i], L.about_val_font, 0);
        lv_obj_set_style_text_color(about_value[i], COL_TEXT, 0);
        lv_obj_align(about_value[i], LV_ALIGN_TOP_RIGHT, 0, y);
    }

    lv_obj_t* hint = lv_label_create(about_container);
    lv_label_set_text(hint, "Swipe right to go back");
    lv_obj_set_style_text_font(hint, L.about_hint_font, 0);
    lv_obj_set_style_text_color(hint, COL_DIM, 0);
    lv_obj_align(hint, LV_ALIGN_BOTTOM_MID, 0, L.about_hint_y - L.about_row_h);

    lv_obj_t* credit = lv_label_create(about_container);
    lv_label_set_text(credit, "github.com/TheOriUHD/Clawdmeter");
    lv_obj_set_style_text_font(credit, L.about_hint_font, 0);
    lv_obj_set_style_text_color(credit, COL_DIM, 0);
    lv_obj_align(credit, LV_ALIGN_BOTTOM_MID, 0, L.about_hint_y);

    refresh_about();
    lv_obj_add_flag(about_container, LV_OBJ_FLAG_HIDDEN);
}

static void refresh_about(void) {
    if (!about_container) return;
    char buf[48];
    lv_label_set_text(about_value[ABOUT_BOARD], board_caps().name);
    lv_label_set_text(about_value[ABOUT_FW], FW_VERSION);
    lv_label_set_text(about_value[ABOUT_BLE], s_ble_connected ? "Connected" : "Advertising");
    lv_label_set_text(about_value[ABOUT_ADDR], ble_get_mac_address());

    if (!board_caps().has_battery)   snprintf(buf, sizeof(buf), "None");
    else if (batt_pct_cached < 0)    snprintf(buf, sizeof(buf), "n/a");
    else snprintf(buf, sizeof(buf), "%d%%%s", batt_pct_cached,
                  batt_charging_cached ? " \xE2\x80\xA2 charging" : "");
    lv_label_set_text(about_value[ABOUT_BATTERY], buf);

    const size_t free_b = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    if (free_b) snprintf(buf, sizeof(buf), "%u KB", (unsigned)(free_b / 1024));
    else        snprintf(buf, sizeof(buf), "n/a");
    lv_label_set_text(about_value[ABOUT_RAM], buf);

    const uint32_t up_s = lv_tick_get() / 1000;
    if (up_s >= 3600) snprintf(buf, sizeof(buf), "%luh %02lum",
                               (unsigned long)(up_s / 3600), (unsigned long)((up_s % 3600) / 60));
    else              snprintf(buf, sizeof(buf), "%lum %02lus",
                               (unsigned long)(up_s / 60), (unsigned long)(up_s % 60));
    lv_label_set_text(about_value[ABOUT_UPTIME], buf);
}

// ======== Public API ========

void ui_init(void) {
    compute_layout(board_caps());

    lv_obj_t* scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, COL_BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    // A screen that can scroll swallows horizontal drags as scroll attempts
    // (the walking mascot briefly pokes past the edge on PSRAM boards). Pages
    // never overflow, so disable it and let swipes reach the gesture handler.
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(scr, gesture_cb, LV_EVENT_GESTURE, NULL);

#ifndef BOARD_HAS_PSRAM
    // Static corner mascot (see clawd_still.h) — the animated one needs PSRAM.
    if (L.small_icons) init_icon_dsc_rgb565a8(&logo_dsc, CLAWD_STILL_SMALL_W, CLAWD_STILL_SMALL_H, clawd_still_small_data);
    else               init_icon_dsc_rgb565a8(&logo_dsc, CLAWD_STILL_W, CLAWD_STILL_H, clawd_still_data);
#endif
    init_battery_icons();

    init_usage_screen(scr);
    build_settings_screen(scr);
    build_about_screen(scr);
    splash_init(scr);

    if (splash_get_root()) {
        lv_obj_add_event_cb(splash_get_root(), global_click_cb, LV_EVENT_CLICKED, NULL);
    }

    // Corner mascot in the old logo slot. The still Clawd is shorter than the
    // 80/40 px slot the spark logo used; center it vertically in that slot.
    {
        const int slot  = L.small_icons ? LOGO_SMALL_HEIGHT : LOGO_HEIGHT;
        const int art_h = L.small_icons ? CLAWD_STILL_SMALL_H : CLAWD_STILL_H;
        const int top   = L.logo_y + (slot - art_h) / 2;
#ifdef BOARD_HAS_PSRAM
        // Animated: idles, does acts, and takes walk-off/lurk trips.
        splash_mascot_create(scr, L.margin, top + art_h, L.small_icons ? 2 : 3);
#else
        logo_img = lv_image_create(scr);
        lv_image_set_src(logo_img, &logo_dsc);
        lv_obj_set_pos(logo_img, L.margin, top);
#endif
    }

    battery_img = lv_image_create(scr);
    lv_image_set_src(battery_img, &battery_dscs[0]);
    lv_obj_set_pos(battery_img, L.scr_w - L.batt_w - L.margin, L.batt_y);
    // Boards without battery telemetry never show the indicator (per the HAL
    // contract; previously every board drew the empty-battery glyph).
    if (!board_caps().has_battery) {
        lv_obj_del(battery_img);
        battery_img = nullptr;
    }
}

void ui_update(const UsageData* data) {
    if (!data->valid) return;
    data_ok = data->ok;
    if (!data->ok) return;          // a {"ok":false} "no data" beat → fall through to idle, keep last numbers
    last_data_ms = lv_tick_get();   // a real usage update just landed
    data_received = true;

    if (data->clock_epoch > 0) {    // daemon supplied wall-clock time → the title clock may run
        clock_base_epoch = data->clock_epoch;
        clock_base_ms = last_data_ms;
        clock_fmt = data->clock_fmt;
    } else {                        // no time from the daemon → the title reverts to "Usage"
        clock_base_epoch = 0;
    }

    int s_pct = (int)(data->session_pct + 0.5f);

    if (data->enterprise) {
        // Spending box: big number-only label + small "%" symbol + desc + pace
        lv_obj_set_style_text_font(lbl_session_pct, L.ent_pct_font, 0);
        lv_label_set_text(lbl_session_label, "Spending");
        lv_obj_add_flag(lbl_session_reset, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(lbl_session_pct_sym, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(lbl_spending_desc,   LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(lbl_spending_status,   LV_OBJ_FLAG_HIDDEN);
        if (panel_weekly) lv_obj_clear_flag(panel_weekly, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_set_style_text_font(lbl_session_pct, L.pct_font, 0);
        lv_label_set_text(lbl_session_label, "Current");
        lv_obj_clear_flag(lbl_session_reset, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(lbl_session_pct_sym, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(lbl_spending_desc,   LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(lbl_spending_status, LV_OBJ_FLAG_HIDDEN);
        if (panel_weekly) lv_obj_clear_flag(panel_weekly, LV_OBJ_FLAG_HIDDEN);
    }

    // Weekly faces. Scoped limits (e.g. Fable) come from the "ws" key: absence →
    // one face, classic card; 0% is a real reading. Enterprise has no weekly
    // window at all. While faces exist the Weekly card handles its own taps
    // (flip) instead of bubbling them up to the splash toggle.
    cached_scoped_count = data->enterprise ? 0 : data->scoped_weekly_count;
    for (int i = 0; i < cached_scoped_count; i++) cached_scoped[i] = data->scoped_weekly[i];
    if (weekly_face > cached_scoped_count) weekly_face = 0;
    if (cached_scoped_count > 0) {
        lv_obj_clear_flag(panel_weekly, LV_OBJ_FLAG_EVENT_BUBBLE);
        lv_obj_set_style_bg_color(panel_weekly, COL_PRESSED, LV_STATE_PRESSED);
        if (face_next_auto_ms == 0) face_next_auto_ms = last_data_ms + FACE_AUTO_MS;
    } else {
        lv_obj_add_flag(panel_weekly, LV_OBJ_FLAG_EVENT_BUBBLE);
        lv_obj_set_style_bg_color(panel_weekly, COL_PANEL, LV_STATE_PRESSED);
        face_next_auto_ms = 0;
    }

    char buf[48];

    // Pace vars used in both enterprise blocks below
    const char* pace_text = "Under pace";
    lv_color_t  pace_color = COL_GREEN;
    const char* pace_hex   = "788c5d";   // matches THEME_GREEN
    if (data->session_pct > (float)data->time_pct + 15.0f) {
        pace_text = "Over pace";  pace_color = COL_RED;   pace_hex = "c0392b";
    } else if (data->session_pct > (float)data->time_pct - 15.0f) {
        pace_text = "On pace";    pace_color = COL_AMBER; pace_hex = "d97757";
    }

    if (data->enterprise) {
        lv_label_set_text_fmt(lbl_session_pct, "%d", s_pct);
        lv_obj_align_to(lbl_session_pct_sym, lbl_session_pct,
                        LV_ALIGN_OUT_RIGHT_TOP, 4, 12);
    } else {
        lv_label_set_text_fmt(lbl_session_pct, "%d%%", s_pct);
        format_reset_time(data->session_reset_mins, buf, sizeof(buf));
        lv_label_set_text(lbl_session_reset, buf);
    }

    lv_bar_set_value(bar_session, s_pct, LV_ANIM_ON);
    lv_obj_set_style_bg_color(bar_session, pct_color(data->session_pct), LV_PART_INDICATOR);

    if (data->enterprise) {
        // Period box: time % + dynamic pace color + "Resets <date>" label
        lv_label_set_text(lbl_weekly_label, "Period");
        lv_label_set_text_fmt(lbl_weekly_pct, "%d%%", data->time_pct);
        lv_bar_set_value(bar_weekly, data->time_pct, LV_ANIM_ON);
        lv_color_t bar_pace = (data->session_pct <= (float)data->time_pct) ? COL_GREEN :
                              (data->session_pct <= (float)data->time_pct + 15.0f) ? COL_AMBER :
                              COL_RED;
        lv_obj_set_style_bg_color(bar_weekly, bar_pace, LV_PART_INDICATOR);
        snprintf(buf, sizeof(buf), "#%s %s# - #faf9f5 Resets %s#",
                 pace_hex, pace_text, data->reset_date);
        lv_label_set_text(lbl_weekly_reset, buf);
    } else {
        cached_weekly_pct = data->weekly_pct;
        cached_weekly_reset = data->weekly_reset_mins;
        render_weekly_face(true);
    }
}

// Pick the usage-view sub-screen: pairing hint (BLE down), the idle "Zzz" screen
// (connected but data has gone stale), or the live usage panels. Only re-lays-out
// on an actual change. The animated status line stays visible everywhere — it
// reads "Listening…" on the idle screen, keeping it alive rather than frozen.
static void update_view_state(void) {
    if (!usage_group || !pair_group || !idle_group) return;
    int v;
    if (!s_ble_connected) {
        v = 0;  // pairing hint
    } else if (data_received && data_ok && (lv_tick_get() - last_data_ms) < DATA_FRESH_MS) {
        v = 2;  // live usage
    } else {
        v = 1;  // idle / Zzz
    }
    if (v == view_state) return;
    view_state = v;
    lv_obj_add_flag(pair_group, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(idle_group, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(usage_group, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(v == 0 ? pair_group : v == 1 ? idle_group : usage_group,
                      LV_OBJ_FLAG_HIDDEN);
}

// The title shows the time only when the daemon supplied it AND the device's
// Clock setting is on; the format is the device's choice, or the host's when
// set to Auto.
static void tick_title_clock(uint32_t now) {
    const uint8_t mode = settings_get().clock;
    const bool wanted = (mode != CLOCK_OFF) && clock_base_epoch > 0;
    if (!wanted) {
        if (title_is_clock) {
            lv_label_set_text(lbl_title, "Usage");
            title_is_clock = false;
            clock_last_min = -1;
        }
        return;
    }
    const int fmt = (mode == CLOCK_12H) ? 12 : (mode == CLOCK_24H) ? 24 : clock_fmt;
    time_t cur = (time_t)(clock_base_epoch + (now - clock_base_ms) / 1000);
    struct tm tmv;
    gmtime_r(&cur, &tmv);   // epoch is already local wall-clock → gmtime keeps it as-is
    if (tmv.tm_min == clock_last_min && title_is_clock) return;   // only rewrite when the minute changes
    clock_last_min = tmv.tm_min;
    title_is_clock = true;
    char tbuf[12];
    if (fmt == 12) {
        int h12 = tmv.tm_hour % 12;
        if (h12 == 0) h12 = 12;
        snprintf(tbuf, sizeof(tbuf), "%d:%02d %s", h12, tmv.tm_min,
                 tmv.tm_hour < 12 ? "AM" : "PM");
    } else {
        snprintf(tbuf, sizeof(tbuf), "%02d:%02d", tmv.tm_hour, tmv.tm_min);
    }
    lv_label_set_text(lbl_title, tbuf);
}

void ui_tick_anim(void) {
    const uint32_t now = lv_tick_get();

    if (current_screen == SCREEN_ABOUT) {
        if (now - about_last_refresh_ms >= 1000) {
            about_last_refresh_ms = now;
            refresh_about();
        }
        return;
    }
    if (current_screen == SCREEN_SETTINGS) {
        // Expire the two-tap pairing confirmation / the "Cleared" notice.
        if (pairing_confirm_ms && now - pairing_confirm_ms >= PAIRING_CONFIRM_MS) {
            pairing_confirm_ms = 0;
            refresh_settings_tiles();
        }
        if (pairing_cleared_ms && now - pairing_cleared_ms >= PAIRING_CLEARED_MS) {
            pairing_cleared_ms = 0;
            refresh_settings_tiles();
        }
        return;
    }
    if (current_screen != SCREEN_USAGE) return;

    update_view_state();
    if (view_state == 1) splash_mini_tick();   // animate the sleeping creature on the idle screen

    tick_title_clock(now);

    // Weekly card: advance the face on its own clock (a tap resets the clock
    // with a longer hold, see weekly_click_cb).
    if (cached_scoped_count > 0 && view_state == 2 && face_next_auto_ms &&
        (int32_t)(now - face_next_auto_ms) >= 0) {
        flip_weekly_face(FACE_AUTO_MS);
    }

    if (now - anim_msg_start >= ANIM_MSG_MS) {
        anim_msg_idx = (anim_msg_idx + 1) % ANIM_MSG_COUNT;
        anim_msg_start = now;
    }

    if (now - anim_last_ms < spinner_ms[anim_spinner_idx]) return;
    anim_last_ms = now;
    anim_phase = (anim_phase + 1) % SPINNER_PHASES;
    anim_spinner_idx = (anim_phase < SPINNER_COUNT) ? anim_phase
                                                    : (SPINNER_PHASES - anim_phase);

    // Status text by priority. Whimsical messages only when connected & settled.
    const char* text;
    if (!s_ble_connected) {
        text = "Waiting";              // advertising / waiting for a host connection
    } else if (view_state == 1) {      // idle — alternate so it reads as alive AND data-less
        text = (anim_msg_idx & 1) ? "No data" : "Listening";
    } else if (now - connected_at_ms < 5000) {
        text = "Connected";
    } else {
        text = anim_messages[anim_msg_idx];
    }

    // All states share the whimsical style: "<glyph> <Title-case word>…"
    static char buf[80];
    snprintf(buf, sizeof(buf), "%s %s\xE2\x80\xA6",
             spinner_frames[anim_spinner_idx], text);
    lv_label_set_text(lbl_anim, buf);
}

static screen_t prev_non_splash_screen = SCREEN_USAGE;

// Header chrome (battery glyph, corner mascot) and the status ticker follow the
// user's settings on every non-splash page; the splash hides all of them.
static void apply_header_visibility(void) {
    const Settings& s = settings_get();
    const bool on_splash = (current_screen == SCREEN_SPLASH);
    if (battery_img) {
        if (on_splash || !s.show_battery) lv_obj_add_flag(battery_img, LV_OBJ_FLAG_HIDDEN);
        else                              lv_obj_clear_flag(battery_img, LV_OBJ_FLAG_HIDDEN);
    }
    const bool mascot = !on_splash && s.show_mascot;
    splash_mascot_set_visible(mascot);
    if (logo_img) {
        if (mascot) lv_obj_clear_flag(logo_img, LV_OBJ_FLAG_HIDDEN);
        else        lv_obj_add_flag(logo_img, LV_OBJ_FLAG_HIDDEN);
    }
    if (lbl_anim) {
        if (s.show_status) lv_obj_clear_flag(lbl_anim, LV_OBJ_FLAG_HIDDEN);
        else               lv_obj_add_flag(lbl_anim, LV_OBJ_FLAG_HIDDEN);
    }
}

static void global_click_cb(lv_event_t* e) {
    (void)e;
    if (click_guarded()) return;
    if (current_screen == SCREEN_SPLASH) ui_show_screen(prev_non_splash_screen);
    else                                  ui_show_screen(SCREEN_SPLASH);
}

// Horizontal swipes page between Usage → Settings → About (left) and back
// (right); on the splash any swipe returns to the last page.
static void gesture_cb(lv_event_t* e) {
    (void)e;
    lv_indev_t* indev = lv_indev_active();
    if (!indev) return;
    const lv_dir_t dir = lv_indev_get_gesture_dir(indev);
    if (dir != LV_DIR_LEFT && dir != LV_DIR_RIGHT) return;
    last_gesture_ms = lv_tick_get();
    switch (current_screen) {
    case SCREEN_SPLASH:
        ui_show_screen(prev_non_splash_screen);
        break;
    case SCREEN_USAGE:
        if (dir == LV_DIR_LEFT) ui_show_settings_page(0);
        break;
    case SCREEN_SETTINGS:
        if (dir == LV_DIR_LEFT) {
            if (settings_page + 1 < SET_PAGES) show_settings_page(settings_page + 1);
            else                               ui_show_screen(SCREEN_ABOUT);
        } else {
            if (settings_page > 0) show_settings_page(settings_page - 1);
            else                   ui_show_screen(SCREEN_USAGE);
        }
        break;
    case SCREEN_ABOUT:
        if (dir == LV_DIR_RIGHT) ui_show_settings_page(SET_PAGES - 1);
        break;
    default: break;
    }
}

void ui_show_screen(screen_t screen) {
    lv_obj_add_flag(usage_container, LV_OBJ_FLAG_HIDDEN);
    if (settings_container) lv_obj_add_flag(settings_container, LV_OBJ_FLAG_HIDDEN);
    if (about_container)    lv_obj_add_flag(about_container, LV_OBJ_FLAG_HIDDEN);
    splash_hide();

    switch (screen) {
    case SCREEN_SPLASH:   splash_show(); break;
    case SCREEN_USAGE:    lv_obj_clear_flag(usage_container, LV_OBJ_FLAG_HIDDEN); break;
    case SCREEN_SETTINGS:
        pairing_confirm_ms = 0;
        pairing_cleared_ms = 0;
        refresh_settings_tiles();
        lv_obj_clear_flag(settings_container, LV_OBJ_FLAG_HIDDEN);
        break;
    case SCREEN_ABOUT:
        refresh_about();
        about_last_refresh_ms = lv_tick_get();
        lv_obj_clear_flag(about_container, LV_OBJ_FLAG_HIDDEN);
        break;
    default: break;
    }

    if (screen != SCREEN_SPLASH) prev_non_splash_screen = screen;
    current_screen = screen;
    apply_header_visibility();
}

void ui_toggle_splash(void) {
    if (current_screen == SCREEN_SPLASH) ui_show_screen(prev_non_splash_screen);
    else                                  ui_show_screen(SCREEN_SPLASH);
}

screen_t ui_get_current_screen(void) {
    return current_screen;
}

void ui_refresh_settings(void) {
    refresh_settings_tiles();
    apply_header_visibility();
    clock_last_min = -1;   // title clock re-evaluates its mode on the next tick
}

void ui_flip_weekly_face(void) {
    flip_weekly_face(FACE_HOLD_MS);
}

void ui_show_settings_page(int page) {
    show_settings_page(page);
    ui_show_screen(SCREEN_SETTINGS);
}

void ui_update_ble_status(ble_state_t state, const char* name, const char* mac) {
    (void)name; (void)mac;
    bool was_connected = s_ble_connected;
    s_ble_connected = (state == BLE_STATE_CONNECTED);

    if (s_ble_connected && !was_connected) connected_at_ms = lv_tick_get();
    // pair / idle / usage — picked from connection + data freshness.
    update_view_state();
}

void ui_update_battery(int percent, bool charging) {
    batt_pct_cached = percent;
    batt_charging_cached = charging;
    if (!battery_img) return;
    int idx;
    if (charging) {
        idx = 4;
    } else if (percent < 0) {
        idx = 0;
    } else if (percent <= 10) {
        idx = 0;
    } else if (percent <= 35) {
        idx = 1;
    } else if (percent <= 75) {
        idx = 2;
    } else {
        idx = 3;
    }
    lv_image_set_src(battery_img, &battery_dscs[idx]);
    apply_header_visibility();
}
