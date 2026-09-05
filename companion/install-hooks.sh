#!/bin/sh
# One-liner installer for the Clawdmeter companion hooks (any machine that runs
# Claude Code — a laptop, a dev box you ssh into, a container):
#
#   curl -fsSL https://raw.githubusercontent.com/TheOriUHD/Clawdmeter/main/companion/install-hooks.sh | sh
#
# Options pass straight through, e.g. `| sh -s -- --url http://mac.local:47393`
# or `| sh -s -- --uninstall`. Needs python3 and curl.
set -e
RAW="${CLAWDMETER_RAW:-https://raw.githubusercontent.com/TheOriUHD/Clawdmeter/main/companion}"
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t clawdmeter)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$RAW/install-hooks.py" -o "$TMP/install-hooks.py"
curl -fsSL "$RAW/hooks.json" -o "$TMP/hooks.json"
python3 "$TMP/install-hooks.py" "$@"
