#!/usr/bin/env python3
"""Tests for the official OAuth usage endpoint path (macOS daemon).

The endpoint is the primary usage source: token-free and the only place the
per-model weekly limits ("ws", e.g. Fable) come from. Every failure must fall
back to the rate-limit-header method, a 429 must bench the endpoint, and the
wire format must fit the firmware's 512-byte BLE buffer.

Run: python -m pytest daemon/tests/test_usage_endpoint.py -x -q
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

import daemon.claude_usage_daemon as d

NOW = 1_800_000_000.0   # fixed "now" for deterministic reset math


def _iso(seconds_from_now: float) -> str:
    import datetime
    t = datetime.datetime.fromtimestamp(NOW + seconds_from_now, tz=datetime.timezone.utc)
    return t.isoformat()


def _pro_body(fable: int | None = 4, extra_limits=()):
    """A Pro/Max-shaped response like the live endpoint returns."""
    limits = [
        {"kind": "session", "group": "session", "percent": 12, "severity": "normal",
         "resets_at": _iso(2 * 3600), "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 33, "severity": "normal",
         "resets_at": _iso(3 * 86400), "scope": None, "is_active": False},
    ]
    if fable is not None:
        limits.append({"kind": "weekly_scoped", "group": "weekly", "percent": fable,
                       "severity": "normal", "resets_at": _iso(3 * 86400),
                       "scope": {"model": {"id": None, "display_name": "Fable"},
                                 "surface": None},
                       "is_active": True})
    limits.extend(extra_limits)
    return {
        "five_hour": {"utilization": 12.4, "resets_at": _iso(2 * 3600)},
        "seven_day": {"utilization": 33.0, "resets_at": _iso(3 * 86400)},
        "seven_day_opus": None,
        "limits": limits,
    }


# --- pure parsing -------------------------------------------------------------

def test_parse_pro_with_fable_row():
    p = d.parse_usage_response(_pro_body(fable=4), NOW)
    assert p["s"] == 12 and p["w"] == 33
    assert p["sr"] == 120                # 2h → minutes
    assert p["wr"] == 3 * 24 * 60
    assert p["acct"] == "pro" and p["ok"] is True and p["st"] == "allowed"
    assert p["ws"] == [{"n": "Fable", "p": 4}]


def test_parse_omits_ws_when_no_scoped_limit():
    p = d.parse_usage_response(_pro_body(fable=None), NOW)
    assert "ws" not in p


def test_scoped_zero_percent_is_a_real_reading():
    p = d.parse_usage_response(_pro_body(fable=0), NOW)
    assert p["ws"] == [{"n": "Fable", "p": 0}]


def test_parse_status_rejected_at_100():
    body = _pro_body()
    body["five_hour"]["utilization"] = 100.0
    assert d.parse_usage_response(body, NOW)["st"] == "rejected"


def test_parse_enterprise_shape_returns_none():
    """No weekly window → not Pro/Max → header method stays the authority."""
    assert d.parse_usage_response({"five_hour": {"utilization": 40.0}}, NOW) is None
    assert d.parse_usage_response({"seven_day": None, "five_hour": None}, NOW) is None
    assert d.parse_usage_response([], NOW) is None
    assert d.parse_usage_response({"five_hour": {"utilization": "x"}, "seven_day": {}}, NOW) is None


def test_scoped_label_falls_back_to_model_id_and_truncates():
    extra = [{"kind": "weekly_scoped", "percent": 55,
              "scope": {"model": {"id": "claude-hypothetical-model-with-a-long-name",
                                  "display_name": None}}}]
    ws = d.scoped_weekly_limits(_pro_body(fable=None, extra_limits=extra)["limits"])
    assert ws == [{"n": "claude-hypothet", "p": 55}]
    assert len(ws[0]["n"]) == d.SCOPED_NAME_MAX


def test_scoped_ignores_malformed_entries():
    limits = [
        {"kind": "weekly_scoped", "percent": 10, "scope": None},                 # no scope
        {"kind": "weekly_scoped", "percent": 10, "scope": {"model": None}},      # no model
        {"kind": "weekly_scoped", "percent": "n/a", "scope": {"model": {"display_name": "X"}}},
        {"kind": "weekly_all", "percent": 10, "scope": {"model": {"display_name": "Y"}}},
        "garbage",
    ]
    assert d.scoped_weekly_limits(limits) == []
    assert d.scoped_weekly_limits(None) == []


def test_scoped_percent_is_clamped():
    limits = [{"kind": "weekly_scoped", "percent": 140.7,
               "scope": {"model": {"display_name": "Fable"}}}]
    assert d.scoped_weekly_limits(limits) == [{"n": "Fable", "p": 100}]


def test_iso_reset_minutes_handles_z_suffix_naive_and_garbage():
    assert d._iso_reset_minutes("2028-01-01T00:00:00Z", NOW) > 0
    assert d._iso_reset_minutes("2028-01-01T00:00:00", NOW) > 0        # naive → UTC
    assert d._iso_reset_minutes("1999-01-01T00:00:00+00:00", NOW) == 0  # in the past
    assert d._iso_reset_minutes("not a date", NOW) == 0
    assert d._iso_reset_minutes(None, NOW) == 0
    assert d._iso_reset_minutes(12345, NOW) == 0


def test_wire_size_fits_ble_buffer_with_max_scoped_rows():
    extra = [{"kind": "weekly_scoped", "percent": 100,
              "scope": {"model": {"display_name": "M" * 40}}} for _ in range(3)]
    p = d.parse_usage_response(_pro_body(fable=100, extra_limits=extra), NOW)
    p["c"] = 1
    p["t"] = 1785474000
    p["tf"] = 24
    wire = json.dumps(p, separators=(",", ":")).encode()
    assert len(p["ws"]) == 4
    assert len(wire) < 512, len(wire)


# --- network path: fallback + cooldown -----------------------------------------

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    """Stands in for httpx.AsyncClient; records every GET."""
    calls: list = []
    resp = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        _FakeClient.calls.append((url, headers))
        if isinstance(_FakeClient.resp, Exception):
            raise _FakeClient.resp
        return _FakeClient.resp


@pytest.fixture(autouse=True)
def _reset_endpoint_state():
    d._usage_endpoint_cooldown_until = 0.0
    d._usage_source = None
    _FakeClient.calls = []
    _FakeClient.resp = None
    yield


def _poll(token="tok"):
    with patch.object(d.httpx, "AsyncClient", _FakeClient), \
         patch.object(d, "add_chime_field"), patch.object(d, "add_clock_fields"):
        return asyncio.run(d.poll_usage_endpoint(token))


def test_endpoint_success_builds_payload_and_sends_bearer():
    _FakeClient.resp = _Resp(200, _pro_body())
    p = _poll("secret-token")
    assert p["ws"] == [{"n": "Fable", "p": 4}]
    url, headers = _FakeClient.calls[0]
    assert url == d.USAGE_URL
    assert headers["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_endpoint_non_200_returns_none_without_raising(status):
    _FakeClient.resp = _Resp(status, text="nope")
    assert _poll() is None


def test_endpoint_network_error_returns_none():
    _FakeClient.resp = d.httpx.ConnectError("boom")
    assert _poll() is None


def test_endpoint_bad_json_returns_none():
    _FakeClient.resp = _Resp(200, ValueError("bad json"))
    assert _poll() is None


def test_endpoint_enterprise_shape_returns_none_for_header_fallback():
    _FakeClient.resp = _Resp(200, {"five_hour": {"utilization": 30.0}})
    assert _poll() is None


def test_429_benches_endpoint_and_skips_network_during_cooldown():
    _FakeClient.resp = _Resp(429, text="slow down")
    assert _poll() is None
    assert d._usage_endpoint_cooldown_until > 0
    n = len(_FakeClient.calls)
    _FakeClient.resp = _Resp(200, _pro_body())
    assert _poll() is None                      # still benched → no request made
    assert len(_FakeClient.calls) == n
    d._usage_endpoint_cooldown_until = 0.0      # bench over
    assert _poll() is not None
    assert len(_FakeClient.calls) == n + 1


def test_poll_active_prefers_endpoint_then_falls_back_to_headers():
    with patch.object(d, "read_config_dirs", return_value=[d.DEFAULT_CONFIG_DIR]), \
         patch.object(d, "read_token_for", return_value="tok"), \
         patch.object(d, "poll_usage_endpoint", AsyncMock(return_value={"s": 5, "ok": True})) as ep, \
         patch.object(d, "poll_api", AsyncMock(return_value={"s": 9, "ok": True})) as api:
        payload, dead = asyncio.run(d.poll_active(d.PlanSelector()))
    assert payload["s"] == 5 and dead is False
    ep.assert_awaited_once()
    api.assert_not_awaited()

    with patch.object(d, "read_config_dirs", return_value=[d.DEFAULT_CONFIG_DIR]), \
         patch.object(d, "read_token_for", return_value="tok"), \
         patch.object(d, "poll_usage_endpoint", AsyncMock(return_value=None)), \
         patch.object(d, "poll_api", AsyncMock(return_value={"s": 9, "ok": True})) as api:
        payload, dead = asyncio.run(d.poll_active(d.PlanSelector()))
    assert payload["s"] == 9 and dead is False
    api.assert_awaited_once()


def test_poll_active_dead_token_still_flows_through_header_authority():
    """The endpoint never declares a token dead; poll_api's TokenExpired does."""
    with patch.object(d, "read_config_dirs", return_value=[d.DEFAULT_CONFIG_DIR]), \
         patch.object(d, "read_token_for", return_value="tok"), \
         patch.object(d, "poll_usage_endpoint", AsyncMock(return_value=None)), \
         patch.object(d, "poll_api", AsyncMock(side_effect=d.TokenExpired())):
        payload, dead = asyncio.run(d.poll_active(d.PlanSelector()))
    assert payload is None and dead is True


def test_clock_default_is_auto_so_device_controls_display(tmp_path):
    with patch.object(d, "CONFIG_FILE", tmp_path / "missing"):
        assert d.read_clock_setting() == "auto"
    cfg = tmp_path / "config"
    cfg.write_text("clock = off\n")
    with patch.object(d, "CONFIG_FILE", cfg):
        assert d.read_clock_setting() == "off"
