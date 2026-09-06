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
import dataclasses
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

COMPANION_PORT = 47393
COMPANION_BIND = "0.0.0.0"       # every interface: other machines join over the network (token-protected)
DEFAULT_URL = "http://127.0.0.1:47393"
TOKEN_HEADER = "x-clawdmeter-token"

# Device state codes (mirror firmware/src/data.h CompanionState).
CC_NONE, CC_IDLE, CC_THINKING, CC_TOOL, CC_DONE, CC_ATTENTION, CC_ERROR, CC_COMPACTING, CC_TURN_DONE = range(9)
ATTENTION_STATES = (CC_ATTENTION, CC_TURN_DONE)
WORKING_STATES = (CC_THINKING, CC_TOOL, CC_COMPACTING)

LONG_TURN_S = 20.0          # a turn at least this long ends in CC_TURN_DONE (nudge) rather than CC_DONE
DONE_TO_IDLE_S = 60         # "your turn" fades to the calm green Ready after a minute
ACTIVE_STALE_S = 30 * 60    # a working state with no events this long: the Stop hook was missed
IDLE_STALE_S = 12 * 3600    # an idle session we cannot watch (another machine) is dropped after this
STATE_VERSION = 1           # companion-state.json format
# Executable names that can be Claude Code (the desktop app's bundle, the native
# CLI, an npm install). A hook's parent that is none of these — a wrapper shell,
# a reused PID — is not worth watching.
CLAUDE_PROCESS_HINTS = ("claude", "node", "bun")
LABEL_MAX = 24
PROJECT_MAX = 16
MODEL_MAX = 12

# Tools whose PreToolUse means "Claude is waiting for you", not "Claude is busy".
ASK_TOOLS = {"AskUserQuestion": "Question for you", "ExitPlanMode": "Plan to approve"}


