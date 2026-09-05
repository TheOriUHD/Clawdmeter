"""Claude Code companion: live session state for the device.

Claude Code fires *hooks* on everything it does (a prompt was submitted, a tool
is about to run, it needs a permission, it finished its turn …). A tiny hook —
one `curl` line, see companion/hooks.json — posts each event's JSON to this
daemon on 127.0.0.1:COMPANION_PORT. A session running on another machine over
SSH reaches the same port through an SSH RemoteForward (or Tailscale), so the
companion works wherever Claude Code runs.

This module turns that stream into ONE compact object for the device, the "cc"
payload key:

    {"n": 2,            live sessions
     "a": 1,            sessions that need you right now
     "s": 5,            headline state (below)
     "l": "Bash: pio run",  what it is doing / waiting for (≤ 24 chars)
     "p": "Clawdmeter", project (cwd basename, ≤ 16)
     "e": 37,           seconds in this state
     "g": 0,            active subagents of the headline session
     "m": "Fable 5.1"}  model, when known (≤ 12)

States (CC_*): 0 none · 1 idle (session open, waiting for a prompt) ·
2 thinking · 3 running a tool · 4 done, quiet (a short exchange ended) ·
5 needs you (permission prompt / question / plan approval) · 6 error ·
7 compacting · 8 done after a long turn (worth a nudge). 5 and 8 are the
"attention" states the device glows and chimes for; the daemon decides which
Stop events deserve 8 (turns longer than LONG_TURN_S) so a quick chat never
nags. The headline session is the most urgent one: attention > working >
done > idle.

`summary()` is what goes on the wire; `signature()` is the part of it whose
change should push a beat to the device at once (everything but the ticking
elapsed seconds).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass, field

COMPANION_PORT = 47393
COMPANION_BIND = "127.0.0.1"

# Device state codes (mirror firmware/src/data.h CompanionState).
CC_NONE, CC_IDLE, CC_THINKING, CC_TOOL, CC_DONE, CC_ATTENTION, CC_ERROR, CC_COMPACTING, CC_TURN_DONE = range(9)
ATTENTION_STATES = (CC_ATTENTION, CC_TURN_DONE)
WORKING_STATES = (CC_THINKING, CC_TOOL, CC_COMPACTING)

LONG_TURN_S = 20.0          # a turn at least this long ends in CC_TURN_DONE (nudge) rather than CC_DONE
DONE_TO_IDLE_S = 10 * 60    # "done" fades to plain idle after this
ACTIVE_STALE_S = 30 * 60    # an active state with no events this long is presumed dead
IDLE_STALE_S = 6 * 3600     # an idle session with no events this long is dropped
LABEL_MAX = 24
PROJECT_MAX = 16
MODEL_MAX = 12

# Tools whose PreToolUse means "Claude is waiting for you", not "Claude is busy".
ASK_TOOLS = {"AskUserQuestion": "Question for you", "ExitPlanMode": "Plan to approve"}


def _short(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _basename(p: str) -> str:
    p = (p or "").rstrip("/\\")
    if not p:
        return ""
    return p.replace("\\", "/").rsplit("/", 1)[-1]


def model_label(model: str | None) -> str:
    """'claude-fable-5-1' → 'Fable 5.1'; unknown shapes pass through shortened."""
    if not isinstance(model, str) or not model:
        return ""
    m = model.strip()
    if m.startswith("claude-"):
        m = m[len("claude-"):]
    m = m.split("[", 1)[0]          # strip "[1m]"-style suffixes
    parts = m.split("-")
    name = parts[0].capitalize() if parts else m
    nums = [p for p in parts[1:] if p.isdigit()]
    if len(nums) >= 2 and len(nums[0]) <= 2:
        return _short(f"{name} {nums[0]}.{nums[1]}", MODEL_MAX)
    if nums:
        return _short(f"{name} {nums[0]}", MODEL_MAX)
    return _short(name, MODEL_MAX)


def tool_label(tool_name: str, tool_input) -> str:
    """One glanceable line for a tool call: 'Editing ui.cpp', 'Bash: pio run'."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    name = tool_name or "tool"
    path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    base = _basename(path) if isinstance(path, str) else ""
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return _short(f"Editing {base}" if base else "Editing", LABEL_MAX)
    if name == "Write":
        return _short(f"Writing {base}" if base else "Writing", LABEL_MAX)
    if name == "Read":
        return _short(f"Reading {base}" if base else "Reading", LABEL_MAX)
    if name in ("Grep", "Glob"):
        pat = ti.get("pattern")
        return _short(f"Searching {pat}" if isinstance(pat, str) else "Searching", LABEL_MAX)
    if name == "Bash":
        cmd = ti.get("command")
        if isinstance(cmd, str) and cmd.strip():
            first = cmd.strip().split("\n", 1)[0]
            return _short(f"Bash: {first}", LABEL_MAX)
        return "Bash"
    if name in ("WebFetch", "WebSearch"):
        return "Browsing the web"
    if name in ("Agent", "Task"):
        d = ti.get("description")
        return _short(f"Agent: {d}" if isinstance(d, str) else "Running an agent", LABEL_MAX)
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "MCP"
        return _short(f"Using {server}", LABEL_MAX)
    if name in ASK_TOOLS:
        return ASK_TOOLS[name]
    return _short(f"Using {name}", LABEL_MAX)


