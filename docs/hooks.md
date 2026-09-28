# Event hooks: automate the agent loop

Sidekick fires lifecycle events around agent work. Configure shell commands
to observe or gate them — secret-blocking, logging, desktop notifications —
the same extension point Codex, Claude Code and Gemini CLI offer.

## Configure

`~/.sidekick/config.toml` (**global file only** — repos may not define hooks;
a `[hooks]` block in `.sidekick.toml` is ignored with a warning, so cloning
a repo can never auto-run commands):

```toml
[[hooks.PreToolUse]]
command = "python3 ~/.sidekick/hooks/block_secrets.py"

[[hooks.PostToolUse]]
command = "notify-send 'sidekick' \"$TOOL ran\""
timeout = 10

[[hooks.SessionStart]]
command = "echo session started >> ~/.sidekick/hook.log"
```

| key       | required | default | meaning                             |
|-----------|----------|---------|-------------------------------------|
| `command` | yes      | —       | shell command (`sh -c`)             |
| `timeout` | no       | `30`    | seconds, clamped to 1–120           |

Events: `SessionStart` (once per session per process), `PreToolUse` (before
every tool call, may deny), `PostToolUse` (after, with truncated result).
`sk hooks` lists handlers; `sk hooks --check` dry-runs each with a sample
payload (`dry_run: true` — make handlers no-op on it).

## Protocol

The handler gets a JSON envelope on stdin:

```json
{"event": "PreToolUse", "session": "chat-1", "cwd": "/home/you/proj",
 "tool": "shell", "args": {"cmd": "curl …"}, "result": "…", "dry_run": false}
```

(`tool`/`args`/`result` only where relevant.) To decide, print JSON to
stdout: `{"decision": "allow"}` or `{"decision": "deny", "reason": "…"}`.
Exit 0 with anything else counts as allow (logging hooks stay usable).
Non-zero exit or timeout **denies** `PreToolUse` (fail closed, reason shown
to the agent and audit-logged as denied) and only warns elsewhere.

## Example: block secret exfiltration

`~/.sidekick/hooks/block_secrets.py` (chmod +x):

```python
#!/usr/bin/env python3
"""Deny shell calls that paste likely secrets. Reads the envelope on stdin."""
import json
import re
import sys

SUSPICIOUS = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bap]-)")

try:
    env = json.load(sys.stdin)
except Exception:
    print(json.dumps({"decision": "allow"}))
    raise SystemExit(0)

if env.get("event") == "PreToolUse" and env.get("tool") == "shell":
    if SUSPICIOUS.search(json.dumps(env.get("args", {}))):
        print(json.dumps({"decision": "deny", "reason": "looks like a secret — paste keys via `sk auth add` instead"}))
        raise SystemExit(0)
print(json.dumps({"decision": "allow"}))
```

```toml
[[hooks.PreToolUse]]
command = "python3 ~/.sidekick/hooks/block_secrets.py"
```
