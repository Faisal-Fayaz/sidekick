# Platform support

Sidekick is a **local-first POSIX tool**. This page states the decision so it
can be checked rather than guessed.

## Supported today

| OS | Python | Status |
|---|---|---|
| ubuntu-latest (Linux) | 3.12, 3.13, 3.14 | Supported; CI matrix leg |
| macos-latest (macOS) | 3.12, 3.13, 3.14 | Supported; CI matrix leg |

These are the only combinations in `.github/workflows/ci.yml`'s `test` matrix.
Other Linux distributions that provide the same POSIX interfaces are expected
to work, but are not separately tested. **WSL2 is Linux** for this purpose:
run Sidekick inside the WSL2 distribution, not from Windows Python.

## Windows: a non-goal (decided 2026-11-20)

The open core does **not** support Windows as a host. This is an explicit
decision, not "best effort". Concrete blockers:

- **Shell and command syntax.** Tool checks and the shell gate assume `sh -c`,
  POSIX paths and POSIX quoting (`src/sk/tools/shell.py`, the sandbox policy).
- **Permissions.** API keys, `serve.token`, the audit database and the config
  directory are protected with `chmod 0600`/`0700` (`src/sk/fsperm.py`). Windows
  ACLs are a different model; a direct port would silently weaken them.
- **Sockets.** `sk serve` and the sandbox relay bind loopback **Unix domain
  sockets** (`src/sk/serve.py`, `src/sk/relay.py`). Named pipes are a different
  transport with different authentication properties.
- **OS sandbox.** Enforcement uses bubblewrap on Linux and Seatbelt on macOS
  (`src/sk/sandbox_os.py`); neither exists on Windows.
- **CI and distribution.** There is no Windows matrix leg, and the published
  channels (PyPI wheel/packaging docs, AUR) are POSIX-oriented.
- **Paths.** `~` expansion, `/tmp` write roots and path-blocklist rules assume
  POSIX separators and roots throughout the tool layer.

Supporting Windows would mean redesigning those pieces, not adding a CI leg.
The decision is revisited only with a dedicated porting milestone; until then,
Windows issues are closed as out of scope with a link here.

## What works on Windows today

Nothing is claimed. Running the CLI under Windows Python may fail in
non-obvious ways because the POSIX assumptions above are unchecked. Use WSL2.

## Keeping this honest

`tests/test_platform_support.py` compares the OS/Python matrix in this file
with `.github/workflows/ci.yml`. If a Windows job is added without updating
this page, the test fails. The same test asserts that the non-goal statement
and every named blocker remain present, so the decision cannot be quietly
forgotten.

## See also

- [Sandbox](sandbox.md) — the OS layer that has no Windows equivalent.
- [The SDK and the local API](sdk.md) — the Unix-socket local surface.
- [Threat model](threat-model.md) — the trust boundaries a port would have to
  preserve.
