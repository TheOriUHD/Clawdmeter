#pragma once
// WiFi-link extras (WiFi build only — see link_wifi.cpp). The transport itself
// is reached through ble.h, which both links implement.
void link_wifi_set_credentials(const char* ssid, const char* pass);
bool link_wifi_has_credentials(void);
void link_wifi_status(void);
// Drop the stored network and reboot into the setup hotspot.
void link_wifi_forget_and_restart(void);
