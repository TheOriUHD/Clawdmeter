"""Claude Code lifetime stats for the device's Stats page — the "st" payload key.

Claude Code writes every session to ~/.claude/projects/<cwd-slug>/<session>.jsonl
(subagents to agent-*.jsonl next to it). This module folds those transcripts
into the numbers the Claude app shows on its stats card — sessions, messages,
total tokens, active days, current and longest streak, peak hour, favourite
model — plus a GitHub-style activity heatmap of the last weeks.

The corpus can be hundreds of megabytes, so the scan is incremental: a small
cache remembers, per file, how far it read and what it found; only appended
bytes are parsed on the next pass (transcripts are append-only). Lines are
matched with regular expressions instead of json.loads — a tool result can be
a 100 KB line and we only need five fields.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import time
from pathlib import Path

CACHE_VERSION = 2      # bumped when the parse changes: the whole corpus is rescanned
HEAT_WEEKS = 24
MAX_LINE_SCAN = 4 * 1024 * 1024      # a line longer than this is skipped, not parsed

# Transcripts are compact JSON, but tolerate spaces after the colons anyway.
_TYPE_RE = re.compile(rb'"type"\s*:\s*"(user|assistant)"')
_TS_RE = re.compile(rb'"timestamp"\s*:\s*"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)')
_MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"]{1,64})"')
_USAGE_RES = [re.compile(rb'"%s"\s*:\s*(\d+)' % k) for k in
              (b"input_tokens", b"output_tokens", b"cache_creation_input_tokens", b"cache_read_input_tokens")]


def _empty_entry() -> dict:
    return {"size": 0, "mtime": 0.0, "off": 0, "msgs": 0, "user": 0, "tokens": 0,
            "days": {}, "hours": [0] * 24, "models": {}}


def _local_day_hour(y: int, mo: int, d: int, h: int, mi: int) -> tuple[str, int]:
    """UTC transcript timestamp → local calendar day ("YYYY-MM-DD") and local hour."""
    try:
        epoch = calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))
    except (OverflowError, ValueError):
        return "", -1
    lt = time.localtime(epoch)
    return f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}", lt.tm_hour


def model_family_label(model: str) -> str:
    """'claude-fable-5-1' → 'Fable 5' (family + major), 'claude-haiku-4-5-20251001' → 'Haiku 4.5'."""
    m = (model or "").strip()
    if m.startswith("claude-"):
        m = m[len("claude-"):]
    m = m.split("[", 1)[0]
    parts = [p for p in m.split("-") if p]
    if not parts:
        return ""
    name = parts[0].capitalize()
    nums = [p for p in parts[1:] if p.isdigit() and len(p) <= 2]
    if len(nums) >= 2 and int(nums[0]) < 5:          # the 4.x generation is known by both digits
        return f"{name} {nums[0]}.{nums[1]}"
    if nums:
        return f"{name} {nums[0]}"
    return name


def format_count(n: int) -> str:
    """32384 → '32,384'; the device shows up to six characters comfortably."""
    return f"{int(n):,}"


def format_tokens(n: int) -> str:
    n = int(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(n)


class ClaudeStats:
    def __init__(self, project_dirs: list[Path], cache_path: Path,
                 exclude_substrings: tuple[str, ...] = (), log=print) -> None:
        self.project_dirs = [Path(p) for p in project_dirs]
        self.cache_path = Path(cache_path)
        self.exclude = tuple(s for s in exclude_substrings if s)
        self.log = log
        self.files: dict[str, dict] = {}
        self._last_signature: tuple | None = None
        self._load()

    # ---- cache -----------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.cache_path.read_text())
            if isinstance(raw, dict) and raw.get("v") == CACHE_VERSION and isinstance(raw.get("files"), dict):
                self.files = raw["files"]
        except (OSError, ValueError):
            self.files = {}

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": CACHE_VERSION, "files": self.files}, separators=(",", ":")))
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    # ---- scanning ----------------------------------------------------------------
    def _candidate_files(self) -> list[Path]:
        out: list[Path] = []
        for root in self.project_dirs:
            try:
                dirs = [d for d in root.iterdir() if d.is_dir()]
            except OSError:
                continue
            for d in dirs:
                if any(s.lower() in d.name.lower() for s in self.exclude):
                    continue
                try:
                    out.extend(f for f in d.iterdir() if f.suffix == ".jsonl" and f.is_file())
                except OSError:
                    continue
        return out

    @staticmethod
    def _parse_chunk(entry: dict, data: bytes) -> None:
        for line in data.split(b"\n"):
            if not line or len(line) > MAX_LINE_SCAN:
                continue
            # The top-level "type" may sit after a huge "message" object (assistant
            # lines), so search the whole line: nested content blocks are
            # "text"/"tool_use"/"tool_result" and an escaped \"type\" never matches.
            m = _TYPE_RE.search(line)
            if not m:
                continue
            kind = m.group(1)
            entry["msgs"] += 1
            if kind == b"user":
                entry["user"] += 1
            ts = _TS_RE.search(line)
            if ts:
                day, hour = _local_day_hour(*(int(g) for g in ts.groups()))
                if day:
                    entry["days"][day] = entry["days"].get(day, 0) + 1
                    entry["hours"][hour] += 1
            if kind == b"assistant":
                mm = _MODEL_RE.search(line)
                if mm:
                    model = mm.group(1).decode("utf-8", "replace")
                    if model and not model.startswith("<"):
                        entry["models"][model] = entry["models"].get(model, 0) + 1
                for rx in _USAGE_RES:
                    um = rx.search(line)
                    if um:
                        entry["tokens"] += int(um.group(1))

    def refresh(self) -> bool:
        """Scan new transcript bytes. Returns True when the device summary changed."""
        seen: set[str] = set()
        t0 = time.time()
        read_bytes = 0
        for f in self._candidate_files():
            key = str(f)
            seen.add(key)
            try:
                st = f.stat()
            except OSError:
                continue
            entry = self.files.get(key)
            if entry is None or st.st_size < entry.get("off", 0):
                entry = _empty_entry()              # new, or truncated/rewritten: start over
                entry["agent"] = f.name.startswith("agent-")
            elif st.st_size == entry.get("size") and abs(st.st_mtime - entry.get("mtime", 0)) < 1e-6:
                continue                            # unchanged
            try:
                with open(f, "rb") as fh:
                    fh.seek(entry["off"])
                    data = fh.read()
            except OSError:
                continue
            # Only whole lines: keep an unfinished tail for the next pass.
            cut = data.rfind(b"\n")
            if cut < 0:
                continue
            self._parse_chunk(entry, data[:cut + 1])
            entry["off"] += cut + 1
            entry["size"] = st.st_size
            entry["mtime"] = st.st_mtime
            self.files[key] = entry
            read_bytes += cut + 1
        for key in list(self.files):
            if key not in seen:
                del self.files[key]
        if read_bytes:
            self._save()
            self.log(f"Stats: scanned {read_bytes / 1e6:.1f} MB of transcripts in {time.time() - t0:.1f}s")
        sig = self.signature()
        changed = sig != self._last_signature
        self._last_signature = sig
        return changed

    # ---- aggregation -------------------------------------------------------------
    def totals(self) -> dict:
        days: dict[str, int] = {}
        hours = [0] * 24
        models: dict[str, int] = {}
        msgs = tokens = sessions = 0
        for e in self.files.values():
            if e.get("user", 0) == 0 and e.get("msgs", 0) == 0:
                continue
            if not e.get("agent") and e.get("user", 0) > 0:
                sessions += 1
            msgs += e.get("msgs", 0)
            tokens += e.get("tokens", 0)
            for d, n in e.get("days", {}).items():
                days[d] = days.get(d, 0) + n
            for i, n in enumerate(e.get("hours", [])[:24]):
                hours[i] += n
            for m, n in e.get("models", {}).items():
                models[m] = models.get(m, 0) + n
        return {"sessions": sessions, "messages": msgs, "tokens": tokens, "days": days,
                "hours": hours, "models": models}

    @staticmethod
    def _streaks(days: dict[str, int], today: str) -> tuple[int, int]:
        """(current, longest) runs of consecutive active days; current may end yesterday."""
        import datetime as _dt
        active = sorted(_dt.date.fromisoformat(d) for d in days if days[d] > 0)
        if not active:
            return 0, 0
        longest = run = 1
        for a, b in zip(active, active[1:]):
            run = run + 1 if (b - a).days == 1 else 1
            longest = max(longest, run)
        t = _dt.date.fromisoformat(today)
        last = active[-1]
        if (t - last).days > 1:
            return 0, longest
        current = 1
        for a, b in zip(reversed(active[:-1]), reversed(active)):
            if (b - a).days == 1:
                current += 1
            else:
                break
        return current, longest

    def summary(self, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        t = self.totals()
        if not t["messages"]:
            return None
        lt = time.localtime(now)
        today = f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"
        cur, longest = self._streaks(t["days"], today)
        peak = max(range(24), key=lambda h: t["hours"][h]) if any(t["hours"]) else -1
        fav = max(t["models"], key=t["models"].get) if t["models"] else ""
        return {
            "sessions": t["sessions"], "messages": t["messages"], "tokens": t["tokens"],
            "active_days": sum(1 for n in t["days"].values() if n > 0),
            "streak": cur, "best_streak": longest, "peak_hour": peak,
            "model": model_family_label(fav), "days": t["days"], "today": today,
        }

    @staticmethod
    def heatmap(days: dict[str, int], today: str, weeks: int = HEAT_WEEKS) -> str:
        """One char per day, '0'..'4', columns of Sunday→Saturday weeks ending with
        the current week; days after today are 'x'."""
        import datetime as _dt
        t = _dt.date.fromisoformat(today)
        week_start = t - _dt.timedelta(days=(t.weekday() + 1) % 7)      # Sunday of this week
        start = week_start - _dt.timedelta(days=7 * (weeks - 1))
        window = [start + _dt.timedelta(days=i) for i in range(7 * weeks)]
        counts = [days.get(d.isoformat(), 0) for d in window]
        peak = max([c for d, c in zip(window, counts) if d <= t], default=0)
        out = []
        for d, c in zip(window, counts):
            if d > t:
                out.append("x")
            elif c <= 0 or peak <= 0:
                out.append("0")
            else:
                lvl = 1 + min(3, (4 * c - 1) * 1 // peak)     # quartiles of the busiest day
                out.append(str(max(1, min(4, lvl))))
        return "".join(out)

    def payload(self, now: float | None = None, weeks: int = HEAT_WEEKS) -> dict | None:
        s = self.summary(now)
        if s is None:
            return None
        return {"se": s["sessions"], "me": s["messages"], "tk": s["tokens"], "ad": s["active_days"],
                "cs": s["streak"], "ls": s["best_streak"], "ph": s["peak_hour"], "fm": s["model"],
                "hm": self.heatmap(s["days"], s["today"], weeks)}

    def signature(self) -> tuple | None:
        p = self.payload()
        return tuple(sorted(p.items())) if p else None
