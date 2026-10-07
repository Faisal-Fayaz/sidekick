# MCP client: use external MCP servers as sidekick tools

Sidekick can consume [Model Context Protocol](https://modelcontextprotocol.io)
servers and expose their tools to the agent — the same extension point
opencode, Codex, Claude Code and Gemini CLI offer.

Two transports: local stdio servers (`command`) and Streamable HTTP
(`url`, with `Mcp-Session-Id` handling and SSE-response fallback).

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
| `command` | yes*     | —       | executable to spawn (stdio transport)      |
| `url`     | yes*     | —       | `http(s)://…` endpoint (Streamable HTTP)   |
| `args`    | no       | `[]`    | argv (stdio only)                          |
| `env`     | no       | `{}`    | extra env vars (stdio only, merged over the process env) |
| `headers` | no       | `{}`    | extra HTTP headers (url only)              |
| `timeout` | no       | `30`    | seconds per request, clamped to 1–300      |
| `trust`   | no       | `untrusted` | `untrusted` or `full` — see below      |
| `inherit_env` | no    | `[]`    | glob patterns of extra env vars a stdio server may also read from the parent process (#302) |

\*Exactly one of `command`/`url`. Setting both refuses the entry (fail fast —
fix the config instead of guessing).

Server names must match `[A-Za-z0-9_-]+` (no `__`); invalid entries are
skipped. Check wiring with `sk mcp-servers` (live tool count or the error).

## `trust`: why your tools are refused

This is the gate most people hit first, and it was previously undocumented —
so the error named a config key that appeared nowhere in this file.

There are **three** gates on an MCP tool call, and the first one is not
approval:

1. **`trust`** (config, default `untrusted`, fail closed)
2. **`readOnlyHint`** (tool annotation, fail closed)
3. **approval** (`--allow`, `--read-only`, `sk chat`)

On an `untrusted` server, a tool is available only if it declares a **literal**
`readOnlyHint: true`. Anything else — a missing annotation, `readOnlyHint: "true"`,
a string, an empty annotations dict — is refused, and the error tells you to set
`trust = "full"`.

```toml
[mcp_servers.db]
command = "/usr/local/bin/sqlite-mcp"
args = ["--db", "/home/you/app.db"]
trust = "full"        # tools no longer need readOnlyHint: true
```

`untrusted` is the default and the right one: a spawned server inherits the
ability to do whatever the model asks of it, and only a server you wrote should
opt out of per-tool annotation. `inherit_env` has the same shape of risk —
patterns like `AWS_*` hand a stdio server credentials from your shell — so it is
also opt-in per server.

Only `full` and `untrusted` are accepted; **anything else silently normalises to
`untrusted`** rather than erroring, so a typo fails closed.

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

### MCP and the egress allowlist

MCP servers you configure are **not** subject to the egress allowlist
([`egress.md`](egress.md)). That allowlist governs destinations the *model*
chooses — a page it asks to read, a search hit, a provider-supplied image URL.
A server URL is something you typed into your own config, alongside its trust
level, so it is treated the same way as your model provider: always reachable.

If a server is compromised or misbehaving, the gate that matters is approval —
MCP tools are asked by default like `shell`, and `--read-only` denies them. If
you want a hard network boundary for the whole process, use one at the OS level
(`unshare --net sk run …`) rather than trying to express it as host rules.

## Minimal stdio server (Python)

Any program speaking JSON-RPC 2.0 over newline-delimited stdio works. It
must answer `initialize` (protocol `2024-11-05`), accept
`notifications/initialized`, and implement `tools/list` + `tools/call`
(`{content: [{type: "text", text}], isError?}`). `tests/test_mcp_client.py`
contains a complete fake server used by the offline suite — copy its shape.
