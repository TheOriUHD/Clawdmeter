#!/usr/bin/env python3
"""Companion hooks: the fragment, the plugin copy, the marketplace and the installer."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPANION = ROOT / "companion"


def _load_installer():
    spec = importlib.util.spec_from_file_location("install_hooks", COMPANION / "install-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fragment_plugin_and_marketplace_are_consistent():
    frag = json.loads((COMPANION / "hooks.json").read_text())
    plug = json.loads((COMPANION / "plugin" / "hooks" / "hooks.json").read_text())
    assert frag == plug, "companion/hooks.json and the plugin copy must stay identical"
    events = set(frag["hooks"])
    for must in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                 "Notification", "Stop", "SessionEnd"):
        assert must in events
    for groups in frag["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                assert h["type"] == "command" and h.get("async") is True
                assert "CLAWDMETER_URL" in h["command"] and "127.0.0.1:47393" in h["command"]
                assert h["command"].rstrip().endswith("|| true")     # never fail the tool call
    manifest = json.loads((COMPANION / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "clawdmeter-companion"
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["name"] == "clawdmeter" and market["owner"]["name"]
    entry = market["plugins"][0]
    assert entry["name"] == manifest["name"]
    assert (ROOT / entry["source"]).is_dir()


def test_installer_merges_idempotently_and_uninstalls(tmp_path):
    ih = _load_installer()
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]},
    }))
    argv = [sys.executable, str(COMPANION / "install-hooks.py"), "--settings", str(settings)]
    assert subprocess.run(argv, capture_output=True, text=True).returncode == 0
    s1 = json.loads(settings.read_text())
    assert s1["model"] == "opus"
    pre = s1["hooks"]["PreToolUse"]
    assert pre[0]["hooks"][0]["command"] == "echo mine"                 # the user's hook survives, first
    assert any(ih.is_ours(g) for g in pre)
    assert "SessionEnd" in s1["hooks"]
    # Re-running does not duplicate.
    assert subprocess.run(argv, capture_output=True, text=True).returncode == 0
    s2 = json.loads(settings.read_text())
    assert s2 == s1
    # A custom URL is baked in as the default, still overridable by the env var.
    assert subprocess.run(argv + ["--url", "http://mac.local:47393/"], capture_output=True, text=True).returncode == 0
    s3 = json.loads(settings.read_text())
    ours = [g for g in s3["hooks"]["Stop"] if ih.is_ours(g)][0]["hooks"][0]["command"]
    assert "${CLAWDMETER_URL:-http://mac.local:47393}/hook" in ours
    # Uninstall removes only ours.
    assert subprocess.run(argv + ["--uninstall"], capture_output=True, text=True).returncode == 0
    s4 = json.loads(settings.read_text())
    assert s4["hooks"] == {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}
    assert (tmp_path / "settings.json.bak").exists()


def test_installer_creates_settings_when_missing(tmp_path):
    settings = tmp_path / "nested" / "settings.json"
    r = subprocess.run([sys.executable, str(COMPANION / "install-hooks.py"), "--settings", str(settings)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "SessionStart" in json.loads(settings.read_text())["hooks"]


def test_installer_refuses_corrupt_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{oops")
    r = subprocess.run([sys.executable, str(COMPANION / "install-hooks.py"), "--settings", str(settings)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and settings.read_text() == "{oops"