def _short(s: str, n: int) -> str:
    """Cut to n characters with an ASCII ellipsis (the device fonts have no "…")."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 3].rstrip() + "..."


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


# ---- Process liveness ------------------------------------------------------------
#
# Hooks on the bridge's own machine send their parent PID (X-Clawdmeter-Pid:
# $PPID) — Claude Code runs each hook straight from its own process, so that is
# the session's process. Watching it means a session stays "Ready" for as long
# as that Claude Code is open, whether you type for an hour or sleep on it, and
# vanishes the moment the window closes even if no SessionEnd hook ever came.

def pid_alive(pid: int) -> bool:
    """Does a process with this id exist? Never signals it (os.kill on Windows
    would *terminate* the target — hence the ctypes path)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5             # ERROR_ACCESS_DENIED: exists, not ours
            try:
                code = ctypes.c_ulong()
                if not k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                    return True
                return code.value == 259                        # STILL_ACTIVE
            finally:
                k32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:  # noqa: BLE001 - liveness is best effort; unknown counts as alive
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_name(pid: int) -> str | None:
    """The executable's base name, or None when it cannot be determined."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            handle = k32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(len(buf))
                if not k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
                    return None
                return os.path.basename(buf.value) or None
            finally:
                k32.CloseHandle(ctypes.c_void_p(handle))
        if sys.platform.startswith("linux"):
            try:
                return os.path.basename(os.readlink(f"/proc/{pid}/exe")) or None
            except OSError:
                with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                    return f.read().strip() or None
        exe = shutil.which("ps") or "/bin/ps"
        out = subprocess.run([exe, "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=3)
        name = out.stdout.strip().splitlines()
        return os.path.basename(name[0].strip()) if name else None
    except Exception:  # noqa: BLE001
        return None


def watchable_pid(pid: int) -> bool:
    """A local PID worth watching: alive and, when we can tell, a Claude Code process."""
    if not pid_alive(pid):
        return False
    name = process_name(pid)
    return name is None or any(h in name.lower() for h in CLAUDE_PROCESS_HINTS)


def boot_time() -> float | None:
    """When this machine booted (epoch seconds), or None. PIDs saved before a
    reboot mean nothing after it."""
    try:
        if sys.platform == "darwin":
            # sysctlbyname() straight from libc: launchd's PATH may lack /usr/sbin.
            try:
                import ctypes
                import ctypes.util

                class _Timeval(ctypes.Structure):
                    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_int32)]

                libc = ctypes.CDLL(ctypes.util.find_library("c"))
                tv = _Timeval()
                size = ctypes.c_size_t(ctypes.sizeof(tv))
                if libc.sysctlbyname(b"kern.boottime", ctypes.byref(tv), ctypes.byref(size), None, 0) == 0 and tv.tv_sec > 0:
                    return float(tv.tv_sec)
            except Exception:  # noqa: BLE001 - fall through to the command
                pass
            exe = shutil.which("sysctl") or "/usr/sbin/sysctl"
            out = subprocess.run([exe, "-n", "kern.boottime"], capture_output=True, text=True, timeout=3).stdout
            m = re.search(r"sec\s*=\s*(\d+)", out)
            return float(m.group(1)) if m else None
        if sys.platform.startswith("linux"):
            with open("/proc/stat", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("btime "):
                        return float(line.split()[1])
            return None
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32")
            k32.GetTickCount64.restype = ctypes.c_ulonglong
            return time.time() - k32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001
        return None
    return None


def same_boot(saved: object, tolerance_s: float = 300.0) -> bool:
    if not isinstance(saved, (int, float)):
        return False
    now_boot = boot_time()
    return now_boot is not None and abs(now_boot - float(saved)) <= tolerance_s


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
    pid: int = 0                # the session's Claude Code process (this machine only)
    watched: bool = False       # pid verified alive and Claude-like → liveness, not timers, ends it

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
        self.log = None            # optional logger for the rare, interesting lines

    # ---- ingest --------------------------------------------------------------
    def ingest(self, ev: dict, now: float | None = None, host: str = "", pid: int = 0) -> bool:
        """Apply one hook event. Returns True when the device-visible summary changed.

        ``pid`` is the hook's parent process on *this* machine (0 = unknown or
        another machine); a live Claude-like process is watched from then on.
        """
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
        if pid and pid != s.pid:
            s.pid = pid
            s.watched = watchable_pid(pid)
            if self.log:
                self.log(f"Companion: session {sid[:8]} is pid {pid}"
                         + (f" ({process_name(pid) or 'process'}) — watching it" if s.watched
                            else " — not a Claude Code process, using timers"))
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
                s.set_state(CC_TURN_DONE, "Finished, your turn", now)
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
        """Time and liveness housekeeping; True when the device-visible summary changed.

        A watched session (its process is on this machine) lives exactly as long
        as the process: no idle timeout, gone the tick it exits. A working state
        with no events for ACTIVE_STALE_S means a missed Stop hook → Ready. An
        unwatched session (another machine) falls back to the timers.
        """
        now = time.time() if now is None else now
        changed = False
        for sid, s in list(self.sessions.items()):
            age = now - s.last_event
            if s.state in (CC_DONE, CC_TURN_DONE) and now - s.since >= DONE_TO_IDLE_S:
                s.set_state(CC_IDLE, "Ready", now)
            if s.watched:
                if not pid_alive(s.pid):
                    if self.log:
                        self.log(f"Companion: session {sid[:8]} ended (pid {s.pid} gone)")
                    del self.sessions[sid]
                    changed = True
                elif s.state in WORKING_STATES + (CC_ERROR,) and age >= ACTIVE_STALE_S:
                    s.set_state(CC_IDLE, "Ready", now)
                continue
            stale = IDLE_STALE_S if s.state in (CC_IDLE, CC_DONE, CC_TURN_DONE) else ACTIVE_STALE_S
            if age >= stale:
                del self.sessions[sid]
                changed = True
        return self._changed() or changed

    # ---- persistence -----------------------------------------------------------
    # The table survives a daemon restart (a deploy, a reboot of the bridge): the
    # sessions come back and are re-checked against their processes, so the
    # display never drops to "Idle" only because the bridge was restarted.
    def to_dict(self) -> dict:
        return {"v": STATE_VERSION, "boot": boot_time(), "saved": time.time(),
                "sessions": [dataclasses.asdict(s) for s in self.sessions.values()]}

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    def load(self, path: Path, now: float | None = None) -> int:
        """Restore a saved table (never raises). Returns the number of sessions kept."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict) or data.get("v") != STATE_VERSION:
            return 0
        now = time.time() if now is None else now
        boot_ok = same_boot(data.get("boot"))
        fields = {f.name: f.type for f in dataclasses.fields(SessionState)}
        kept = 0
        for raw in data.get("sessions") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("sid"), str) or not raw["sid"]:
                continue
            s = SessionState(sid=raw["sid"])
            for k, v in raw.items():
                if k == "sid" or k not in fields:
                    continue
                cur = getattr(s, k)
                try:
                    if isinstance(cur, bool):
                        v = bool(v)
                    elif isinstance(cur, int):
                        v = int(v)
                    elif isinstance(cur, float):
                        v = float(v)
                    elif isinstance(cur, str):
                        v = str(v)
                except (TypeError, ValueError):
                    continue
                setattr(s, k, v)
            if s.watched and not (boot_ok and watchable_pid(s.pid)):
                continue                     # its process is gone (or PIDs mean nothing after a reboot)
            self.sessions[s.sid] = s
            kept += 1
        self.expire(now)
        return len(self.sessions)

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


