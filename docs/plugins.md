# Plugins — user-defined tools

How user-defined tools plug into sidekick without touching code.

Status: **shipped**. This document describes what the code does. Where an
earlier draft of this file described intent, the difference is called out
explicitly under [Deliberate differences](#deliberate-differences) — the `params`
syntax in particular is not what that draft showed, and following the draft
silently produces a tool with no parameters.

## Goals

- A tool declared in a skill file behaves like a builtin: listed in
  `TOOLS_SCHEMA`, dispatched through `_gated_dispatch`, audited in `tool_runs`.
- Zero new dependencies. Unparseable declarations error clearly and never brick
  the tool.
- Hostile packs fail closed: a cloned skill repo is untrusted input, same threat
  class as `.sidekick.toml` project files (`PROJECT_BLOCKED_KEYS`).

## Non-goals

- Arbitrary code execution from packs (no Python entrypoints).
- A package manager / version solver — that is the registry issue.
- Daemon-side or TUI-specific tool surfaces (CLI/agent loop only).

## Where declarations live

`TOOLS.md` next to `SKILL.md`, loaded automatically from
`~/.sidekick/skills/*/TOOLS.md`. There is **no `--tools` flag** — an earlier
draft proposed one; auto-load won because a flag is friction for no gain when
the only source is a directory the user already controls.

## Manifest format

YAML-ish frontmatter, parsed with the existing `parse_frontmatter` (no yaml dep):

```markdown
---
tool: weather
description: Current weather for a city (keyless wttr.in).
approval: ask        # ask | auto  (auto = reads only, never writes)
kind: url-template   # url-template | shell-template
url: https://wttr.in/{city}?format=3
params: city: string required
---

Optional long-form docs for the model (capped at 2KB).
```

### `params` is an inline string, not a mapping

This is the one that bites. `params` takes a **comma-separated inline list**:

```
params: city: string required, units: string
```

A YAML block mapping does **not** work:

```yaml
params:                    # ← ignored, silently
  city: {type: string, required: true}
```

The parser only handles `params` when the value is a string, so a mapping leaves
the parameter set empty, `{city}` is never substituted, and the tool is still
listed in the schema with no required arguments. Nothing warns. Only `string`
and `integer` are recognised; `max_len` is always 500 and is not configurable
per declaration.

The shipped examples in `docs/examples/*/TOOLS.md` use the working inline form.

| Field | Rule |
|---|---|
| `tool` | `^[a-z][a-z0-9_]{1,30}$`; must not collide with a builtin name (builtins win, the pack tool is ignored with a warning) |
| `description` | ≤200 chars, shown in the prompt index |
| `approval` | `ask` (default, joins `APPROVAL_TOOLS`) or `auto` (read-only) |
| `kind` | `url-template` or `shell-template`; unknown kinds are ignored with a warning |
| `url` / `cmd` | template with `{param}` slots; params validated before substitution |
| `params` | inline `"name: type [required]"` list, comma separated |

## Entrypoints

- **`url-template`** — substitutes params, then runs the *existing*
  `tool_read_url` pipeline verbatim: the egress allowlist and SSRF guard,
  content-type whitelist, size caps, ≤3 redirects. A plugin URL fetcher can
  never be more permissive than `read_url`.

  Note that `read_url` is **egress-gated**, so a `url-template` plugin is
  refused unless its host is on `egress_allow` — see `docs/egress.md`. This is
  the same rule that applies to the builtin tool, not an extra plugin
  restriction.

- **`shell-template`** — substitutes params, then runs the *existing*
  `tool_exec` allowlist pipeline: the binary must be in `ALLOWED_BINARIES`,
  metacharacters are rejected, and `find -exec/-delete` plus non-read-only
  `git` subcommands are refused.

  An earlier draft said a command outside the allowlist "falls back to the
  `shell` tool, which keeps its approval gate + hard-refusal patterns". **It
  does not.** There is no fallback and no escalation: `tool_exec` returns its
  own `Blocked: …` error string, and that error is what the model sees. The
  command does not run. (Asserted in `tests/test_plugins.py`.)

  Shell-template tools are always `approval: ask`, whatever the manifest says.

No new executor code: both kinds are thin wrappers over existing tools.

## Sandbox rules

1. **Name fence** — `tool` names are **not** namespaced per pack. Collisions
   resolve to builtin-first, then first-installed-wins, with a warning naming
   the losers. (An earlier draft described `pack.tool` namespacing; it was
   never implemented, because the collision warning turned out to be enough.)
2. **Project-file rule** — tool declarations are never read from `.sidekick.toml`
   project files. Only `~/.sidekick/skills/`. A hostile repo cannot smuggle tool
   definitions into your session.
3. **Budget fences** — ≤10 tools per pack (`MAX_TOOLS_PER_PACK`), ≤30 packs
   (`MAX_PACKS`). Bodies are capped at 2000 chars (`MAX_BODY_CHARS`) — though
   note that only name and description reach the schema, so pack prose is not
   sent to the model at all.
4. **Secret fence** — declarations carry no secrets; keys stay in the keyring /
   env / config (`effective_api_key`).
5. **Audit parity** — plugin calls log `tool_runs` rows like builtins, so
   `sk audit` and `sk stats` cover them.

## Schema mapping and diagnostics

At load time each valid declaration compiles to one `TOOLS_SCHEMA` entry plus a
dispatcher closure over the kind pipeline above.

Invalid declarations are skipped **with a warning shown by `sk plugins`**. An
earlier draft said they surfaced via `sk config --show`; they do not — that
command only prints `cfg.project_warnings`. Use `sk plugins` to see
declarations that failed to load and why.

`--allow` applies unchanged: `weather` as a bare entry allows the whole plugin
tool.

## Deliberate differences

Where the shipped behaviour differs from the original design document, and why:

| Original | Shipped | Why |
|---|---|---|
| `params` as a YAML mapping | inline comma-separated string | no yaml dep; the mapping form fails silently, so the string form is the only honest option — **but it should warn** when handed a mapping, and does not |
| outside-allowlist shell → `shell` tool fallback | `tool_exec` returns a `Blocked:` error | an implicit escalation to a *more* powerful tool is a worse default than refusing. The refusal is visible; the old wording was not true |
| `pack.tool` namespacing | no namespacing, collision warning instead | namespacing made prompts unreadable; the warning is sufficient |
| `--tools PATH` flag | auto-load only | one source directory, no flag needed |
| warnings via `sk config --show` | warnings via `sk plugins` | `--show` is about project files; plugin warnings are a different concern |
| registry / `enabled: false` kill switch | not implemented | still open |