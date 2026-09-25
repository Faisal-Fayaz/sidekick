# Plugin ecosystem design (closes #48, constrains #49)

How user-defined tools plug into sidekick without touching code.
Status: **design** — no implementation here; #49 implements against this doc.

## Goals

- A tool declared in a config/skill file behaves like a builtin: listed in
  `TOOLS_SCHEMA`, dispatched through `_gated_dispatch`, audited in `tool_runs`.
- Zero new dependencies. Unparseable declarations error clearly and never
  brick the tool (same leniency rule as `_parse_spend_cap` in `config.py`).
- Hostile packs fail closed: a cloned skill repo is untrusted input, same
  threat class as `.sidekick.toml` project files (`PROJECT_BLOCKED_KEYS`).

## Non-goals

- Arbitrary code execution from packs (no Python entrypoints in v1).
- A package manager / version solver (that's #50, the registry).
- Daemon-side or TUI-specific tool surfaces (CLI/agent loop only).

## Manifest format (`TOOLS.md` next to `SKILL.md`)

Packs declare tools in a `TOOLS.md` file in the skill dir, YAML-ish
frontmatter (parsed with the existing `parse_frontmatter`, no yaml dep):

```markdown
---
tool: weather
description: Current weather for a city (keyless wttr.in).
approval: ask        # ask | auto  (auto = reads only, never writes)
kind: url-template   # url-template | shell-template (v1 kinds)
url: https://wttr.in/{city}?format=3
params:
  city: {type: string, required: true, max_len: 80}
---

Optional long-form docs for the model (capped at 2KB, like skill bodies).
```

Field rules:

| Field | Rule |
|---|---|
| `tool` | `^[a-z][a-z0-9_]{1,30}$`, must not collide with builtin names (builtins win, pack tool ignored with a warning) |
| `description` | ≤200 chars, shown in the prompt index |
| `approval` | `ask` (default, joins `APPROVAL_TOOLS`) or `auto` (read-only; any write-shaped kind forces `ask`) |
| `kind` | v1: `url-template` or `shell-template` only; unknown kinds ignored with a warning |
| `url` / `cmd` | template with `{param}` slots; params validated before substitution |
| `params` | JSON-Schema-subset (`type: string|integer`, `required`, `max_len`); unknown keys ignored |

## Entrypoints (v1 kinds)

- **`url-template`** — substitutes params, then runs the *existing*
  `tool_read_url` pipeline verbatim: SSRF guard (`_url_blocked`),
  content-type whitelist, 1MB/15K caps, `max_redirects=3`. A plugin URL
  fetcher can never be more permissive than `read_url`.
- **`shell-template`** — substitutes params, then runs the *existing*
  `tool_exec` allowlist pipeline (`_check_cmd`): binary must be in
  `ALLOWED_BINARIES`, metachars rejected, `find -exec/-delete` and
  non-read-only `git` subcommands refused. Anything outside the allowlist
  falls back to the `shell` tool, which keeps its approval gate +
  hard-refusal patterns. Shell-template tools are always `approval: ask`.

No new executor code: both kinds are thin wrappers over existing tools,
so every property test in `tests/test_properties.py` covers plugins free.

## Sandbox rules (untrusted packs)

1. **Name fence** — `tool` names are namespaced per pack internally
   (`pack.tool`); collisions with builtins or other packs resolve to
   builtin-first, then first-installed-wins, with a warning listing losers.
2. **Project-file rule** — like `PROJECT_BLOCKED_KEYS`, tool declarations
   are *never* read from `.sidekick.toml` project files (global
   `~/.sidekick/skills/` + explicit `--tools` paths only). A hostile repo
   cannot smuggle tool definitions into your session.
3. **Budget fences** — ≤10 tools per pack, ≤30 packs total (mirrors
   `MAX_FILES`/30-pack caps in `skills.py`); bodies >2KB truncated.
4. **Secret fence** — declarations carry no secrets; keys stay in keyring /
   env / config (`effective_api_key`), never in pack files.
5. **Audit parity** — plugin calls log `tool_runs` rows with provider/host
   like builtins, so `sk audit` / `sk stats` cover them.

## Schema mapping

At startup the loader compiles each valid declaration to one
`TOOLS_SCHEMA` entry (`type: function`, `function: {name, description,
parameters}`) plus a dispatcher closure over the kind pipeline above.
Invalid declarations are skipped with a warning surfaced by
`sk config --show`-style diagnostics (exact command: #49's call).

`--allow` (#40) applies unchanged: `weather` as a bare entry allows the
whole plugin tool; `shell:` scopes keep working because shell-template
calls route through the `shell` tool identity.

## Open questions for #49

1. `--tools PATH` flag vs auto-load from `~/.sidekick/skills/*/TOOLS.md` —
   recommendation: auto-load (zero friction), flag for ad-hoc files.
2. Should `approval: auto` plugin tools appear in the prompt index by
   default, or only on relevance like `SKILL.md` bundles? (Prompt-budget
   pressure grows with every tool.)
3. Registry (#50) signature story: checksum pinning for remote packs —
   defer to #50, local packs need nothing.
4. Per-pack kill switch (`enabled: false`) — cheap, include in #49?