# ---- The hooks themselves ------------------------------------------------------
#
# One shell line per Claude Code event: post the JSON Claude Code pipes to stdin
# to the bridge, in the background (`async`), never failing the tool call
# (`|| true`, 2 s cap). companion/hooks.json and the plugin copy are generated
# from hooks_fragment() — this is the single source of truth.
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
               "PermissionRequest", "PermissionDenied", "Notification", "Stop", "StopFailure",
               "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "SessionEnd"]


def hook_command(url: str = DEFAULT_URL, token: str | None = None) -> str:
    """The curl line. CLAWDMETER_URL in the environment overrides the baked-in bridge URL."""
    url = url.rstrip("/")
    auth = f" -H 'X-Clawdmeter-Token: {token}'" if token else ""
    return ("curl -s -m 2 -o /dev/null -X POST -H 'Content-Type: application/json' "
            "-H \"X-Clawdmeter-Host: $(hostname -s 2>/dev/null || hostname)\" "
            "-H \"X-Clawdmeter-Pid: $PPID\"" + auth +
            " --data-binary @- \"${CLAWDMETER_URL:-" + url + "}/hook\" >/dev/null 2>&1 || true")


def hooks_fragment(url: str = DEFAULT_URL, token: str | None = None) -> dict:
    """{"hooks": {<event>: [{"hooks": [{command}]}]}} as it goes into settings.json."""
    cmd = hook_command(url, token)
    return {"hooks": {ev: [{"hooks": [{"type": "command", "command": cmd, "async": True, "timeout": 5}]}]
                      for ev in HOOK_EVENTS}}


# The merge logic the served installers carry, in Python and in JavaScript: drop
# every hook group of ours (marker CLAWDMETER_URL), append the fresh ones, keep
# everything else in settings.json byte-for-byte in spirit.
MERGE_PY = r"""
import json, os, shutil, sys
settings_path, frag_path, uninstall = sys.argv[1], sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == "--uninstall"
settings = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        text = f.read()
    settings = json.loads(text) if text.strip() else {}
    if not isinstance(settings, dict):
        sys.exit("settings.json does not hold a JSON object")
    shutil.copy2(settings_path, settings_path + ".bak")
with open(frag_path) as f:
    frag = json.load(f)["hooks"]
hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
def ours(g):
    return isinstance(g, dict) and any("CLAWDMETER_URL" in str(h.get("command", "")) for h in g.get("hooks", []) or [] if isinstance(h, dict))
for ev in list(hooks):
    kept = [g for g in hooks[ev] if not ours(g)] if isinstance(hooks[ev], list) else hooks[ev]
    if kept: hooks[ev] = kept
    else: del hooks[ev]
if not uninstall:
    for ev, groups in frag.items():
        hooks.setdefault(ev, []).extend(groups)
if hooks: settings["hooks"] = hooks
else: settings.pop("hooks", None)
os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
"""

