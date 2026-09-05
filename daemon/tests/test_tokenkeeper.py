#!/usr/bin/env python3
"""Token keeper: nudge Claude Code to renew its stored token before the device blanks."""
import asyncio
import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import patch

import daemon.tokenkeeper as tk
import daemon.claude_usage_daemon as mac


def test_due_uses_a_margin():
    now = 1_000_000.0
    assert tk.due(None, now) is False
    assert tk.due(now + 3600, now) is False
    assert tk.due(now + tk.REFRESH_MARGIN_S - 1, now) is True
    assert tk.due(now - 5, now) is True


def test_find_cli_prefers_path_then_known_homes(tmp_path):
    with patch.object(tk.shutil, "which", return_value="/somewhere/claude"):
        assert tk.find_claude_cli() == "/somewhere/claude"
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    with patch.object(tk.shutil, "which", return_value=None), patch.object(tk, "CLI_CANDIDATES", [str(fake)]):
        assert tk.find_claude_cli() == str(fake)
    with patch.object(tk.shutil, "which", return_value=None), patch.object(tk, "CLI_CANDIDATES", []):
        assert tk.find_claude_cli() is None


def _fake_cli(tmp_path, exit_code=0):
    """A stand-in `claude` that records argv + the env it saw."""
    rec = tmp_path / "rec.json"
    script = tmp_path / "claude"
    script.write_text(f"""#!/bin/sh
printf '{{"argv": "%s", "url": "%s", "claudecode": "%s", "cwd": "%s"}}' "$*" "$CLAWDMETER_URL" "${{CLAUDECODE:-unset}}" "$(pwd)" > "{rec}"
exit {exit_code}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, rec


def test_keeper_runs_cli_quietly_and_respects_cooldown(tmp_path):
    script, rec = _fake_cli(tmp_path)
    logs = []
    keeper = tk.TokenKeeper(cwd=tmp_path / "work", min_gap_s=100, log=logs.append)
    keeper.cli = str(script)
    with patch.dict(os.environ, {"CLAUDECODE": "1"}):
        assert asyncio.run(keeper.run("test", now=1000.0)) is True
    r = json.loads(rec.read_text())
    assert "-p" in r["argv"] and "--model haiku" in r["argv"]
    assert r["url"] == "http://127.0.0.1:9"          # hooks of the throwaway session go nowhere
    assert r["claudecode"] == "unset"                # not seen as a nested Claude Code
    assert r["cwd"].endswith("work")
    assert any("renewed" in l for l in logs)
    # Cooldown: a second call within min_gap does nothing.
    rec.unlink()
    assert asyncio.run(keeper.run("again", now=1050.0)) is False and not rec.exists()
    assert asyncio.run(keeper.run("later", now=1200.0)) is True and rec.exists()


def test_keeper_reports_failures(tmp_path):
    script, _ = _fake_cli(tmp_path, exit_code=3)
    logs = []
    keeper = tk.TokenKeeper(cwd=tmp_path, min_gap_s=0, log=logs.append)
    keeper.cli = str(script)
    assert asyncio.run(keeper.run("test")) is False
    assert any("exited 3" in l for l in logs)
    missing = tk.TokenKeeper(cwd=tmp_path, min_gap_s=0, log=logs.append)
    with patch.object(tk, "find_claude_cli", return_value=None):
        assert asyncio.run(missing.run("test")) is False
    assert any("not found" in l for l in logs)


def test_mac_reads_expiry_from_blob_and_file(tmp_path):
    ms = 1_788_648_839_477
    assert mac._extract_expiry_s(json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": ms}})) == ms / 1000
    assert mac._extract_expiry_s(json.dumps({"accessToken": "x", "expiresAt": 1_700_000_000})) == 1_700_000_000.0
    assert mac._extract_expiry_s("garbage") is None and mac._extract_expiry_s("[]") is None
    cfg = tmp_path / "cfgdir"
    cfg.mkdir()
    (cfg / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": ms}}))
    assert mac.read_token_expiry(cfg) == ms / 1000
    with patch.object(mac, "CONFIG_FILE", tmp_path / "config"):
        (tmp_path / "config").write_text("token_keeper = off\n")
        assert mac.read_token_keeper_setting() == "off"
        assert asyncio.run(mac.keep_token_fresh("x")) is False
    with patch.object(mac, "CONFIG_FILE", tmp_path / "missing"):
        assert mac.read_token_keeper_setting() == "on"
