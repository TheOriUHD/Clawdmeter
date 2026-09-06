"""Test-suite guards.

The daemons have side effects the suite must never trigger: the token keeper
spawns the real `claude` CLI when a poll is rejected (the Windows loop tests
drive exactly that path with mocked polls), and main() opens the companion
listener and creates a token file. Both honour these environment switches.
"""
import os

os.environ["CLAWDMETER_NO_TOKEN_KEEPER"] = "1"
os.environ["CLAWDMETER_NO_LISTENER"] = "1"