MERGE_JS = r"""
const fs = require("fs"), path = require("path");
const [settingsPath, fragPath, flag] = process.argv.slice(2);
const uninstall = flag === "--uninstall";
let settings = {};
if (fs.existsSync(settingsPath)) {
  const text = fs.readFileSync(settingsPath, "utf8");
  settings = text.trim() ? JSON.parse(text) : {};
  if (typeof settings !== "object" || Array.isArray(settings) || settings === null) { console.error("settings.json does not hold a JSON object"); process.exit(1); }
  fs.copyFileSync(settingsPath, settingsPath + ".bak");
}
const frag = JSON.parse(fs.readFileSync(fragPath, "utf8")).hooks;
const hooks = (settings.hooks && typeof settings.hooks === "object" && !Array.isArray(settings.hooks)) ? settings.hooks : {};
const ours = g => g && typeof g === "object" && Array.isArray(g.hooks) && g.hooks.some(h => h && String(h.command || "").includes("CLAWDMETER_URL"));
for (const ev of Object.keys(hooks)) {
  if (!Array.isArray(hooks[ev])) continue;
  const kept = hooks[ev].filter(g => !ours(g));
  if (kept.length) hooks[ev] = kept; else delete hooks[ev];
}
if (!uninstall) for (const [ev, groups] of Object.entries(frag)) { (hooks[ev] = hooks[ev] || []).push(...groups); }
if (Object.keys(hooks).length) settings.hooks = hooks; else delete settings.hooks;
fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
"""


def render_install_sh(base_url: str, token: str) -> str:
    """A self-contained POSIX installer the bridge serves at /install/<token>.

    Works on any Linux/macOS box that has curl plus python3 or node (a VM, a
    container, a dev server); `sh -s -- --uninstall` removes the hooks again.
    """
    frag = json.dumps(hooks_fragment(base_url, token), indent=2)
    return f"""#!/bin/sh
# Clawdmeter companion — installs the Claude Code hooks that report this
# machine's sessions to the bridge at {base_url}. Served by the bridge itself.
# Re-run any time (idempotent); `... | sh -s -- --uninstall` removes them.
set -e
SETTINGS="${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}/settings.json"
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t clawdmeter)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/hooks.json" <<'CLAWD_HOOKS'
{frag}
CLAWD_HOOKS
cat > "$TMP/merge.py" <<'CLAWD_PY'
{MERGE_PY.strip()}
CLAWD_PY
cat > "$TMP/merge.js" <<'CLAWD_JS'
{MERGE_JS.strip()}
CLAWD_JS
if command -v python3 >/dev/null 2>&1; then
    python3 "$TMP/merge.py" "$SETTINGS" "$TMP/hooks.json" "$@"
elif command -v node >/dev/null 2>&1; then
    node "$TMP/merge.js" "$SETTINGS" "$TMP/hooks.json" "$@"
else
    echo "Clawdmeter: need python3 or node to edit $SETTINGS" >&2
    exit 1
fi
if [ "$1" = "--uninstall" ]; then
    echo "Clawdmeter companion hooks removed from $SETTINGS"
else
    command -v curl >/dev/null 2>&1 || echo "warning: curl not found — the hooks need it" >&2
    echo "Clawdmeter companion hooks installed in $SETTINGS"
    echo "New Claude Code sessions on this machine report to {base_url}"
fi
"""