@dataclass
class SessionState:
    sid: str
    state: int = CC_IDLE
    label: str = ""
    project: str = ""
    model: str = ""
    host: str = ""
    since: float = 0.0          # when the current state began
    last_event: float = 0.0
    turn_started: float = 0.0   # UserPromptSubmit time (0 = not in a turn)
    agents: int = 0
    tools: int = 0
    pending_permission: str = ""   # tool waiting for a permission decision

    def set_state(self, state: int, label: str, now: float) -> None:
        if state != self.state or label != self.label:
            self.state = state
            self.label = label
            self.since = now


class Companion:
    """Session table + the device summary. Not thread-safe; one asyncio loop."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.enabled = False       # listener up → send "cc" even with no sessions
        self._last_signature: tuple | None = None

    # ---- ingest --------------------------------------------------------------
    def ingest(self, ev: dict, now: float | None = None, host: str = "") -> bool:
        """Apply one hook event. Returns True when the device-visible summary changed."""
        if not isinstance(ev, dict):
            return False
        now = time.time() if now is None else now
        sid = ev.get("session_id")
        name = ev.get("hook_event_name")
        if not isinstance(sid, str) or not sid or not isinstance(name, str):
            return False
        # Subagent activity does not drive the headline state (it would flicker
        # between the main agent and its helpers); it only counts.
        is_sub = bool(ev.get("agent_id"))

        if name == "SessionEnd":
            self.sessions.pop(sid, None)
            return self._changed()

        s = self.sessions.get(sid)
        if s is None:
            s = SessionState(sid=sid, since=now)
            self.sessions[sid] = s
        s.last_event = now
        if host:
            s.host = host
        cwd = ev.get("cwd")
        if isinstance(cwd, str) and cwd:
            s.project = _short(_basename(cwd), PROJECT_MAX)
        model = ev.get("model")
        if isinstance(model, str) and model:
            s.model = model_label(model)

        tool = ev.get("tool_name") if isinstance(ev.get("tool_name"), str) else ""
        tin = ev.get("tool_input")

        if name == "SessionStart":
            st = ev.get("start_type") or ev.get("source")
            if st == "compact":
                s.set_state(CC_THINKING, "Thinking", now)
            elif s.state not in WORKING_STATES:
                s.set_state(CC_IDLE, "Ready", now)
        elif name == "UserPromptSubmit":
            s.turn_started = now
            s.pending_permission = ""
            s.tools = 0
            s.set_state(CC_THINKING, "Thinking", now)
        elif name == "PreToolUse":
            if not is_sub:
                if tool in ASK_TOOLS:
                    s.set_state(CC_ATTENTION, ASK_TOOLS[tool], now)
                else:
                    s.pending_permission = ""
                    s.set_state(CC_TOOL, tool_label(tool, tin), now)
            s.tools += 1
        elif name in ("PostToolUse", "PostToolUseFailure"):
            if not is_sub:
                s.pending_permission = ""
                if s.turn_started == 0.0:
                    s.turn_started = now
                s.set_state(CC_THINKING, "Thinking", now)
        elif name == "PermissionRequest":
            if not is_sub:
                s.pending_permission = tool
                s.set_state(CC_ATTENTION, _short(f"Permission: {tool}" if tool else "Permission needed", LABEL_MAX), now)
        elif name == "PermissionDenied":
            if not is_sub:
                s.pending_permission = ""
                s.set_state(CC_THINKING, "Thinking", now)
        elif name == "Notification":
            nt = ev.get("notification_type") or ""
            if nt == "permission_prompt":
                s.set_state(CC_ATTENTION, _short(f"Permission: {s.pending_permission}" if s.pending_permission else "Permission needed", LABEL_MAX), now)
            elif nt in ("elicitation_dialog",):
                s.set_state(CC_ATTENTION, "Input needed", now)
            elif nt == "idle_prompt":
                # Claude has been waiting for you for a while.
                if s.state in (CC_DONE, CC_IDLE):
                    s.set_state(CC_TURN_DONE, "Waiting for you", now)
        elif name == "Stop":
            long_turn = s.turn_started > 0.0 and (now - s.turn_started) >= LONG_TURN_S
            s.turn_started = 0.0
            s.pending_permission = ""
            s.agents = 0
            if long_turn:
                s.set_state(CC_TURN_DONE, "Done · your turn", now)
            else:
                s.set_state(CC_DONE, "Your turn", now)
        elif name == "StopFailure":
            s.turn_started = 0.0
            s.set_state(CC_ERROR, "API error", now)
        elif name == "SubagentStart":
            s.agents += 1
        elif name == "SubagentStop":
            s.agents = max(0, s.agents - 1)
        elif name == "PreCompact":
            s.set_state(CC_COMPACTING, "Compacting", now)
        elif name == "PostCompact":
            s.set_state(CC_THINKING, "Thinking", now)
        # Anything else (MessageDisplay, ConfigChange, …) only refreshes last_event.
        return self._changed()

    # ---- housekeeping --------------------------------------------------------
    def expire(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        changed = False
        for sid, s in list(self.sessions.items()):
            age = now - s.last_event
            if s.state in (CC_DONE, CC_TURN_DONE) and now - s.since >= DONE_TO_IDLE_S:
                s.set_state(CC_IDLE, "Ready", now)
            stale = IDLE_STALE_S if s.state in (CC_IDLE, CC_DONE, CC_TURN_DONE) else ACTIVE_STALE_S
            if age >= stale:
                del self.sessions[sid]
                changed = True
        return self._changed() or changed

    # ---- device view -----------------------------------------------------------
    @staticmethod
    def _priority(s: SessionState) -> tuple:
        rank = {CC_ATTENTION: 5, CC_TURN_DONE: 4, CC_TOOL: 3, CC_THINKING: 3, CC_COMPACTING: 3,
                CC_ERROR: 2, CC_DONE: 1, CC_IDLE: 0}.get(s.state, 0)
        return (rank, s.last_event)

    def headline(self) -> SessionState | None:
        if not self.sessions:
            return None
        return max(self.sessions.values(), key=self._priority)

    def summary(self, now: float | None = None) -> dict | None:
        """The "cc" object, or None when the companion is off (no listener)."""
        if not self.enabled and not self.sessions:
            return None
        now = time.time() if now is None else now
        h = self.headline()
        if h is None:
            return {"n": 0, "a": 0, "s": CC_NONE}
        out = {
            "n": len(self.sessions),
            "a": sum(1 for s in self.sessions.values() if s.state in ATTENTION_STATES),
            "s": h.state,
            "l": _short(h.label, LABEL_MAX),
            "e": max(0, int(now - h.since)),
        }
        if h.project:
            out["p"] = h.project
        if h.model:
            out["m"] = h.model
        if h.agents:
            out["g"] = h.agents
        if h.host:
            out["h"] = _short(h.host, 12)
        return out

    def signature(self) -> tuple | None:
        s = self.summary()
        if s is None:
            return None
        return tuple(sorted((k, v) for k, v in s.items() if k != "e"))

    def _changed(self) -> bool:
        sig = self.signature()
        if sig != self._last_signature:
            self._last_signature = sig
            return True
        return False

    def describe(self) -> str:
        s = self.summary()
        if s is None:
            return "companion off"
        names = {CC_NONE: "none", CC_IDLE: "idle", CC_THINKING: "thinking", CC_TOOL: "tool",
                 CC_DONE: "done", CC_ATTENTION: "NEEDS YOU", CC_ERROR: "error",
                 CC_COMPACTING: "compacting", CC_TURN_DONE: "turn done"}
        return f"{s.get('n', 0)} session(s), {names.get(s.get('s'), '?')}: {s.get('l', '')}"


# ---- HTTP listener ---------------------------------------------------------------
#
# A deliberately tiny HTTP/1.1 server: the hook posts a JSON body to /hook and
# wants a 2xx back as fast as possible (the hook is on Claude Code's critical
# path unless it runs async). GET /state returns the summary for debugging.
# No framework, no dependency, nothing listening beyond loopback by default.

_MAX_BODY = 256 * 1024
LOCAL_HOST = ""   # set below, after hostname_short() is defined


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict, bytes]:
    line = await asyncio.wait_for(reader.readline(), timeout=5)
    parts = line.decode("latin-1").strip().split()
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, path = parts[0].upper(), parts[1]
    headers: dict[str, str] = {}
    while True:
        h = await asyncio.wait_for(reader.readline(), timeout=5)
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length < 0 or length > _MAX_BODY:
        raise ValueError("body too large")
    body = await asyncio.wait_for(reader.readexactly(length), timeout=10) if length else b""
    return method, path, headers, body


def _response(status: int, body: str, ctype: str = "text/plain") -> bytes:
    text = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}.get(status, "OK")
    data = body.encode()
    return (f"HTTP/1.1 {status} {text}\r\nContent-Type: {ctype}; charset=utf-8\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data


async def start_companion_server(companion: Companion, on_change, host: str = COMPANION_BIND,
                                 port: int = COMPANION_PORT, log=print):
    """Listen for hook events; call on_change() whenever the summary changes.

    Returns the asyncio server, or None when the port is busy (another daemon
    instance) — the companion then stays off rather than crashing the daemon.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, body = await _read_request(reader)
            peer = writer.get_extra_info("peername")
            remote = ""
            if peer and peer[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                remote = str(peer[0])
            if method == "POST" and path.rstrip("/") in ("/hook", "/event", ""):
                try:
                    ev = json.loads(body.decode("utf-8", "replace") or "{}")
                except ValueError:
                    writer.write(_response(400, "invalid JSON"))
                    return
                hdr_host = headers.get("x-clawdmeter-host", "").split(".", 1)[0]
                if hdr_host.lower() == LOCAL_HOST.lower():
                    hdr_host = ""            # our own machine is not "remote"
                changed = companion.ingest(ev, host=hdr_host or remote)
                writer.write(_response(200, "ok"))
                if changed:
                    log(f"Companion: {companion.describe()}")
                    on_change()
            elif method == "GET" and path.rstrip("/") in ("/state", "/status"):
                writer.write(_response(200, json.dumps(companion.summary() or {}), "application/json"))
            elif method == "GET" and path in ("", "/"):
                writer.write(_response(200, "Clawdmeter companion: POST Claude Code hook JSON to /hook\n"))
            else:
                writer.write(_response(404, "not found"))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, UnicodeDecodeError):
            try:
                writer.write(_response(400, "bad request"))
            except Exception:
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle, host, port, reuse_address=True)
    except OSError as e:
        log(f"Companion listener unavailable on {host}:{port}: {e}")
        companion.enabled = False
        return None
    companion.enabled = True
    log(f"Companion listening on http://{host}:{port}/hook (Claude Code hooks)")
    return server


def hostname_short() -> str:
    try:
        return socket.gethostname().split(".", 1)[0][:12]
    except OSError:
        return ""


LOCAL_HOST = hostname_short()
