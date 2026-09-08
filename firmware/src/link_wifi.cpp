#ifdef CLAWD_LINK_WIFI
// WiFi transport — the second of the device's two modes.
//
// It implements the very same contract ble.cpp does (ble.h: a link state and a
// stream of JSON payloads), so main.cpp, the parser, the UI and the alerts have
// no idea which radio delivered a payload. Only the build differs: the two
// stacks do not fit together on this part (see ble_stub.cpp), so a Clawdmeter is
// flashed as a BLE device or as a WiFi device.
//
// Where BLE tied the device to one nearby machine, this joins the home network
// and talks to the hub (daemon/hub.py) on whatever always-on box runs it. The
// hub is found by mDNS, so nothing is configured but the WiFi credentials, and
// any number of Clawdmeters can watch the same hub.
//
// The HTTP long-poll lives on its own FreeRTOS task: it parks for up to ~25 s
// waiting for the hub to have news, which the LVGL loop could never afford to
// do. The task hands finished payloads over under a mutex, exactly as NimBLE's
// host task hands over notifications, and ble_tick() consumes them.
#include "ble.h"

#include <Arduino.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>

#define LINK_BUF_SIZE   1600      // hub trims payloads to 1400 B; leave headroom
#define PREFS_NS        "clawdwifi"
#define KEY_SSID        "ssid"
#define KEY_PASS        "pass"
#define HUB_SERVICE     "clawdmeter"
#define POLL_TIMEOUT_MS 30000     // must outlast the hub's ~25 s hold
#define RETRY_MS        3000

static char        rx_buf[LINK_BUF_SIZE];
static char        pending[LINK_BUF_SIZE];
static volatile bool pending_ready = false;
static SemaphoreHandle_t rx_lock = nullptr;

static volatile ble_state_t link_state = BLE_STATE_INIT;
static char  hub_host[64] = "";
static uint16_t hub_port = 0;
static uint32_t hub_seq = 0;
static char  ip_str[20] = "0.0.0.0";

// ---- credentials -------------------------------------------------------------
// Typed by the user (serial `wifi <ssid> <password>`) and kept in NVS. They are
// never transmitted anywhere but to the access point itself.
static String cred_ssid, cred_pass;

static void load_credentials(void) {
    Preferences p;
    if (!p.begin(PREFS_NS, true)) return;
    cred_ssid = p.getString(KEY_SSID, "");
    cred_pass = p.getString(KEY_PASS, "");
    p.end();
}

void link_wifi_set_credentials(const char* ssid, const char* pass) {
    Preferences p;
    if (!p.begin(PREFS_NS, false)) return;
    p.putString(KEY_SSID, ssid);
    p.putString(KEY_PASS, pass);
    p.end();
    Serial.printf("wifi: saved SSID '%s' (%u-char key) - reboot to apply\n",
                  ssid, (unsigned)strlen(pass));
}

bool link_wifi_has_credentials(void) {
    load_credentials();
    return cred_ssid.length() > 0;
}

void link_wifi_status(void) {
    Serial.printf("wifi: ssid='%s' state=%d ip=%s hub=%s:%u seq=%lu rssi=%d\n",
                  cred_ssid.c_str(), (int)link_state, ip_str,
                  hub_host[0] ? hub_host : "(searching)", (unsigned)hub_port,
                  (unsigned long)hub_seq, (int)WiFi.RSSI());
}

// ---- discovery ---------------------------------------------------------------
// The hub advertises _clawdmeter._tcp; the first responder wins. Re-queried
// whenever a poll fails, so moving the hub to another box needs no action here.
static bool discover_hub(void) {
    const int n = MDNS.queryService(HUB_SERVICE, "tcp");
    if (n <= 0) return false;
    IPAddress addr = MDNS.address(0);
    snprintf(hub_host, sizeof(hub_host), "%s", addr.toString().c_str());
    hub_port = MDNS.port(0);
    Serial.printf("wifi: hub found at %s:%u\n", hub_host, (unsigned)hub_port);
    return true;
}

// ---- the poll task -----------------------------------------------------------
static void publish(const String& body) {
    if (body.length() == 0 || body.length() >= LINK_BUF_SIZE) return;
    if (xSemaphoreTake(rx_lock, portMAX_DELAY) == pdTRUE) {
        memcpy(pending, body.c_str(), body.length() + 1);
        pending_ready = true;
        xSemaphoreGive(rx_lock);
    }
}