def render_install_ps1(base_url: str, token: str) -> str:
    """A PowerShell installer for Windows workers: `irm <url> | iex` (no Python needed).

    Claude Code on Windows runs command hooks through Git Bash, which ships curl,
    so the hook line itself is the same as everywhere else.
    """
    frag = json.dumps(hooks_fragment(base_url, token), indent=2)
    return f"""# Clawdmeter companion — installs the Claude Code hooks that report this
# machine's sessions to the bridge at {base_url}. Served by the bridge itself.
$ErrorActionPreference = 'Stop'
$dir = if ($env:CLAUDE_CONFIG_DIR) {{ $env:CLAUDE_CONFIG_DIR }} else {{ Join-Path $HOME '.claude' }}
$path = Join-Path $dir 'settings.json'
$frag = @'
{frag}
'@ | ConvertFrom-Json
$settings = [pscustomobject]@{{}}
if (Test-Path $path) {{
    $raw = Get-Content $path -Raw
    if ($raw.Trim()) {{ $settings = $raw | ConvertFrom-Json }}
    Copy-Item $path "$path.bak" -Force
}}
if (-not ($settings.PSObject.Properties.Name -contains 'hooks')) {{
    $settings | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{{}})
}}
$ours = {{ param($g) ($g.hooks | ForEach-Object {{ [string]$_.command }}) -join ' ' -match 'CLAWDMETER_URL' }}
foreach ($ev in @($settings.hooks.PSObject.Properties.Name)) {{
    $kept = @($settings.hooks.$ev | Where-Object {{ -not (& $ours $_) }})
    if ($kept.Count) {{ $settings.hooks.$ev = $kept }} else {{ $settings.hooks.PSObject.Properties.Remove($ev) }}
}}
foreach ($ev in $frag.hooks.PSObject.Properties.Name) {{
    $new = @($frag.hooks.$ev)
    if ($settings.hooks.PSObject.Properties.Name -contains $ev) {{
        $settings.hooks.$ev = @($settings.hooks.$ev) + $new
    }} else {{
        $settings.hooks | Add-Member -NotePropertyName $ev -NotePropertyValue $new
    }}
}}
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$settings | ConvertTo-Json -Depth 12 | Set-Content -Path $path -Encoding UTF8
Write-Host "Clawdmeter companion hooks installed in $path"
Write-Host "New Claude Code sessions on this machine report to {base_url}"
"""


# ---- Token + addresses -----------------------------------------------------------
def load_or_create_token(path: Path, override: str | None = None) -> str:
    """The shared secret other machines present. From the config (`companion_token`)
    when set, else a generated one kept next to the config file."""
    if override and override.strip():
        return override.strip()
    path = Path(path)
    try:
        tok = path.read_text().strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_hex(10)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tok + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return tok


