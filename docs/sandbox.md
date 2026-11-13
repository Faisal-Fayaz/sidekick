# Sandbox

Sidekick has two separate planes for reaching the machine:

| Plane | Question it answers | Where it lives |
|---|---|---|
| **Permission** | May this call run at all? | the approval gate, the hard shell blocklist, the protected-path list |
| **Sandbox** | What may this call reach once it runs? | `~/.sidekick/sandbox.toml` |

They are deliberately independent. A command you approved can still be
refused by the sandbox, and a command the sandbox allows still asks for
approval. Neither plane can widen the other — and neither is a substitute for
the other.

The shell blocklist is a denylist over strings, and a denylist over shell
strings is structurally incomplete: `R=rm; $R -rf /`, `bash -c`, `$'\x72\x6d'`
all walk around it (#288). The blocklist stays — it catches the cooperative
mistake and refuses the obviously destructive command. The sandbox is the other
side of that: it decides what a process can touch.

## The policy file

`~/.sidekick/sandbox.toml` — user-owned. **Never** project-owned: a cloned
repository must not be able to widen what the agent can reach, the same reason
every other project-supplied file in Sidekick is untrusted by default.

```toml
schema = 1
mode = "enforced"          # enforced | advisory | off

[filesystem]
read_roots = ["~/src"]     # extra roots the agent may read
write_roots = []           # extra roots the agent may write
deny = ["~/.ssh", "~/.aws"]  # denied even inside an allowed root

[network]
allow = ["api.openai.com"]  # hosts the shell may reach; empty = no network
```

Without this file there is no sandbox: Sidekick behaves exactly as it did
before. The file is the opt-in.

The **project root** (the git root, or the working directory) is readable and
writable by default. Everything else has to be named.

`mode` decides what a violation does:

- `enforced` — the call is refused and the tool never runs.
- `advisory` — the call runs and the result carries a `[sandbox advisory]`
  line saying what would have been blocked. Use it to see the blast radius
  before you turn the fence up.
- `off` — the file is parsed but nothing is enforced.

## What is enforced

Everything goes through one chokepoint — the tool dispatcher — so a tool
added later cannot forget the boundary:

- **Path tools** (`read_file`, `list_dir`, `search_code`, `glob_files`,
  `repo_map`, `write_file`, `edit_file`, `delete_file`, `make_dir`) are
  checked against the roots. A path is resolved before it is checked, so a
  symlink inside the project that points out of it is refused.
- **Shell commands** (`shell`, `exec`, `shell_session`) are read as text.
  Every path and every host they mention is checked, wherever they appear —
  behind a variable, inside quotes, after a redirect. `cd /etc && cat passwd`
  reads `/etc/passwd`, and it is refused.
- **Network access** from the shell is denied unless the host is in
  `[network] allow`. Model-initiated fetches are a different policy: they are
  governed by the egress allowlist (`sk egress`), which is already
  deny-by-default.

## The OS layer

The policy above is enforced by reading the command text. That is a floor, not
a ceiling: a program that runs can do things the parser never saw. So when the
machine can enforce it, the sandbox does:

| platform | tool | what it enforces |
|---|---|---|
| Linux | `bwrap` (bubblewrap) | the declared roots are writable, everything else is read-only; no network at all unless a host is allowed |
| macOS | `sandbox-exec` (Seatbelt) | the same, as a Seatbelt profile |
| WSL2 | the Linux path | |
| native Windows | not supported | |

**It never fails closed.** bubblewrap and Seatbelt are optional; an install
without them keeps the policy enforced where it can be (the tool layer), and
`sk doctor` says the OS enforcement is *advisory*:

```bash
sk doctor     # sandbox=enforced (read=0 write=0, network=off, policy=…) · os enforcement: enforced
```

What the OS layer enforces is the **write** boundary: the declared roots are
writable, everything else is read-only. The read boundary stays with the
policy, because hiding the filesystem from a process breaks it in ways that are
hard to explain — a python that cannot see `/usr`, a git that cannot see its
config — and buys little that the policy does not already do.

Network isolation is total: when the policy allows no host, the process gets
no network at all. Selective allow-listing under a network namespace needs a
relay, which is not implemented; when hosts *are* allowed the network is left
alone and the egress policy governs it.

## What it does not reach

The shell check reads the command string. It sees the paths and hosts it can
parse, but it cannot see what a program does once it runs:

```python
python3 -c "open('/etc/shadow','w').write('x')"
```

The path is inside a string the parser cannot take apart, so this is the
documented ceiling of Phase 1. Two later phases close it:

- **Phase 2 — optional OS enforcement.** `bwrap` (Linux) or `sandbox-exec`
  (macOS), used when present. Never fail closed on a missing optional
  dependency: that would break installs.
- **Phase 3 — network-only isolation.** A network namespace with no route
  except an explicit relay.

Until then, treat the sandbox as a raised floor, not a sealed room. The
approval gate still matters: it is what stops an unattended model from
running anything at all. The OS layer above raises it further — it stops a
program that *is* running from writing outside the roots — but it cannot stop
a program from reading what the policy lets it read.

## Platform support

| Platform | Policy file | OS enforcement |
|---|---|---|
| Linux | supported | `bwrap` (bubblewrap) |
| macOS | supported | `sandbox-exec` (Seatbelt) |
| Windows | WSL2 only | the Linux path |
| native Windows | not supported | not supported |

Native Windows is out of scope, following the same precedent: WSL2 runs the
Linux path, and native Windows does not get a weaker version of the feature.

## See the state

```bash
sk doctor            # sandbox=enforced (read=1 write=0, network=on, policy=…)
sk config --show     # the same line
```

Both show the mode, so "why was my command refused" has an answer.

## Reproducing the checks

```bash
sk doctor                                  # the effective mode
sk check --trust-project-checks --yes      # the project's own checks
```

The sandbox has its own tests, including the bypass corpus from #288:

```bash
uv run pytest tests/test_sandbox.py -q
```

## See also

- [Review](review.md) — evidence-backed review, also policy-layered.
- [Checks](checks.md) — the project's own checks, trust-gated.
- [Egress policy](egress.md) — the allowlist for model-initiated fetches.
