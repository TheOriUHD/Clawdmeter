#ifdef CLAWD_LINK_WIFI
#include "setup_portal.h"

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

// The access point is WPA2, not open: the whole point of the portal is to carry
// the house WiFi key, and an open AP would put it on the air in the clear. The
// device has a screen, so showing an 8-character key costs the user one glance
// and buys real protection. Both name and key derive from the MAC, so they are
// stable - the same device always shows the same pair.
static char ap_ssid[24];
static char ap_pass[12];

static DNSServer dns;
static WebServer web(80);
static bool active = false;
static bool have_creds = false;
static String got_ssid, got_pass;

// A scan is taken once when the portal opens and refreshed on demand, so the
// page can offer a list instead of asking anyone to spell their SSID.
static int scan_count = 0;

static const char PAGE_HEAD[] PROGMEM =
    "<!doctype html><html><head><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Clawdmeter setup</title><style>"
    "*{box-sizing:border-box}"
    "body{margin:0;padding:32px 20px;background:#12110f;color:#f2ede4;"
    "font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    ".w{max-width:420px;margin:0 auto}"
    "h1{font-size:24px;margin:0 0 4px}"
    "p.sub{margin:0 0 28px;color:#8d867a}"
    "label{display:block;font-size:13px;text-transform:uppercase;letter-spacing:.08em;"
    "color:#8d867a;margin:0 0 8px}"
    "select,input{width:100%;padding:14px;margin:0 0 20px;border-radius:12px;"
    "border:1px solid #33302b;background:#1c1a17;color:#f2ede4;font-size:16px}"
    "button{width:100%;padding:16px;border:0;border-radius:12px;background:#d97757;"
    "color:#12110f;font-size:16px;font-weight:600}"
    "button:active{background:#c2664a}"
    ".note{margin-top:24px;color:#8d867a;font-size:13px}"
    "</style></head><body><div class=w>";

static String page_form(void) {
    String h = FPSTR(PAGE_HEAD);
    h += "<h1>Clawdmeter</h1><p class=sub>Pick the network it should join.</p>"
         "<form method=POST action=/save><label>Network</label><select name=ssid>";
    for (int i = 0; i < scan_count; i++) {
        String s = WiFi.SSID(i);
        if (s.length() == 0) continue;
        s.replace("<", "&lt;");
        h += "<option>" + s + "</option>";
    }
    if (scan_count <= 0) h += "<option>(no networks found)</option>";
    h += "</select><label>Password</label>"
         "<input name=pass type=password autocomplete=off placeholder='Network password'>"
         "<button type=submit>Join</button></form>"
         "<p class=note>The Clawdmeter stores this on the device and uses it only to "
         "join that network.</p></div></body></html>";
    return h;
}

static String page_saved(const String& ssid) {
    String h = FPSTR(PAGE_HEAD);
    h += "<h1>Joining " + ssid + "</h1>"
         "<p class=sub>You can close this and reconnect your phone to your own "
         "network. The Clawdmeter will show its progress on screen.</p>"
         "</div></body></html>";
    return h;
}

// Every phone OS probes a URL of its own to decide whether a network is "real".
// Answering all of them with the form is what makes the login sheet appear by
// itself instead of the user hunting for an address.
static void handle_captive(void) { web.send(200, "text/html", page_form()); }

static void handle_save(void) {
    got_ssid = web.arg("ssid");
    got_pass = web.arg("pass");
    if (got_ssid.length() == 0) { web.send(200, "text/html", page_form()); return; }
    web.send(200, "text/html", page_saved(got_ssid));
    WiFi.scanDelete();                        // the list has done its job
    scan_count = 0;
    Serial.printf("portal: '%s' submitted (%u-char key)\n",
                  got_ssid.c_str(), (unsigned)got_pass.length());   // never the key itself
    have_creds = true;
}

void portal_begin(void) {
    if (active) return;
    // The factory MAC out of efuse, not WiFi.macAddress(): the radio is not up
    // yet at this point and that call hands back all zeros, which would name
    // every device "Clawdmeter-0000" and give them all the same key.
    const uint64_t chip = ESP.getEfuseMac();
    const uint8_t b4 = (uint8_t)((chip >> 32) & 0xFF), b5 = (uint8_t)((chip >> 40) & 0xFF);
    snprintf(ap_ssid, sizeof(ap_ssid), "Clawdmeter-%02X%02X", b4, b5);
    // An alphabet without look-alike glyphs: read off a screen, typed on a phone.
    static const char AB[] = "abcdefghjkmnpqrstuvwxyz23456789";
    uint32_t seed = (uint32_t)(chip & 0xFFFFFFFF) ^ (uint32_t)(chip >> 32);
    for (int i = 0; i < 8; i++) { ap_pass[i] = AB[seed % (sizeof(AB) - 1)]; seed = seed / 7 + 2654435761u; }
    ap_pass[8] = '\0';

    WiFi.mode(WIFI_AP_STA);                 // AP for the phone, STA so we can scan
    WiFi.softAP(ap_ssid, ap_pass);
    delay(200);
    const IPAddress ip = WiFi.softAPIP();
    dns.start(53, "*", ip);                 // every lookup lands here: that is the portal
    scan_count = WiFi.scanNetworks();

    web.on("/", handle_captive);
    web.on("/save", HTTP_POST, handle_save);
    web.on("/hotspot-detect.html", handle_captive);   // iOS, macOS
    web.on("/generate_204", handle_captive);          // Android
    web.on("/gen_204", handle_captive);
    web.on("/ncsi.txt", handle_captive);              // Windows
    web.on("/connecttest.txt", handle_captive);
    web.on("/canonical.html", handle_captive);        // Firefox
    web.onNotFound(handle_captive);
    web.begin();

    active = true;
    have_creds = false;
    Serial.printf("portal: '%s' up on %s, key %s, %d network(s) in range\n",
                  ap_ssid, ip.toString().c_str(), ap_pass, scan_count);
}

void portal_tick(void) {
    if (!active) return;
    dns.processNextRequest();
    web.handleClient();
}

bool portal_active(void) { return active; }
bool portal_credentials_ready(void) { return have_creds; }
const char* portal_new_ssid(void) { return got_ssid.c_str(); }
const char* portal_new_pass(void) { return got_pass.c_str(); }
const char* portal_ap_ssid(void) { return ap_ssid; }
const char* portal_ap_pass(void) { return ap_pass; }
int portal_phones_connected(void) { return WiFi.softAPgetStationNum(); }

#endif  // CLAWD_LINK_WIFI
