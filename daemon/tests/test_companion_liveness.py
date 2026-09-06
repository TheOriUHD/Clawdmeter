#!/usr/bin/env python3
"""Companion liveness + persistence: a local session lives as long as its Claude
Code process; the table survives a daemon restart; the hook reports its parent PID."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import daemon.companion as cc
import daemon.claude_usage_daemon as mac
import daemon.claude_usage_daemon_windows as win


def ev(name, sid="s1", **extra):
    e = {"session_id": sid, "hook_event_name": name, "cwd": "/Users/me/dev/Clawdmeter"}
    e.update(extra)
    return e


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _http(port, method, path, body=None, headers=None):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    req = f"{method} {path} HTTP/1.1\r\nHost: bridge.local:1\r\nContent-Length: {len(data)}\r\n"
    for k, v in (headers or {}).items():
        req += f"{k}: {v}\r\n"
    w.write(req.encode() + b"\r\n" + data)
    await w.drain()
    resp = await r.read()
    w.close()
    head, _, payload = resp.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


def _claude_like(monkeypatch, alive=lambda pid: True):
    monkeypatch.setattr(cc, "pid_alive", alive)
    monkeypatch.setattr(cc, "process_name", lambda pid: "claude")


def test_pid_alive_and_process_name_on_real_processes():
    assert cc.pid_alive(os.getpid())
    name = cc.process_name(os.getpid())
    assert name is None or "python" in name.lower() or "pytest" in name.lower()
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not cc.pid_alive(child.pid) and not cc.watchable_pid(child.pid)
    assert not cc.pid_alive(0) and not cc.pid_alive(-1) and not cc.pid_alive("7")
    boot = cc.boot_time()
    if sys.platform in ("darwin", "win32") or sys.platform.startswith("linux"):
        assert boot is not None and 0 < boot <= time.time() and cc.same_boot(boot)
    # Even with a bare PATH (launchd) the boot time must still be found.
    with_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = "/nonexistent"
        assert cc.boot_time() == boot
    finally:
        os.environ["PATH"] = with_path
    assert not cc.same_boot(None) and not cc.same_boot(12.0)


def test_watchable_requires_a_claude_like_name(monkeypatch):
    monkeypatch.setattr(cc, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cc, "process_name", lambda pid: "zsh")
    assert not cc.watchable_pid(123)                # a wrapper shell is not the session
    monkeypatch.setattr(cc, "process_name", lambda pid: "/Applications/x/claude")
    assert cc.watchable_pid(123)
    monkeypatch.setattr(cc, "process_name", lambda pid: "node")
    assert cc.watchable_pid(123)
    monkeypatch.setattr(cc, "process_name", lambda pid: None)
    assert cc.watchable_pid(123)                    # unknown name: benefit of the doubt


def test_watched_session_lives_exactly_as_long_as_its_process(monkeypatch):
    alive = {"on": True}
    _claude_like(monkeypatch, alive=lambda pid: alive["on"] and pid == 4242)
    c = cc.Companion()
    c.enabled = True
    c.ingest(ev("SessionStart", start_type="startup"), now=0, pid=4242)
    assert c.sessions["s1"].watched and c.sessions["s1"].pid == 4242
    c.ingest(ev("UserPromptSubmit"), now=1, pid=4242)
    c.ingest(ev("Stop"), now=30, pid=4242)
    # A whole day without typing: still there, Ready — the window is open.
    day = 30 + 24 * 3600
    c.expire(now=day)
    assert c.summary(now=day)["s"] == cc.CC_IDLE and c.summary()["n"] == 1
    # Working with no events for ACTIVE_STALE_S = a missed Stop hook → Ready, not dropped.
    c.ingest(ev("PreToolUse", tool_name="Bash", tool_input={"command": "sleep 9"}), now=day + 10, pid=4242)
    assert c.summary()["s"] == cc.CC_TOOL
    c.expire(now=day + 10 + cc.ACTIVE_STALE_S + 1)
    assert c.summary()["s"] == cc.CC_IDLE and c.summary()["n"] == 1
    # A permission prompt can wait for hours; it is never timed out.
    c.ingest(ev("PermissionRequest", tool_name="Bash"), now=day + 20000, pid=4242)
    c.expire(now=day + 20000 + 5 * 3600)
    assert c.summary()["s"] == cc.CC_ATTENTION
    # The process exits → gone on the next tick, no SessionEnd needed.
    alive["on"] = False
    assert c.expire(now=day + 20000 + 5 * 3600 + 2)
    assert c.summary() == {"n": 0, "a": 0, "s": cc.CC_NONE}


def test_unwatched_session_falls_back_to_timers():
    c = cc.Companion()
    c.enabled = True
    c.ingest(ev("SessionStart", start_type="startup"), now=0, host="devbox")
    c.ingest(ev("Stop"), now=1, host="devbox")
    assert not c.sessions["s1"].watched
    c.expire(now=1 + cc.IDLE_STALE_S - 1)
    assert c.summary()["n"] == 1
    assert c.expire(now=1 + cc.IDLE_STALE_S + 1)
    assert c.summary()["n"] == 0
    c.ingest(ev("UserPromptSubmit", sid="w"), now=10, host="devbox")
    assert c.expire(now=10 + cc.ACTIVE_STALE_S + 1) and c.summary()["n"] == 0


def test_non_claude_parent_is_remembered_but_not_watched(monkeypatch):
    monkeypatch.setattr(cc, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cc, "process_name", lambda pid: "bash")
    c = cc.Companion()
    c.ingest(ev("UserPromptSubmit"), now=0, pid=77)
    s = c.sessions["s1"]
    assert s.pid == 77 and not s.watched
    calls = []
    monkeypatch.setattr(cc, "watchable_pid", lambda pid: calls.append(pid) or False)
    c.ingest(ev("PreToolUse", tool_name="Read", tool_input={}), now=1, pid=77)
    assert calls == []                              # not re-checked for the same pid
    c.ingest(ev("PreToolUse", tool_name="Read", tool_input={}), now=2, pid=78)
    assert calls == [78]                            # a new pid (resumed session) is


def test_state_survives_a_restart(tmp_path, monkeypatch):
    _claude_like(monkeypatch, alive=lambda pid: pid == 4242)
    c = cc.Companion()
    c.enabled = True
    c.ingest(ev("UserPromptSubmit", model="claude-fable-5-1"), now=10, pid=4242)
    c.ingest(ev("UserPromptSubmit", sid="remote"), now=11, host="devbox")
    c.ingest(ev("Stop", sid="quiet"), now=12, pid=4243)          # 4243 is dead → unwatched, timers
    f = tmp_path / "companion-state.json"
    c.save(f)
    data = json.loads(f.read_text())
    assert data["v"] == cc.STATE_VERSION and len(data["sessions"]) == 3 and not (tmp_path / "companion-state.json.tmp").exists()

    c2 = cc.Companion()
    c2.enabled = True
    assert c2.load(f, now=20) == 3
    s1 = c2.sessions["s1"]
    assert s1.watched and s1.pid == 4242 and s1.state == cc.CC_THINKING and s1.model == "Fable 5.1"
    assert c2.sessions["remote"].host == "devbox" and c2.sessions["quiet"].state == cc.CC_DONE
    # Its process died while the daemon was down → not restored; the others are.
    monkeypatch.setattr(cc, "pid_alive", lambda pid: False)
    c3 = cc.Companion()
    assert c3.load(f, now=20) == 2 and "s1" not in c3.sessions
    # After a reboot PIDs mean nothing: watched sessions are dropped.
    monkeypatch.setattr(cc, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cc, "same_boot", lambda saved: False)
    c4 = cc.Companion()
    assert c4.load(f, now=20) == 2 and "s1" not in c4.sessions
    # Loading applies the timers too.
    monkeypatch.setattr(cc, "same_boot", lambda saved: True)
    c5 = cc.Companion()
    assert c5.load(f, now=20 + cc.IDLE_STALE_S) == 1 and set(c5.sessions) == {"s1"}
    # Garbage never raises.
    f.write_text("{oops")
    assert cc.Companion().load(f) == 0
    (tmp_path / "old.json").write_text(json.dumps({"v": 0, "sessions": [{"sid": "x"}]}))
    assert cc.Companion().load(tmp_path / "old.json") == 0
    assert cc.Companion().load(tmp_path / "missing.json") == 0
    (tmp_path / "bad.json").write_text(json.dumps({"v": cc.STATE_VERSION, "boot": 0,
                                                   "sessions": [{"sid": "y", "state": "nine", "pid": "x", "bogus": 1}, 7, {"sid": ""}]}))
    c6 = cc.Companion()
    assert c6.load(tmp_path / "bad.json", now=0) == 1 and c6.sessions["y"].state == cc.CC_IDLE and c6.sessions["y"].pid == 0


def test_listener_passes_the_pid_only_for_this_machine(monkeypatch):
    _claude_like(monkeypatch)

    async def run():
        c = cc.Companion()
        port = _free_port()
        server = await cc.start_companion_server(c, lambda: None, "127.0.0.1", port, log=lambda *_: None, token="tok")
        assert server is not None
        # Our own machine: loopback + our host name → watched.
        st, _ = await _http(port, "POST", "/hook", ev("UserPromptSubmit"),
                            {"X-Clawdmeter-Host": cc.LOCAL_HOST, "X-Clawdmeter-Pid": "4242"})
        assert st == 200 and c.sessions["s1"].pid == 4242 and c.sessions["s1"].watched
        # Older hooks without a Host header are local too.
        await _http(port, "POST", "/hook", ev("UserPromptSubmit", sid="s2"), {"X-Clawdmeter-Pid": "4243"})
        assert c.sessions["s2"].watched
        # A tunnelled or containerised host posting over loopback: not our PID space.
        await _http(port, "POST", "/hook", ev("UserPromptSubmit", sid="s3"),
                    {"X-Clawdmeter-Host": "devbox", "X-Clawdmeter-Pid": "4244", "X-Clawdmeter-Token": "tok"})
        assert c.sessions["s3"].pid == 0 and not c.sessions["s3"].watched and c.sessions["s3"].host == "devbox"
        await _http(port, "POST", "/hook", ev("UserPromptSubmit", sid="s4"), {"X-Clawdmeter-Pid": "abc"})
        assert c.sessions["s4"].pid == 0
        server.close()
        await server.wait_closed()

    asyncio.run(run())


def test_hook_command_reports_its_parent_pid():
    assert '-H "X-Clawdmeter-Pid: $PPID"' in cc.hook_command()
    for groups in cc.hooks_fragment()["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                assert "$PPID" in h["command"]
    assert "$PPID" in cc.render_install_sh("http://b:1", "tok") and "$PPID" in cc.render_install_ps1("http://b:1", "tok")


def test_daemons_save_and_restore_the_table(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAWDMETER_NO_LISTENER", raising=False)     # conftest's guard also skips saving
    for mod in (mac, win):
        state = tmp_path / f"{mod.__name__}.json"
        monkeypatch.setattr(mod, "STATE_FILE", state)
        monkeypatch.setattr(mod, "COMPANION", cc.Companion())
        monkeypatch.setattr(mod, "_state_save_handle", None)

        async def run():
            mod.COMPANION.ingest(ev("UserPromptSubmit"), now=time.time(), host="devbox")
            mod._companion_changed()
            mod._companion_changed()                                  # coalesces into one write
            assert not state.exists()
            await asyncio.sleep(1.4)
            assert state.exists() and mod._state_save_handle is None
            monkeypatch.setattr(mod, "COMPANION", cc.Companion())
            mod._restore_companion_state()
            assert mod.COMPANION.summary()["n"] == 1 and mod.COMPANION.summary()["h"] == "devbox"

        asyncio.run(run())
