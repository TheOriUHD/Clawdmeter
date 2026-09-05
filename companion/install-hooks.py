#!/usr/bin/env python3
"""Install the Clawdmeter companion hooks into Claude Code's user settings.

Merges companion/hooks.json into ~/.claude/settings.json (or
$CLAUDE_CONFIG_DIR/settings.json), keeping every hook you already have. Safe to
re-run: earlier Clawdmeter entries are replaced, nothing else is touched.

    python3 install-hooks.py                # this machine talks to its own daemon on 127.0.0.1:47393
    python3 install-hooks.py --uninstall

For OTHER machines you normally don't need this file: run `companion/link` on
the bridge (the computer with the daemon) and paste the printed one-liner into
the other machine — it fetches an installer from the daemon itself with the
address and token baked in. This script does the same by hand:

    python3 install-hooks.py --url http://192.168.1.23:47393 --token <token>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

MARKER = "CLAWDMETER_URL"          # every command we install contains this
DEFAULT_URL = "http://127.0.0.1:47393"


def settings_path() -> Path:
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg).expanduser() if cfg else Path.home() / ".claude"
    return base / "settings.json"


def load_hooks_fragment(url: str | None, token: str | None = None) -> dict:
    """The hooks to install, with the bridge URL and (for other machines) the token baked in."""
    here = Path(__file__).resolve().parent
    frag = json.loads((here / "hooks.json").read_text())["hooks"]
    target = (url or DEFAULT_URL).rstrip("/")
    auth = f" -H 'X-Clawdmeter-Token: {token}'" if token else ""
    for groups in frag.values():
        for g in groups:
            for h in g["hooks"]:
                cmd = h["command"].replace("${CLAWDMETER_URL:-" + DEFAULT_URL + "}", "${CLAWDMETER_URL:-" + target + "}")
                if auth:
                    cmd = cmd.replace(" --data-binary @-", auth + " --data-binary @-", 1)
                h["command"] = cmd
    return frag


def is_ours(group: dict) -> bool:
    for h in group.get("hooks", []) or []:
        if isinstance(h, dict) and MARKER in str(h.get("command", "")):
            return True
    return False


def merge(settings: dict, fragment: dict | None) -> dict:
    """Drop our previous entries from every event, then add the fragment's."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for ev in list(hooks.keys()):
        groups = hooks.get(ev)
        if isinstance(groups, list):
            kept = [g for g in groups if not (isinstance(g, dict) and is_ours(g))]
            if kept:
                hooks[ev] = kept
            else:
                del hooks[ev]
    if fragment:
        for ev, groups in fragment.items():
            hooks.setdefault(ev, [])
            hooks[ev].extend(groups)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help=f"daemon URL the hooks post to (default {DEFAULT_URL}; "
                                  "CLAWDMETER_URL in the environment overrides at run time)")
    ap.add_argument("--token", help="bridge token for a daemon on another machine (see `companion link`)")
    ap.add_argument("--settings", type=Path, help="settings.json to edit (default: Claude Code's user settings)")
    ap.add_argument("--uninstall", action="store_true", help="remove the Clawdmeter hooks")
    args = ap.parse_args()

    path = args.settings or settings_path()
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text() or "{}")
        except ValueError as e:
            print(f"error: {path} is not valid JSON ({e}); not touching it", file=sys.stderr)
            return 1
        if not isinstance(settings, dict):
            print(f"error: {path} does not hold a JSON object", file=sys.stderr)
            return 1

    fragment = None if args.uninstall else load_hooks_fragment(args.url, args.token)
    merged = merge(settings, fragment)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(merged, indent=2) + "\n")

    if args.uninstall:
        print(f"Removed the Clawdmeter hooks from {path}")
    else:
        n = len(fragment)
        print(f"Installed {n} Clawdmeter hooks into {path}")
        if not shutil.which("curl"):
            print("warning: curl not found on PATH — the hooks need it to reach the daemon", file=sys.stderr)
        print("New Claude Code sessions report to the Clawdmeter daemon; running sessions pick it up on restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
