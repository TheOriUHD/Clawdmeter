#!/usr/bin/env python3
"""Host battery fields ("hb"/"hc") for the device's header glyph.

Run: python -m pytest daemon/tests/test_host_battery.py -q
"""
from unittest.mock import patch

import daemon.claude_usage_daemon as mac
import daemon.claude_usage_daemon_windows as win


PMSET_DISCHARGING = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=12345)\t85%; discharging; 4:12 remaining present: true\n"
)
PMSET_CHARGING = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=12345)\t42%; charging; 1:03 remaining present: true\n"
)
PMSET_CHARGED = " -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n"
PMSET_AC_NOT_CHARGING = " -InternalBattery-0 (id=1)\t80%; AC attached; not charging present: true\n"
PMSET_FINISHING = " -InternalBattery-0 (id=1)\t97%; finishing charge; 0:05 remaining present: true\n"
PMSET_DESKTOP = "Now drawing from 'AC Power'\n"


def test_parse_pmset_states():
    assert mac.parse_pmset_batt(PMSET_DISCHARGING) == (85, False)
    assert mac.parse_pmset_batt(PMSET_CHARGING) == (42, True)
    assert mac.parse_pmset_batt(PMSET_CHARGED) == (100, False)
    assert mac.parse_pmset_batt(PMSET_AC_NOT_CHARGING) == (80, False)
    assert mac.parse_pmset_batt(PMSET_FINISHING) == (97, True)


def test_parse_pmset_no_battery_and_garbage():
    assert mac.parse_pmset_batt(PMSET_DESKTOP) is None
    assert mac.parse_pmset_batt("") is None
    assert mac.parse_pmset_batt(None) is None


def test_fields_added_when_available(tmp_path):
    payload = {"s": 1}
    with patch.object(mac, "read_host_battery", return_value=(85, True)), \
         patch.object(mac, "CONFIG_FILE", tmp_path / "missing"):
        mac.add_host_battery_fields(payload)
    assert payload["hb"] == 85 and payload["hc"] == 1


def test_fields_omitted_without_battery_or_when_off(tmp_path):
    payload = {"s": 1}
    with patch.object(mac, "read_host_battery", return_value=None), \
         patch.object(mac, "CONFIG_FILE", tmp_path / "missing"):
        mac.add_host_battery_fields(payload)
    assert "hb" not in payload and "hc" not in payload

    cfg = tmp_path / "config"
    cfg.write_text("host_battery = off\n")
    with patch.object(mac, "read_host_battery", return_value=(50, False)), \
         patch.object(mac, "CONFIG_FILE", cfg):
        mac.add_host_battery_fields(payload)
    assert "hb" not in payload


def test_host_battery_setting_default_on(tmp_path):
    with patch.object(mac, "CONFIG_FILE", tmp_path / "missing"):
        assert mac.read_host_battery_setting() == "on"


def test_windows_parse():
    assert win.parse_win32_battery("73 1\n") == (73, False)     # discharging
    assert win.parse_win32_battery("73 2\n") == (73, True)      # on AC
    assert win.parse_win32_battery("40 6\n") == (40, True)      # charging
    assert win.parse_win32_battery("") is None
    assert win.parse_win32_battery("x y") is None
