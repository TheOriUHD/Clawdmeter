#!/usr/bin/env python3
"""WiFi hub: long-poll semantics, several devices at once, payload trimming."""
import asyncio
import json
import socket

import pytest

import daemon.hub as hub_mod
from daemon.hub import Hub


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _get(port, path):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: h\r\n\r\n".encode())
    await w.drain()
    resp = await r.read()
    w.close()
    head, _, body = resp.partition(b"\r\n\r\n")
    return int(head.split()[1]), head.decode(), body.decode()


def test_publish_bumps_seq_only_on_real_change():
    h = Hub()
    assert h.seq == 0
    h.publish({"s": 10})
    assert h.seq == 1
    h.publish({"s": 10})            # identical payload is not news
    assert h.seq == 1
    h.publish({"s": 11})
    assert h.seq == 2


def test_encode_sheds_stats_then_trend_to_fit_one_mtu():
    h = Hub()
    h.publish({"s": 10, "cc": {"n": 1, "s": 2}, "tr": {"h": [1] * 24},
               "st": {"hm": "0" * 1600, "se": 21}})
    data = h.encode()
    assert len(data) <= hub_mod.PAYLOAD_MAX
    body = json.loads(data)
    assert body["s"] == 10 and body["cc"]["s"] == 2      # the live bits survive
    assert "st" not in body and "tr" in body             # the heavy one went first, alone
    # Still too big with the trend as well -> that goes too, live state stays.
    h.publish({"s": 10, "cc": {"n": 1, "s": 2}, "tr": {"h": ["x" * 1500]},
               "st": {"hm": "0" * 1600}})
    body = json.loads(h.encode())
    assert "st" not in body and "tr" not in body and body["cc"]["s"] == 2
    # Comfortably small payloads keep everything, unlike the 500 B BLE cap.
    h.publish({"s": 10, "st": {"se": 21, "hm": "0" * 168}})
    assert "st" in json.loads(h.encode())


def test_long_poll_holds_then_answers_and_serves_many_devices(monkeypatch):
    monkeypatch.setattr(hub_mod, "LONG_POLL_S", 0.4)
    logs = []

    async def run():
        h = Hub()
        port = _free_port()
        h.publish({"s": 10})
        server = await hub_mod.serve_devices(h, "127.0.0.1", port)
        monkeypatch.setattr(hub_mod, "log", logs.append)

        # Behind the hub's sequence -> answered at once, and told where it is.
        status, head, body = await _get(port, "/device/poll?seq=0&id=desk")
        assert status == 200 and json.loads(body)["s"] == 10
        assert "X-Clawdmeter-Seq: 1" in head

        # Up to date -> held, then 204 because nothing moved.
        t0 = asyncio.get_running_loop().time()
        status, _, _ = await _get(port, "/device/poll?seq=1&id=desk")
        held = asyncio.get_running_loop().time() - t0
        assert status == 204 and held >= 0.3

        # Two devices waiting; a publish wakes both with the same payload.
        waiters = [asyncio.create_task(_get(port, f"/device/poll?seq=1&id=d{i}")) for i in (1, 2)]
        await asyncio.sleep(0.1)
        h.publish({"s": 42})
        for t in waiters:
            status, _, body = await t
            assert status == 200 and json.loads(body)["s"] == 42

        # /device/state never blocks.
        status, _, body = await _get(port, "/device/state")
        assert status == 200 and json.loads(body)["s"] == 42
        assert (await _get(port, "/device/nope"))[0] == 404

        assert len(h.devices) == 3 and set(h.devices) == {"desk", "d1", "d2"}
        server.close()
        await server.wait_closed()

    asyncio.run(run())


def test_stale_devices_are_forgotten(monkeypatch):
    h = Hub()
    monkeypatch.setattr(hub_mod, "DEVICE_STALE_S", 0.0)
    assert h.note_device("a", "1.2.3.4")
    assert h.note_device("b", "1.2.3.5")        # 'a' expires as 'b' arrives
    assert set(h.devices) == {"b"}
