#!/usr/bin/env python3
"""Trend history → the device's "tr" payload (24 hourly session maxima, 7 daily weekly-% use)."""
import json

import daemon.trend as trend

DAY = 86400
TZ = 0   # tests pin the timezone offset so day boundaries are deterministic


def test_hourly_and_daily_views(tmp_path):
    h = trend.History(tmp_path / "history.json")
    now = 10 * DAY + 13 * 3600 + 600            # 13:10 on day 10 (UTC)
    # Yesterday: weekly went 10 → 14 (+4), today 14 → 19 (+5) with one weekly reset dip ignored.
    h.add(now - DAY - 3600, 5, 10)
    h.add(now - DAY - 1800, 30, 14)
    h.add(now - 7200, 60, 14)                   # two hours ago, session max 60
    h.add(now - 3600, 20, 3)                    # weekly reset → drop, not negative use
    h.add(now - 60, 45, 8)                      # +5
    hourly = h.hourly_session(now)
    assert len(hourly) == 24 and hourly[-1] == 45 and hourly[-3] == 60 and hourly[0] == -1
    daily = h.daily_weekly_use(now, tz_offset_s=TZ)
    assert len(daily) == 7 and daily[-1] == 5 and daily[-2] == 4 and daily[0] == -1
    p = h.payload(now)
    assert p["h"] == hourly and p["d"] == daily


def test_persistence_and_pruning(tmp_path):
    path = tmp_path / "history.json"
    h = trend.History(path)
    t0 = 100 * DAY
    for i in range(0, 9 * DAY, 3600):          # nine days of hourly samples
        h.add(t0 + i, 1, 2)
    h.save()
    assert path.exists()
    h2 = trend.History(path)
    assert h2.samples[0][0] >= h2.samples[-1][0] - trend.KEEP_S   # pruned to the kept window
    assert len(h2.samples) < 9 * 24
    assert h2.samples == h.samples
    # Duplicate / backwards timestamps are ignored, bad values too.
    n = len(h2.samples)
    h2.add(h2.samples[-1][0], 5, 5)
    h2.add(h2.samples[-1][0] - 10, 5, 5)
    h2.add(h2.samples[-1][0] + 10, "x", 5)
    assert len(h2.samples) == n
    # A corrupt file loads as empty.
    path.write_text("{not json")
    assert trend.History(path).samples == [] and trend.History(path).payload() is None
    # Garbage rows are skipped.
    path.write_text(json.dumps({"samples": [[1, 2, 3], "bad", [4, "x", 6], [7, 8, 9]]}))
    assert trend.History(path).samples == [[1, 2, 3], [7, 8, 9]]


def test_daily_uses_local_midnight(tmp_path):
    h = trend.History(tmp_path / "h.json")
    now = 20 * DAY + 3600                        # 01:00 UTC
    h.add(now - 7200, 1, 10)                     # 23:00 UTC the previous day
    h.add(now - 600, 1, 15)                      # today UTC
    assert h.daily_weekly_use(now, tz_offset_s=0)[-1] == 5 and h.daily_weekly_use(now, tz_offset_s=0)[-2] == 0
    # In UTC+2 both samples fall on the same local day.
    d = h.daily_weekly_use(now, tz_offset_s=2 * 3600)
    assert d[-1] == 5 and d[-2] == -1
