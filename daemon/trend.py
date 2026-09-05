"""Usage history → the device's Trend page ("tr" payload key).

Every successful poll adds one sample (unix seconds, session %, weekly %). The
file keeps eight days so two views can be derived on demand:

    "h": 24 values — for each of the last 24 clock hours (oldest first, the
         current hour last) the highest session-window % seen in that hour.
         A line of these is "how loaded my 5-hour window was through the day".
    "d": 7 values — for each of the last 7 local days (oldest first, today
         last) how many weekly-quota percentage points were consumed that day:
         the sum of positive weekly-% deltas. Weekly resets (a drop) never count
         as negative use, so a bar is always the day's real consumption.

Values are integers 0-100; -1 marks an hour/day with no samples at all so the
device can leave a gap instead of drawing a false zero.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

KEEP_S = 8 * 86400
MAX_SAMPLES = 8 * 1440 + 60


class History:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.samples: list[list[int]] = []
        self._dirty = False
        self.load()

    # ---- persistence -----------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.samples = []
            return
        out = []
        for row in raw.get("samples", []) if isinstance(raw, dict) else []:
            if (isinstance(row, list) and len(row) == 3
                    and all(isinstance(v, (int, float)) for v in row)):
                out.append([int(row[0]), int(row[1]), int(row[2])])
        out.sort()
        self.samples = out

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": 1, "samples": self.samples}, separators=(",", ":")))
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass

    # ---- samples ---------------------------------------------------------------
    def add(self, ts: float, session_pct, weekly_pct) -> None:
        try:
            s = max(0, min(100, int(round(float(session_pct)))))
            w = max(0, min(100, int(round(float(weekly_pct)))))
        except (TypeError, ValueError):
            return
        t = int(ts)
        if self.samples and t <= self.samples[-1][0]:
            return                       # clock went backwards / duplicate poll
        self.samples.append([t, s, w])
        cutoff = t - KEEP_S
        if len(self.samples) > MAX_SAMPLES or self.samples[0][0] < cutoff:
            self.samples = [r for r in self.samples if r[0] >= cutoff][-MAX_SAMPLES:]
        self._dirty = True

    # ---- views -----------------------------------------------------------------
    def hourly_session(self, now: float) -> list[int]:
        """24 hourly maxima of the session %, oldest first, current hour last."""
        now = int(now)
        cur_hour = now - now % 3600
        start = cur_hour - 23 * 3600
        out = [-1] * 24
        for t, s, _w in self.samples:
            if t < start or t >= cur_hour + 3600:
                continue
            i = (t - start) // 3600
            if 0 <= i < 24 and s > out[i]:
                out[i] = s
        return out

    def daily_weekly_use(self, now: float, tz_offset_s: int | None = None) -> list[int]:
        """7 daily weekly-% consumption values (local days), oldest first, today last."""
        if tz_offset_s is None:
            tz_offset_s = time.localtime(now).tm_gmtoff
        local_now = int(now) + tz_offset_s
        today0 = local_now - local_now % 86400          # local midnight, as shifted epoch
        start = today0 - 6 * 86400
        out = [-1] * 7
        prev_w: int | None = None
        for t, _s, w in self.samples:
            lt = t + tz_offset_s
            if lt < start - 3600:                         # need one sample before the window as a baseline
                prev_w = w
                continue
            if lt < start:
                prev_w = w
                continue
            d = (lt - start) // 86400
            if d > 6:
                break
            if out[d] < 0:
                out[d] = 0
            if prev_w is not None and w > prev_w:
                out[d] = min(100, out[d] + (w - prev_w))
            prev_w = w
        return out

    def payload(self, now: float | None = None) -> dict | None:
        """The "tr" object, or None when there is nothing to draw yet."""
        if not self.samples:
            return None
        now = time.time() if now is None else now
        return {"h": self.hourly_session(now), "d": self.daily_weekly_use(now)}
