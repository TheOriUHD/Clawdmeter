#!/usr/bin/env python3
"""Companion: Claude Code hook events → the device's "cc" state.

Run: python -m pytest daemon/tests/test_companion.py -q
"""
import asyncio
import json
import socket

import pytest

import daemon.companion as cc
import daemon.claude_usage_daemon as mac


def ev(name, sid="s1", **extra):
    e = {"session_id": sid, "hook_event_name": name, "cwd": "/Users/me/dev/Clawdmeter",
         "transcript_path": "/tmp/t.jsonl"}
    e.update(extra)
    return e


def test_turn_lifecycle_and_labels():
    c = cc.Companion()
    t = 1000.0
    assert c.ingest(ev("SessionStart", start_type="startup", model="claude-fable-5-1[1m]"), now=t)
    s = c.summary(now=t)
    assert s["s"] == cc.CC_IDLE and s["n"] == 1 and s["p"] == "Clawdmeter" and s["m"] == "Fable 5.1"

    c.ingest(ev("UserPromptSubmit", prompt="fix it"), now=t + 1)
    assert c.summary(now=t + 1)["s"] == cc.CC_THINKING

    c.ingest(ev("PreToolUse", tool_name="Edit", tool_input={"file_path": "/x/y/ui.cpp"}), now=t + 2)
    s = c.summary(now=t + 5)
    assert s["s"] == cc.CC_TOOL and s["l"] == "Editing ui.cpp" and s["e"] == 3

    c.ingest(ev("PostToolUse", tool_name="Edit", tool_input={}, tool_response="ok"), now=t + 6)
    assert c.summary(now=t + 6)["s"] == cc.CC_THINKING

    c.ingest(ev("PreToolUse", tool_name="Bash", tool_input={"command": "pio run -d firmware -e sim\necho done"}), now=t + 7)
    lbl = c.summary(now=t + 7)["l"]
    assert lbl.startswith("Bash: pio run -d") and lbl.endswith("...") and len(lbl) <= cc.LABEL_MAX

    # A long turn ends in the nudge-worthy state; a short one stays quiet.
    c.ingest(ev("Stop", stop_reason="end_turn"), now=t + 40)
    assert c.summary(now=t + 40)["s"] == cc.CC_TURN_DONE and c.summary(now=t + 40)["l"].isascii()
    c.ingest(ev("UserPromptSubmit", prompt="thanks"), now=t + 41)
    c.ingest(ev("Stop"), now=t + 45)
    assert c.summary(now=t + 45)["s"] == cc.CC_DONE

    c.ingest(ev("SessionEnd", end_reason="prompt_input_exit"), now=t + 50)
    assert c.summary(now=t + 50) == {"n": 0, "a": 0, "s": cc.CC_NONE} or c.summary(now=t + 50) is None


def test_attention_states_and_priority():
    c = cc.Companion()
    t = 0.0
    c.ingest(ev("UserPromptSubmit", sid="a"), now=t)
    c.ingest(ev("UserPromptSubmit", sid="b"), now=t)
    c.ingest(ev("PreToolUse", sid="a", tool_name="Read", tool_input={"file_path": "a.py"}), now=t + 1)
    c.ingest(ev("PermissionRequest", sid="b", tool_name="Bash", tool_input={"command": "rm -rf build"}), now=t + 2)
    s = c.summary(now=t + 3)
    assert s["n"] == 2 and s["a"] == 1
    assert s["s"] == cc.CC_ATTENTION and s["l"] == "Permission: Bash"      # attention beats a busy session
    # Granting the permission → the tool runs → attention clears.
    c.ingest(ev("PreToolUse", sid="b", tool_name="Bash", tool_input={"command": "rm -rf build"}), now=t + 4)
    s = c.summary(now=t + 4)
    assert s["a"] == 0 and s["s"] == cc.CC_TOOL
    # Questions and plan approvals are attention too.
    c.ingest(ev("PreToolUse", sid="a", tool_name="AskUserQuestion", tool_input={}), now=t + 5)
    assert c.summary(now=t + 5)["l"] == "Question for you"
    c.ingest(ev("Notification", sid="a", notification_type="permission_prompt", message="x"), now=t + 6)
    assert c.summary(now=t + 6)["s"] == cc.CC_ATTENTION


def test_subagents_do_not_drive_headline():
    c = cc.Companion()
    c.ingest(ev("UserPromptSubmit"), now=0)
    c.ingest(ev("PreToolUse", tool_name="Agent", tool_input={"description": "Explore repo"}), now=1)
    assert c.summary(now=1)["l"] == "Agent: Explore repo"
    c.ingest(ev("SubagentStart", agent_id="sub1", agent_type="Explore"), now=2)
    c.ingest(ev("PreToolUse", agent_id="sub1", tool_name="Grep", tool_input={"pattern": "foo"}), now=3)
    s = c.summary(now=3)
    assert s["l"] == "Agent: Explore repo" and s["g"] == 1
    c.ingest(ev("SubagentStop", agent_id="sub1"), now=4)
    assert "g" not in c.summary(now=4)


