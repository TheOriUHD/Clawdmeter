#pragma once
// WiFi setup portal — the "hotel login" flow (WiFi build only).
//
// With no network stored, the device raises its own access point. A phone that
// joins it gets the captive-portal sheet automatically, picks the house network
// from a scanned list and types its key. Nothing is typed on the device and no
// serial console is needed.
#include <stdbool.h>

void portal_begin(void);          // raise the access point, DNS and web server
void portal_tick(void);           // pump both servers; call from the main loop
bool portal_active(void);
bool portal_credentials_ready(void);   // a phone submitted a network
const char* portal_new_ssid(void);
const char* portal_new_pass(void);
const char* portal_ap_ssid(void);      // the open network the device tells you to join
int  portal_phones_connected(void);
