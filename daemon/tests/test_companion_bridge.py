#!/usr/bin/env python3
"""Companion bridge: token auth, served installers, join text, Windows parity."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import daemon.companion as cc
import daemon.claude_usage_daemon as mac
import daemon.claude_usage_daemon_windows as win

ROOT = Path(__file__).resolve().parents[2]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _http(port, method, path, body=None, headers=None, host_hdr="bridge.local:1"):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = (json.dumps(body).encode() if body is not None else b"")
    req = f"{method} {path} HTTP/1.1\r\nHost: {host_hdr}\r\nContent-Length: {len(data)}\r\n"
    for k, v in (headers or {}).items():
        req += f"{k}: {v}\r\n"
    w.write(req.encode() + b"\r\n" + data)
    await w.drain()
    resp = await r.read()
    w.close()
    head, _, payload = resp.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


def test_hooks_json_is_generated_from_the_module():
    assert json.loads((ROOT / "companion" / "hooks.json").read_text()) == cc.hooks_fragment()
    assert json.loads((ROOT / "companion" / "plugin" / "hooks" / "hooks.json").read_text()) == cc.hooks_fragment()
    cmd = cc.hook_command("http://192.168.1.5:47393/", "tok")
    assert "X-Clawdmeter-Token: tok" in cmd and "${CLAWDMETER_URL:-http://192.168.1.5:47393}/hook" in cmd
    assert "X-Clawdmeter-Token" not in cc.hook_command()


def test_token_file_and_override(tmp_path):
    f = tmp_path / "companion.token"
    t1 = cc.load_or_create_token(f)
    assert len(t1) == 20 and f.read_text().strip() == t1
    assert cc.load_or_create_token(f) == t1                       # stable
    assert cc.load_or_create_token(f, override=" mine ") == "mine"
    if os.name == "posix":
        assert (f.stat().st_mode & 0o777) == 0o600


def test_local_addresses_and_join_text():
    addrs = cc.local_addresses()
    assert all(not a.startswith("127.") for a in addrs)
    txt = cc.join_text(47393, "abc", ["10.0.0.5", "host.local"])
    assert "curl -fsSL http://10.0.0.5:47393/install/abc | sh" in txt
    assert "curl -fsSL http://host.local:47393/install/abc | sh" in txt
    assert "irm http://10.0.0.5:47393/install.ps1/abc | iex" in txt
    assert "<this-machine>" in cc.join_text(1, "t", [])


def test_listener_token_rules_and_install_routes():
    async def run():
        c = cc.Companion()
        port = _free_port()
        server = await cc.start_companion_server(c, lambda: None, "127.0.0.1", port, log=lambda *_: None, token="sekret")
        assert server is not None
        ev = {"session_id": "s", "hook_event_name": "UserPromptSubmit", "cwd": "/x/proj"}
        # Loopback needs no token.
        status, _ = await _http(port, "POST", "/hook", ev)
        assert status == 200 and c.summary()["s"] == cc.CC_THINKING
        # A non-loopback caller is simulated by making the handler see a remote peer.
        with patch.object(cc, "_is_loopback", return_value=False):
            status, body = await _http(port, "POST", "/hook", ev)
            assert status == 401 and b"token" in body
            status, _ = await _http(port, "POST", "/hook", ev, headers={"X-Clawdmeter-Token": "sekret"})
            assert status == 200
            status, _ = await _http(port, "GET", "/state")
            assert status == 401
            status, body = await _http(port, "GET", "/state/sekret")
            assert status == 200 and json.loads(body)["n"] == 1
        # Installers: only with the right token; the URL is the one the client used.
        status, _ = await _http(port, "GET", "/install/wrong")
        assert status == 404
        status, sh = await _http(port, "GET", "/install/sekret", host_hdr="192.168.1.9:47393")
        assert status == 200 and b"#!/bin/sh" in sh and b"http://192.168.1.9:47393" in sh and b"X-Clawdmeter-Token: sekret" in sh
        status, ps = await _http(port, "GET", "/install.ps1/sekret", host_hdr="192.168.1.9:47393")
        assert status == 200 and b"ConvertFrom-Json" in ps and b"http://192.168.1.9:47393" in ps
        # Without a token nothing is served.
        server.close(); await server.wait_closed()
        c2 = cc.Companion(); port2 = _free_port()
        s2 = await cc.start_companion_server(c2, lambda: None, "127.0.0.1", port2, log=lambda *_: None, token=None)
        status, _ = await _http(port2, "GET", "/install/anything")
        assert status == 404
        s2.close(); await s2.wait_closed()
        return sh.decode()
    sh_script = asyncio.run(run())
    return sh_script


def _run_served_installer(tmp_path, script: str, prefer: str):
    """Run the served shell installer against a throw-away HOME with only `prefer` on PATH."""
    home = tmp_path / f"home_{prefer}"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
    }))
    # A PATH with only the chosen interpreter: /bin for sh/cat/rm/echo, plus
    # mktemp linked in (it lives in /usr/bin, which also holds macOS's python3
    # stub — that stub must NOT be reachable in the node-only run).
    bindir = tmp_path / f"bin_{prefer}"
    bindir.mkdir()
    exe = shutil.which(prefer)
    if not exe:
        pytest.skip(f"{prefer} not available")
    os.symlink(exe, bindir / prefer)
    mktemp = shutil.which("mktemp")
    if mktemp and not mktemp.startswith("/bin/"):
        os.symlink(mktemp, bindir / "mktemp")
    env = {"HOME": str(home), "PATH": f"{bindir}:/bin", "TMPDIR": str(tmp_path)}
    r = subprocess.run(["/bin/sh", "-s"], input=script, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    s = json.loads((home / ".claude" / "settings.json").read_text())
    assert s["model"] == "opus"
    assert s["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo mine"
    assert any("CLAWDMETER_URL" in h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"])
    assert "X-Clawdmeter-Token: sekret" in s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert (home / ".claude" / "settings.json.bak").exists()
    # Idempotent, then uninstall keeps the user's own hook.
    r = subprocess.run(["/bin/sh", "-s"], input=script, capture_output=True, text=True, env=env)
    assert r.returncode == 0 and json.loads((home / ".claude" / "settings.json").read_text()) == s
    r = subprocess.run(["/bin/sh", "-s", "--", "--uninstall"], input=script, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    s2 = json.loads((home / ".claude" / "settings.json").read_text())
    assert s2["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}


def test_served_installer_runs_with_python3(tmp_path):
    _run_served_installer(tmp_path, cc.render_install_sh("http://192.168.1.9:47393", "sekret"), "python3")


def test_served_installer_runs_with_node_only(tmp_path):
    _run_served_installer(tmp_path, cc.render_install_sh("http://192.168.1.9:47393", "sekret"), "node")


def test_windows_daemon_parity(tmp_path):
    for name in ("read_companion_setting", "read_companion_port", "read_companion_bind", "read_trend_setting",
                 "companion_token", "finalize_payload", "companion_beat", "shrink_payload", "COMPANION"):
        assert hasattr(win, name), name
    hist = win.trend_mod.History(tmp_path / "h.json")
    old = (win.HISTORY, win.COMPANION)
    try:
        win.HISTORY = hist
        win.COMPANION = cc.Companion()
        win.COMPANION.enabled = True
        out = win.finalize_payload({"s": 5, "sr": 1, "w": 2, "wr": 3, "st": "allowed", "acct": "pro", "ok": True})
        assert out["cc"] == {"n": 0, "a": 0, "s": 0} and "tr" not in out     # trend retired: history only
        # Long payloads go with response; short ones without.
        client = MagicMock()
        client.mtu_size = 247
        client.write_gatt_char = AsyncMock(return_value=None)
        sess = win.Session(client)
        asyncio.run(sess.write_payload({"ok": True}))
        assert client.write_gatt_char.call_args.kwargs["response"] is False
        asyncio.run(sess.write_payload(out))
        assert client.write_gatt_char.call_args.kwargs["response"] is (len(win.shrink_payload(out)) > 244)
    finally:
        win.HISTORY, win.COMPANION = old
    with patch.object(win, "CONFIG_FILE", tmp_path / "config"):
        (tmp_path / "config").write_text("companion_bind = 127.0.0.1\ncompanion_token = abc\n")
        assert win.read_companion_bind() == "127.0.0.1"
        with patch.object(win, "TOKEN_FILE", tmp_path / "companion.token"):
            assert win.companion_token() == "abc"


def test_mac_link_command_prints_join_lines(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    with patch.object(mac, "TOKEN_FILE", tmp_path / "companion.token"), patch.object(mac, "CONFIG_FILE", tmp_path / "config"):
        tok = mac.companion_token()
    r = subprocess.run([sys.executable, str(ROOT / "daemon" / "claude_usage_daemon.py"), "link"],
                       capture_output=True, text=True, env=env, cwd=str(ROOT / "daemon"))
    assert r.returncode == 0, r.stderr
    assert "/install/" in r.stdout and "| sh" in r.stdout and "irm http://" in r.stdout
