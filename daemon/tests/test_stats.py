#!/usr/bin/env python3
"""Stats page data: incremental transcript scan → the "st" payload."""
import json
import time
from pathlib import Path

import daemon.stats as stats


def _line(kind, ts, model=None, usage=None, extra=""):
    d = {"parentUuid": None, "type": kind, "uuid": "u", "timestamp": ts, "sessionId": "s", "cwd": "/x"}
    msg = {"role": kind, "content": [{"type": "text", "text": "hello \"type\":\"user\" inside"}]}
    if model:
        msg["model"] = model
    if usage:
        msg["usage"] = usage
    d["message"] = msg
    return json.dumps(d) + "\n"


def _make_corpus(root: Path):
    proj = root / "projects" / "-Users-me-app"
    proj.mkdir(parents=True)
    s1 = proj / "aaaa.jsonl"
    s1.write_text(
        _line("user", "2026-09-01T12:00:00.000Z") +
        _line("assistant", "2026-09-01T12:00:05.000Z", "claude-fable-5-1", {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 100}) +
        '{"type":"attachment","timestamp":"2026-09-01T12:00:06.000Z"}\n' +
        _line("user", "2026-09-02T12:30:00.000Z") +
        _line("assistant", "2026-09-02T12:30:09.000Z", "claude-opus-5", {"input_tokens": 5, "output_tokens": 5})
    )
    (proj / "agent-bbbb.jsonl").write_text(
        _line("assistant", "2026-09-02T12:31:00.000Z", "claude-haiku-4-5-20251001", {"input_tokens": 1, "output_tokens": 1})
    )
    other = root / "projects" / "-Users-me--config-claude-usage-monitor"
    other.mkdir()
    (other / "keeper.jsonl").write_text(_line("user", "2026-09-03T12:00:00.000Z"))
    return s1


def test_scan_counts_and_incremental_append(tmp_path):
    s1 = _make_corpus(tmp_path)
    logs = []
    st = stats.ClaudeStats([tmp_path / "projects"], tmp_path / "cache.json",
                           exclude_substrings=("claude-usage-monitor",), log=logs.append)
    assert st.refresh() is True
    t = st.totals()
    assert t["sessions"] == 1                       # the agent file is not a session; the keeper dir is excluded
    assert t["messages"] == 5 and t["tokens"] == 10 + 20 + 1000 + 100 + 5 + 5 + 1 + 1
    assert t["models"] == {"claude-fable-5-1": 1, "claude-opus-5": 1, "claude-haiku-4-5-20251001": 1}
    # Unchanged files are skipped, appended lines are picked up from the saved offset.
    assert st.refresh() is False
    with open(s1, "a") as fh:
        fh.write(_line("user", "2026-09-04T12:00:00.000Z"))
        fh.write('{"type":"user","timestamp":"2026-09-04T12:00:01.000Z"')   # unfinished tail
    assert st.refresh() is True
    assert st.totals()["messages"] == 6
    # A fresh instance loads the cache and sees the same numbers without rescanning.
    st2 = stats.ClaudeStats([tmp_path / "projects"], tmp_path / "cache.json", exclude_substrings=("claude-usage-monitor",), log=logs.append)
    assert st2.totals()["messages"] == 6
    # The unfinished tail completes later.
    with open(s1, "a") as fh:
        fh.write(',"message":{"role":"user","content":"x"}}\n')
    st2.refresh()
    assert st2.totals()["messages"] == 7


def test_summary_streaks_peak_and_payload(tmp_path):
    _make_corpus(tmp_path)
    st = stats.ClaudeStats([tmp_path / "projects"], tmp_path / "cache.json", exclude_substrings=("claude-usage-monitor",), log=lambda *_: None)
    st.refresh()
    days = {"2026-09-01": 2, "2026-09-02": 3}
    assert stats.ClaudeStats._streaks(days, "2026-09-02") == (2, 2)
    assert stats.ClaudeStats._streaks(days, "2026-09-03") == (2, 2)      # yesterday still counts
    assert stats.ClaudeStats._streaks(days, "2026-09-05") == (0, 2)
    assert stats.ClaudeStats._streaks({"2026-08-01": 1, "2026-08-02": 1, "2026-08-04": 1}, "2026-08-04") == (1, 2)
    now = time.mktime((2026, 9, 2, 23, 0, 0, 0, 0, -1))
    s = st.summary(now)
    assert s["sessions"] == 1 and s["active_days"] == 2 and s["streak"] == 2 and s["best_streak"] == 2
    assert s["model"] in ("Fable 5", "Opus 5", "Haiku 4.5")
    p = st.payload(now, weeks=4)
    assert set(p) == {"se", "me", "tk", "ad", "cs", "ls", "ph", "fm", "hm"}
    assert len(p["hm"]) == 28 and set(p["hm"]) <= set("01234x")
    hm = stats.ClaudeStats.heatmap({"2026-09-02": 8, "2026-09-01": 2}, "2026-09-02", weeks=1)
    # Week of Sun 2026-08-30 … Sat 2026-09-05: Sun Mon Tue Wed Thu Fri Sat
    assert hm == "0014xxx"


def test_labels_and_formats():
    assert stats.model_family_label("claude-fable-5-1") == "Fable 5"
    assert stats.model_family_label("claude-opus-5") == "Opus 5"
    assert stats.model_family_label("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert stats.model_family_label("claude-sonnet-4-20250514") == "Sonnet 4"
    assert stats.format_count(32384) == "32,384"
    assert stats.format_tokens(35_200_000) == "35.2M" and stats.format_tokens(950) == "950" and stats.format_tokens(1_200) == "1.2K"
