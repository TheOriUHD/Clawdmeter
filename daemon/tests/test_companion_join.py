#!/usr/bin/env python3
"""Bridge observability: rejected hooks and joins are logged, the served installers
say hello, the hook line takes the bridge from the environment, Tailscale addresses."""
import asyncio
import json
import os
import shutil
import socket
import subprocess

import pytest

import daemon.companion as cc


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


def test_rejections_joins_and_first_contact_are_logged(monkeypatch):
    monkeypatch.setattr(cc, "_is_loopback", lambda addr: False)     # treat the test client as another machine
    logs = []

    async def run():
        c = cc.Companion()
        port = _free_port()
        server = await cc.start_companion_server(c, lambda: None, "127.0.0.1", port, log=logs.append, token="sekret")
        assert server is not None
        logs.clear()
        # No token → 401, logged once with the host name, then rate-limited.
        st, _ = await _http(port, "POST", "/hook", ev("UserPromptSubmit"), {"X-Clawdmeter-Host": "aivm"})
        assert st == 401 and not c.sessions
        st, _ = await _http(port, "POST", "/hook", ev("UserPromptSubmit"), {"X-Clawdmeter-Host": "aivm", "X-Clawdmeter-Token": "wrong"})
        assert st == 401
        rej = [l for l in logs if "rejected a hook from aivm" in l]
        assert len(rej) == 1 and "companion link" in rej[0]
        # The installer's hello: logged, nothing ingested.
        st, _ = await _http(port, "POST", "/hook", {"hook_event_name": "ClawdmeterJoin"},
                            {"X-Clawdmeter-Host": "aivm", "X-Clawdmeter-Token": "sekret"})
        assert st == 200 and not c.sessions
        assert any("aivm joined" in l for l in logs)
        # First real hook from that machine: logged once, with the address.
        for _ in range(2):
            st, _ = await _http(port, "POST", "/hook", ev("UserPromptSubmit"),
                                {"X-Clawdmeter-Host": "aivm", "X-Clawdmeter-Token": "sekret"})
            assert st == 200
        first = [l for l in logs if "first hook from aivm" in l]
        assert len(first) == 1 and "127.0.0.1" in first[0]
        assert c.sessions["s1"].host == "aivm" and not c.sessions["s1"].watched
        # The token may travel in the path instead (what CLAWDMETER_TOKEN does).
        st, _ = await _http(port, "POST", "/hook/sekret", ev("UserPromptSubmit", sid="s2"), {"X-Clawdmeter-Host": "aivm"})
        assert st == 200 and "s2" in c.sessions
        server.close()
        await server.wait_closed()

    asyncio.run(run())


@pytest.mark.skipif(not shutil.which("curl"), reason="curl needed to run the hook line")
def test_hook_line_takes_bridge_and_token_from_the_environment(monkeypatch):
    monkeypatch.setattr(cc, "_is_loopback", lambda addr: False)
    logs = []

    async def run():
        c = cc.Companion()
        port = _free_port()
        server = await cc.start_companion_server(c, lambda: None, "127.0.0.1", port, log=logs.append, token="sekret")
        env = dict(os.environ, CLAWDMETER_URL=f"http://127.0.0.1:{port}", CLAWDMETER_TOKEN="sekret")
        # The default (token-free, loopback) line — as the plugin ships it.
        r = await asyncio.create_subprocess_exec("/bin/sh", "-c", cc.hook_command(), stdin=asyncio.subprocess.PIPE, env=env)
        await r.communicate(json.dumps(ev("UserPromptSubmit", sid="envsid")).encode())
        for _ in range(50):
            if "envsid" in c.sessions:
                break
            await asyncio.sleep(0.05)
        assert "envsid" in c.sessions, logs
        # Without the token it is refused (and logged).
        env.pop("CLAWDMETER_TOKEN")
        r = await asyncio.create_subprocess_exec("/bin/sh", "-c", cc.hook_command(), stdin=asyncio.subprocess.PIPE, env=env)
        await r.communicate(json.dumps(ev("UserPromptSubmit", sid="nope")).encode())
        await asyncio.sleep(0.2)
        assert "nope" not in c.sessions and any("rejected a hook" in l for l in logs)
        server.close()
        await server.wait_closed()

    asyncio.run(run())


def test_installers_say_hello_and_join_text_explains_roles():
    sh = cc.render_install_sh("http://192.168.20.165:47393", "tok")
    assert "ClawdmeterJoin" in sh and "X-Clawdmeter-Token: tok" in sh and "did not answer" in sh
    assert subprocess.run(["/bin/sh", "-n"], input=sh, capture_output=True, text=True).returncode == 0
    ps1 = cc.render_install_ps1("http://192.168.20.165:47393", "tok")
    assert "ClawdmeterJoin" in ps1 and "Invoke-WebRequest" in ps1
    txt = cc.join_text(47393, "tok", ["10.0.0.5"])
    assert "no daemon" in txt and "joined" in txt and "CLAWDMETER_TOKEN=tok" in txt


def test_tailscale_range_and_interface_scan():
    assert cc.is_cgnat("100.126.125.117") and cc.is_cgnat("100.64.0.1") and cc.is_cgnat("100.127.255.254")
    assert not cc.is_cgnat("100.128.0.1") and not cc.is_cgnat("192.168.1.1") and not cc.is_cgnat("100.x.1.1") and not cc.is_cgnat("nope")
    addrs = cc.interface_ipv4s()
    assert isinstance(addrs, list) and all(a.count(".") == 3 for a in addrs)
    if any(cc.is_cgnat(a) for a in addrs):
        assert any(cc.is_cgnat(a) for a in cc.local_addresses())