static void poll_task_fn(void*) {
    for (;;) {
        if (WiFi.status() != WL_CONNECTED) {
            link_state = BLE_STATE_DISCONNECTED;
            hub_host[0] = '\0';
            WiFi.begin(cred_ssid.c_str(), cred_pass.c_str());
            for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) vTaskDelay(pdMS_TO_TICKS(250));
            if (WiFi.status() != WL_CONNECTED) { vTaskDelay(pdMS_TO_TICKS(RETRY_MS)); continue; }
            snprintf(ip_str, sizeof(ip_str), "%s", WiFi.localIP().toString().c_str());
            Serial.printf("wifi: joined '%s' as %s\n", cred_ssid.c_str(), ip_str);
            MDNS.begin("clawdmeter");
        }
        if (!hub_host[0]) {
            link_state = BLE_STATE_ADVERTISING;          // "looking for a peer"
            if (!discover_hub()) { vTaskDelay(pdMS_TO_TICKS(RETRY_MS)); continue; }
        }

        HTTPClient http;
        char url[128];
        snprintf(url, sizeof(url), "http://%s:%u/device/poll?seq=%lu&id=%s",
                 hub_host, (unsigned)hub_port, (unsigned long)hub_seq, ip_str);
        http.setTimeout(POLL_TIMEOUT_MS);
        http.setConnectTimeout(5000);
        if (!http.begin(url)) { http.end(); hub_host[0] = '\0'; vTaskDelay(pdMS_TO_TICKS(RETRY_MS)); continue; }
        const char* keep[] = {"X-Clawdmeter-Seq"};
        http.collectHeaders(keep, 1);
        const int code = http.GET();
        if (code == 200) {
            link_state = BLE_STATE_CONNECTED;
            hub_seq = (uint32_t)http.header("X-Clawdmeter-Seq").toInt();
            publish(http.getString());
        } else if (code == 204) {
            link_state = BLE_STATE_CONNECTED;            // held, nothing new: healthy
        } else {
            hub_host[0] = '\0';                          // hub moved or went away
            link_state = BLE_STATE_ADVERTISING;
            vTaskDelay(pdMS_TO_TICKS(RETRY_MS));
        }
        http.end();
        vTaskDelay(pdMS_TO_TICKS(20));                   // never spin the CPU away from LVGL
    }
}

// ---- the ble.h contract ------------------------------------------------------
void ble_init(void) {
    rx_lock = xSemaphoreCreateMutex();
    rx_buf[0] = pending[0] = '\0';
    load_credentials();
    if (cred_ssid.length() == 0) {
        Serial.println("wifi: no credentials - set them with: wifi <ssid> <password>");
        link_state = BLE_STATE_DISCONNECTED;
        return;
    }
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(true);                                  // the panel needs the power more
    link_state = BLE_STATE_ADVERTISING;
    xTaskCreate(poll_task_fn, "clawdpoll", 6144, nullptr, 4, nullptr);
}

void ble_tick(void) {}

bool ble_has_data(void) {
    if (!pending_ready) return false;
    if (xSemaphoreTake(rx_lock, 0) != pdTRUE) return false;
    memcpy(rx_buf, pending, LINK_BUF_SIZE);
    pending_ready = false;
    xSemaphoreGive(rx_lock);
    return true;
}

const char* ble_get_data(void) { return rx_buf; }
ble_state_t ble_get_state(void) { return link_state; }
const char* ble_get_device_name(void) { return "Clawdmeter (WiFi)"; }
const char* ble_get_mac_address(void) { return ip_str; }   // the useful address here

// No pairing, no bonds, no HID over this transport.
void ble_clear_bonds(void) {}
bool ble_has_bonds(void) { return false; }
void ble_send_ack(void) {}
void ble_send_nack(void) {}
void ble_request_refresh(void) { hub_seq = 0; }            // ask for a fresh payload
void ble_set_battery_level(int pct) { (void)pct; }
void ble_keyboard_press(uint8_t key, uint8_t modifier) { (void)key; (void)modifier; }
void ble_keyboard_release(void) {}

#endif  // CLAWD_LINK_WIFI