def local_addresses() -> list[str]:
    """Addresses another machine might reach this one on: the default-route IPv4,
    every IPv4 the hostname resolves to, a Tailscale address, and <host>.local."""
    out: list[str] = []

    def add(a: str) -> None:
        if a and not a.startswith("127.") and a not in out:
            out.append(a)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))          # no packet is sent; picks the default route
        add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    if shutil.which("tailscale"):
        try:
            r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                add(line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    host = hostname_short()
    if host:
        add(f"{host}.local")
    return out


def join_text(port: int, token: str, addresses: list[str] | None = None) -> str:
    """The lines a user pastes into another machine to make it report here."""
    addrs = local_addresses() if addresses is None else addresses
    if not addrs:
        addrs = ["<this-machine>"]
    lines = ["Clawdmeter companion — make another machine report to this bridge.",
             "On the machine that runs Claude Code, run ONE of these (whichever address it can reach):", ""]
    for a in addrs:
        lines.append(f"  curl -fsSL http://{a}:{port}/install/{token} | sh")
    lines += ["", "Windows (PowerShell):", ""]
    for a in addrs[:1]:
        lines.append(f"  irm http://{a}:{port}/install.ps1/{token} | iex")
    lines += ["", f"Check from there:  curl -s http://<bridge>:{port}/state/{token}",
              "Remove later:      ... /install/<token> | sh -s -- --uninstall",
              "The token lives next to the daemon config (companion.token); anything not from this machine must present it."]
    return "\n".join(lines)


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


def _is_loopback(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or addr.startswith("127.")


async def start_companion_server(companion: Companion, on_change, host: str = COMPANION_BIND,
                                 port: int = COMPANION_PORT, log=print, token: str | None = None):
    """Listen for hook events; call on_change() whenever the summary changes.

    Loopback callers (the bridge's own hooks, an SSH tunnel) need nothing; any
    other machine must present `token` (X-Clawdmeter-Token header, or in the
    path for the GET routes). GET /install/<token> and /install.ps1/<token>
    hand out installers with this bridge's address and token baked in — the
    one-liners join_text() prints.

    Returns the asyncio server, or None when the port is busy (another daemon
    instance) — the companion then stays off rather than crashing the daemon.
    """

    def authed(headers: dict, remote: str, path_token: str | None = None) -> bool:
        if not token or not remote:
            return True
        return headers.get(TOKEN_HEADER, "") == token or path_token == token

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, body = await _read_request(reader)
            peer = writer.get_extra_info("peername")
            remote = ""
            if peer and not _is_loopback(str(peer[0])):
                remote = str(peer[0])
            parts = [x for x in path.split("?", 1)[0].split("/") if x]
            route = parts[0] if parts else ""
            arg = parts[1] if len(parts) > 1 else None
            if method == "POST" and route in ("hook", "event", ""):
                if not authed(headers, remote, arg):
                    writer.write(_response(401, "token required (see: companion link)"))
                    return
                try:
                    ev = json.loads(body.decode("utf-8", "replace") or "{}")
                except ValueError:
                    writer.write(_response(400, "invalid JSON"))
                    return
                hdr_host = headers.get("x-clawdmeter-host", "").split(".", 1)[0]
                if hdr_host.lower() == LOCAL_HOST.lower():
                    hdr_host = ""            # our own machine is not "remote"
                # The hook's parent PID is only meaningful for this machine's own
                # sessions: loopback, and not a tunnelled or containerised host.
                pid = 0
                if not remote and not hdr_host:
                    try:
                        pid = int(headers.get("x-clawdmeter-pid", "0") or 0)
                    except ValueError:
                        pid = 0
                changed = companion.ingest(ev, host=hdr_host or remote, pid=pid)
                writer.write(_response(200, "ok"))
                if changed:
                    on_change()          # the beat's log line says what changed
            elif method == "GET" and route in ("state", "status"):
                if not authed(headers, remote, arg):
                    writer.write(_response(401, "token required"))
                    return
                writer.write(_response(200, json.dumps(companion.summary() or {}), "application/json"))
            elif method == "GET" and route in ("install", "install.sh", "install.ps1"):
                if not token or arg != token:
                    writer.write(_response(404, "not found"))
                    return
                # The address the client used to reach us is the one its hooks should use.
                host_hdr = headers.get("host") or f"127.0.0.1:{port}"
                base = f"http://{host_hdr}"
                if route == "install.ps1":
                    writer.write(_response(200, render_install_ps1(base, token), "text/plain"))
                else:
                    writer.write(_response(200, render_install_sh(base, token), "text/x-shellscript"))
                log(f"Companion: served the {'PowerShell' if route == 'install.ps1' else 'shell'} installer to {remote or 'this machine'}")
            elif method == "GET" and route == "":
                writer.write(_response(200, "Clawdmeter companion bridge. Run `companion link` on the bridge for the join command.\n"))
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
    log(f"Companion listening on {host}:{port} (Claude Code hooks; other machines join with `companion link`)")
    return server


def hostname_short() -> str:
    """This machine's own short host name (untruncated; the wire field is cut later)."""
    try:
        return socket.gethostname().split(".", 1)[0]
    except OSError:
        return ""


LOCAL_HOST = hostname_short()