def test_error_compaction_and_expiry():
    c = cc.Companion()
    c.ingest(ev("UserPromptSubmit"), now=0)
    c.ingest(ev("PreCompact", trigger="auto"), now=1)
    assert c.summary(now=1)["s"] == cc.CC_COMPACTING
    c.ingest(ev("PostCompact"), now=2)
    assert c.summary(now=2)["s"] == cc.CC_THINKING
    c.ingest(ev("StopFailure"), now=3)
    assert c.summary(now=3)["s"] == cc.CC_ERROR
    # Nothing heard for a long time → the session is dropped.
    assert c.expire(now=3 + cc.ACTIVE_STALE_S + 1)
    assert c.summary(now=10 ** 6)["s"] == cc.CC_NONE if c.enabled else True


def test_signature_ignores_elapsed_and_changed_flag():
    c = cc.Companion()
    assert c.ingest(ev("UserPromptSubmit"), now=0)
    sig = c.signature()
    assert c.summary(now=100)["e"] == 100
    assert c.signature() == sig
    assert not c.ingest(ev("Notification", notification_type="auth_success"), now=5)   # nothing visible changed
    assert c.ingest(ev("Stop"), now=6)


def test_model_and_tool_labels():
    assert cc.model_label("claude-fable-5-1") == "Fable 5.1"
    assert cc.model_label("claude-opus-5") == "Opus 5"
    assert cc.model_label("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert cc.model_label("") == ""
    assert cc.tool_label("mcp__notion__search", {}) == "Using notion"
    assert cc.tool_label("WebSearch", {"query": "x"}) == "Browsing the web"
    assert cc.tool_label("Grep", {"pattern": "needle"}) == "Searching needle"
    assert len(cc.tool_label("Write", {"file_path": "/a/" + "x" * 80 + ".py"})) <= cc.LABEL_MAX


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_listener_end_to_end():
    async def run():
        c = cc.Companion()
        changes = []
        port = _free_port()
        server = await cc.start_companion_server(c, lambda: changes.append(1), "127.0.0.1", port, log=lambda *_: None)
        assert server is not None and c.enabled
        assert c.summary() == {"n": 0, "a": 0, "s": cc.CC_NONE}

        async def post(path, body, host_hdr=None):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            data = json.dumps(body).encode()
            hdrs = f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            if host_hdr:
                hdrs += f"X-Clawdmeter-Host: {host_hdr}\r\n"
            w.write(hdrs.encode() + b"\r\n" + data)
            await w.drain()
            resp = await r.read()
            w.close()
            return resp

        resp = await post("/hook", ev("UserPromptSubmit"), host_hdr="devbox")
        assert resp.startswith(b"HTTP/1.1 200")
        assert changes and c.summary()["s"] == cc.CC_THINKING and c.summary()["h"] == "devbox"
        # Our own hostname is not "remote".
        await post("/hook", ev("PreToolUse", sid="s2", tool_name="Read", tool_input={}), host_hdr=cc.LOCAL_HOST)
        assert "h" not in {k: v for k, v in c.summary().items()} or c.headline().sid == "s1"
        assert (await post("/hook", "garbage")).startswith(b"HTTP/1.1 200")      # non-object JSON is ignored, still 200
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET /state HTTP/1.1\r\nHost: x\r\n\r\n")
        await w.drain()
        body = (await r.read()).split(b"\r\n\r\n", 1)[1]
        w.close()
        assert json.loads(body)["n"] == 2
        server.close()
        await server.wait_closed()
    asyncio.run(run())


def test_daemon_payload_helpers(tmp_path):
    hist = mac.trend_mod.History(tmp_path / "history.json")
    old_hist, old_comp = mac.HISTORY, mac.COMPANION
    try:
        mac.HISTORY = hist
        mac.COMPANION = cc.Companion()
        mac.COMPANION.enabled = True
        payload = {"s": 40, "sr": 10, "w": 12, "wr": 500, "st": "allowed", "acct": "pro", "ok": True}
        out = mac.finalize_payload(dict(payload))
        assert out["cc"] == {"n": 0, "a": 0, "s": 0}
        assert "tr" not in out and hist.samples and hist.samples[-1][1] == 40    # recorded, not sent
        # A beat before any usage data is companion-only; after, it re-stamps the last payload.
        mac.COMPANION.ingest(ev("UserPromptSubmit"))
        beat = mac.companion_beat(None)
        assert set(beat) == {"cc"} and beat["cc"]["s"] == cc.CC_THINKING
        beat2 = mac.companion_beat(out)
        assert beat2["s"] == 40 and beat2["cc"]["s"] == cc.CC_THINKING
        # Oversized payloads shed the trend first.
        big = dict(out)
        big["tr"] = {"h": [100] * 24, "d": [100] * 7, "pad": "x" * 600}
        data = mac.shrink_payload(big)
        assert len(data) <= mac.BLE_PAYLOAD_MAX and b'"tr"' not in data and b'"cc"' in data
    finally:
        mac.HISTORY, mac.COMPANION = old_hist, old_comp


def test_config_readers(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("companion = off\ncompanion_port = 5000\ncompanion_bind = 0.0.0.0\ntrend = off\n")
    from unittest.mock import patch
    with patch.object(mac, "CONFIG_FILE", cfg):
        assert mac.read_companion_setting() == "off"
        assert mac.read_companion_port() == 5000
        assert mac.read_companion_bind() == "0.0.0.0"
        assert mac.read_trend_setting() == "off"
    with patch.object(mac, "CONFIG_FILE", tmp_path / "missing"):
        assert mac.read_companion_setting() == "on"
        assert mac.read_companion_port() == cc.COMPANION_PORT
        assert mac.read_trend_setting() == "on"
