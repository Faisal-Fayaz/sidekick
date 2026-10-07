"""Event hooks: run configured commands on agent lifecycle events.

v1 scope: `command` handlers only (`[[hooks.<Event>]]` in the global config
file) for SessionStart, PreToolUse, PostToolUse. Handlers receive a JSON
envelope on stdin and may answer `{"decision": "allow"|"deny", "reason": ...}`.
Exit 0 + non-JSON output counts as allow (logging hooks stay usable);
non-zero exit or timeout denies PreToolUse (fail closed) and warns elsewhere.
All entry points never raise.
"""

from __future__ import annotations

import json
import os
import subprocess

HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse")
DEFAULT_TIMEOUT = 30.0

_started_sessions: set[str] = set()


def load_hooks() -> list[dict]:
    """Configured handlers from the global config file. Never raises."""
    try:
        from .config import Config

        return [dict(h) for h in (Config.load().hooks or ())]
    except Exception:
        return []


def _envelope(event: str, payload: dict) -> str:
    body = {"event": event, "session": payload.get("session", ""), "cwd": os.getcwd()}
    for key in ("tool", "args", "result", "dry_run"):
        if key in payload:
            body[key] = payload[key]
    try:
        return json.dumps(body, default=str)
    except Exception:
        return "{}"


def run_hook(command: str, event: str, payload: dict, timeout: float) -> dict:
    """Run one handler. Returns {ok, decision, reason, command}. Never raises."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=_envelope(event, payload),
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
        )
    except subprocess.TimeoutExpired:
        deny = event == "PreToolUse"
        return {
            "ok": False,
            "decision": "deny" if deny else "allow",
            "reason": f"hook timed out after {timeout:g}s",
            "command": command,
        }
    except Exception as e:
        deny = event == "PreToolUse"
        return {
            "ok": False,
            "decision": "deny" if deny else "allow",
            "reason": f"hook failed to spawn: {e}",
            "command": command,
        }
    decision, reason = "allow", ""
    try:
        data = json.loads((proc.stdout or "").strip())
        if isinstance(data, dict) and str(data.get("decision", "")).strip().lower() in (
            "allow",
            "deny",
        ):
            decision = str(data["decision"]).strip().lower()
            reason = str(data.get("reason", "") or "")[:500]
    except Exception:
        pass
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        tail = err[-1][:200] if err else f"exit {proc.returncode}"
        if event == "PreToolUse":
            return {"ok": False, "decision": "deny", "reason": tail, "command": command}
        reason = f"hook exited {proc.returncode}: {tail}" if not reason else reason
        return {"ok": False, "decision": decision, "reason": reason, "command": command}
    return {"ok": True, "decision": decision, "reason": reason, "command": command}


def fire_event(event: str, payload: dict) -> list[dict]:
    """Run every handler for event in config order. Never raises."""
    out: list[dict] = []
    try:
        handlers = [h for h in load_hooks() if h.get("event") == event]
    except Exception:
        return out
    for h in handlers:
        try:
            timeout = float(h.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        try:
            out.append(run_hook(str(h.get("command", "")), event, payload, timeout))
        except Exception as e:
            # A PreToolUse handler that cannot be consulted has not approved
            # anything, so skipping it allowed the tool. `run_hook` is
            # documented as never raising, so reaching this means something
            # unexpected happened in our own code -- and for a gate on a tool
            # call, unexpected must fail closed. PostToolUse and friends stay
            # best-effort, because a notification that did not fire must not
            # block a turn.
            if event == "PreToolUse":
                out.append(
                    {
                        "ok": False,
                        "decision": "deny",
                        "reason": f"hook could not be consulted: {type(e).__name__}: {e}"[:500],
                        "command": str(h.get("command", "")),
                    }
                )
            continue
    return out


def pre_tool_use(session: str, tool: str, args: dict) -> tuple[bool, str]:
    """True+"" when all PreToolUse handlers allow; first deny wins. Never raises."""
    try:
        for res in fire_event(
            event="PreToolUse", payload={"session": session, "tool": tool, "args": args}
        ):
            if res.get("decision") == "deny":
                reason = str(res.get("reason", "") or "denied by hook")
                return False, f"Denied by hook `{res.get('command', '?')}`: {reason}"
    except Exception as e:
        # Previously this discarded the results collected so far and returned
        # allow. If the failure happened after a deny was already recorded, that
        # deny was thrown away and the tool ran -- the one outcome a security
        # hook must never produce. Deny instead, and say why.
        return False, f"Denied: PreToolUse hooks could not be evaluated ({type(e).__name__}: {e})"
    return True, ""


def post_tool_use(session: str, tool: str, args: dict, result: str) -> None:
    """Notify PostToolUse handlers (result truncated). Best effort, never raises."""
    try:
        fire_event(
            event="PostToolUse",
            payload={
                "session": session,
                "tool": tool,
                "args": args,
                "result": str(result or "")[:4000],
            },
        )
    except Exception:
        pass


def session_start(session: str) -> None:
    """Fire SessionStart hooks once per session per process. Never raises."""
    try:
        if not (session or "").strip() or session in _started_sessions:
            return
        if len(_started_sessions) > 1000:
            _started_sessions.clear()
        _started_sessions.add(session)
        fire_event(event="SessionStart", payload={"session": session})
    except Exception:
        pass
