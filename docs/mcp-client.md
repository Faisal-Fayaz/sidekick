# MCP client: use external MCP servers as sidekick tools

Sidekick can consume [Model Context Protocol](https://modelcontextprotocol.io)
servers and expose their tools to the agent — the same extension point
opencode, Codex, Claude Code and Gemini CLI offer.

**v1 scope: local stdio servers only.** Remote/streamable transports are out
of scope (a stdio bridge like `mcp-remote` works if you need one).

## Configure

`~/.sidekick/config.toml` (global file **only** — repos may not configure
servers; a `[mcp_servers.*]` block in `.sidekick.toml` is ignored with a
warning, so cloning a repo can never auto-spawn processes):

```toml
[mcp_servers.files]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
timeout = 30

[mcp_servers.db]
command = "/usr/local/bin/sqlite-mcp"
args = ["--db", "/home/you/app.db"]
env = { SQLITE_MCP_RO = "1" }
```

| key       | required | default | meaning                                    |
|-----------|----------|---------|--------------------------------------------|
| `command` | yes      | —       | executable to spawn                        |
| `args`    | no       | `[]`    | argv                                       |
| `env`     | no       | `{}`    | extra env vars (merged over the process env) |
| `timeout` | no       | `30`    | seconds per request, clamped to 1–300      |

Server names must match `[A-Za-z0-9_-]+` (no `__`); invalid entries are
skipped. Check wiring with `sk mcp-servers` (live tool count or the error).

## How tools appear

Each server tool becomes `mcp__<server>__<tool>` with the server's
description, e.g. `mcp__files__read_file`. Behavior:

- **Approval-gated like `shell`**: asked by default, `--allow
  mcp__files__read_file` skips prompts, `/yolo` auto-approves, `/readonly`
  and `sk run --read-only` deny.
- Calls are audit-logged (`sk audit`) like builtins.
- A dead/slow server never stalls a turn: failures degrade to tool errors
  and cool down briefly; the turn continues with other tools.
- Proxied tools are **not** re-served by `sk mcp` (no recursive serving).

## Minimal stdio server (Python)

Any program speaking JSON-RPC 2.0 over newline-delimited stdio works. It
must answer `initialize` (protocol `2024-11-05`), accept
`notifications/initialized`, and implement `tools/list` + `tools/call`
(`{content: [{type: "text", text}], isError?}`). `tests/test_mcp_client.py`
contains a complete fake server used by the offline suite — copy its shape.
