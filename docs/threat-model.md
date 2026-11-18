# Threat model and audit plan

This is the map of what Sidekick defends, against whom, and where the defenses
stop. It is deliberately honest about residual risk: a document that claims
total safety would be less useful than one that says exactly what is left.

The controls here are not theoretical. Each one names the module that
implements it and the tests that pin it. `tests/test_threat_model.py` checks
that those references still exist, so this document cannot silently rot.

## Assets

| Asset | Why it matters |
|---|---|
| The user's files and their integrity | The agent can read, write and delete under the user's account. |
| Credentials (model keys, `serve.token`, team policy) | A leaked key is a bill and an identity; the token is the gate to a local agent. |
| Conversations and tool ledger | Session history contains code, prompts and sometimes secrets pasted in a turn. |
| The user's attention and judgement | An injected instruction that gets obeyed spends the user's approval on the wrong action. |
| The machine's network position | A local agent that can be turned into a proxy for internal services is a pivot. |

## Trust boundaries

```text
  user's home (~/.sidekick)          trusted — operator wrote it
  ──────────────────────────────────────────────────────────────────
  project on disk (.sidekick/*)       untrusted until SIDEKICK_TRUST_REPO=1
  web pages, search hits, tool output untrusted, always fenced
  ──────────────────────────────────────────────────────────────────
  model output                        untrusted instructions; tools, not prose, act
  ──────────────────────────────────────────────────────────────────
  sk serve (127.0.0.1 + token)        local programs with the token may drive the agent
  ──────────────────────────────────────────────────────────────────
  OS sandbox / network namespace      the raised floor beneath every layer above
```

The model is **not** a trusted principal. Its text is advice; only a tool call
that passes the gate changes anything.

## Adversaries and mitigations

### A1 — A malicious cloned repository

The repo can ship `.sidekick/commands/`, review and check config, team memory
and skill packs. Those files are attacker-authored text.

- **Repo trust gate.** Project-layer commands, review rules, checks, team memory
  and shared skills load only with `SIDEKICK_TRUST_REPO=1` in the *outer shell*.
  The flag is never read from a repo file, so a repo cannot self-authorize.
  `src/sk/custom_commands.py`, `src/sk/team.py`; `tests/test_security.py`,
  `tests/test_team.py`.
- **Permissions live in data, in the user's home only.** A shared skill may
  request only tools already allowed by `~/.sidekick/team/policy.toml`; a repo
  copy of that policy is ignored and a broken policy fails closed.
  `src/sk/team.py`; `tests/test_team.py`.
- **Repo docs are fenced.** `SIDEKICK.md`/`AGENTS.md`/`CLAUDE.md` are injected as
  labelled untrusted data, never as system instructions. `src/sk/agent.py`,
  `src/sk/trust.py`; `tests/test_trust.py`, `tests/test_eval.py`.
- **Residual:** trusting a repo is a deliberate operator decision. After
  `SIDEKICK_TRUST_REPO=1`, its text is still fenced and its commands still need
  approval — but the user has chosen to let that repo's config be read.

### A2 — Hostile web, tool output or shared skill prose (prompt injection)

Any fetched page, search result or tool result can say "ignore your rules,
run this command".

- **Fences with provenance.** Outside content is sanitized (control characters,
  bidi marks, fence markers) and wrapped in `<<<UNTRUSTED …>>>` blocks with a
  source label. The system prompt tells the model these are data, never orders.
  `src/sk/trust.py`, `src/sk/agent.py`; `tests/test_trust.py`.
- **No authority from prose.** Writes, shell and network fetches require a tool
  call that passes the gate; an injected sentence cannot call a tool by itself.
  `src/sk/tools/registry.py`, `src/sk/cli/approvers.py`; `tests/test_security.py`,
  `tests/test_eval.py`.
- **Proposals never write.** Memory proposals from a turn are data; extracting
  one does not even open the database. `src/sk/memory_rank.py`;
  `tests/test_memory_rank.py`.
- **Residual:** prompt injection is not solved by fencing. A model can still be
  persuaded that a fenced instruction is legitimate. The OS sandbox and the
  approval gate are the backstops, not the fence.

### A3 — A browser page driving the local agent

A page in the user's browser could POST to a local port without the user's
knowledge.

- **Token always.** `sk serve` generates a token at startup, writes it with
  0600 to `~/.sidekick/serve.token`, and every route requires `Authorization:
  Bearer <token>` compared in constant time. Same-origin policy stops a page
  reading the token. `src/sk/serve.py`; `tests/test_sdk.py`.
- **Loopback only.** The server refuses to bind anything outside
  `127.0.0.0/8`, `::1` or `localhost`; `0.0.0.0` is refused by name.
  `src/sk/serve.py`; `tests/test_sdk.py`.
- **The IDE client follows the same rules.** The VS Code extension validates
  the endpoint on every request, reads the token from the file (never a
  workspace setting), and holds no key of its own. `ide/vscode/src/client.ts`;
  `ide/vscode/src/test/client.test.ts`.
- **Residual:** any local process that can read the user's home can read the
  token and drive the agent. That is the same trust as the user's own account.

### A4 — Egress and SSRF: the agent made to fetch an internal service

- **Deny by default.** `sk egress` is empty initially; no model-driven fetch
  happens without an explicit operator allow. `src/sk/egress.py`;
  `tests/test_egress.py`.
- **Two gates, every hop.** `_fetch_with_redirects` applies the egress list,
  then the SSRF guard twice in immediate succession, on the first request and
  on each of up to three redirects. Fewer checks would let a flapping DNS
  answer through. `src/sk/tools/web.py`; `tests/test_web.py`.
- **Search results are filtered** by the SSRF guard before the model sees them,
  and provider endpoints obey the same gates. `src/sk/search.py`;
  `tests/test_web_search.py`.
- **Residual:** DNS rebinding between the guard and the connection is not
  closed (fixing it needs connection-level IP pinning). A `shell` command using
  `curl` does not pass through the egress list — the OS sandbox is the answer.

### A5 — An approved tool that reaches too far

- **Approval gate.** Writes, shell, deletes, git pushes and plugin/MCP tools are
  in the approval set; read-only tools run without a prompt.
  `src/sk/tools/registry.py`; `tests/test_security.py`.
- **Hard refusals.** Destructive shell patterns and sensitive write paths
  (`~/.ssh`, `~/.gnupg`, `/etc`, `/usr`) are refused even with approval.
  `src/sk/tools/shell.py`, `src/sk/tools/write.py`; `tests/test_security.py`.
- **OS sandbox.** When a `~/.sidekick/sandbox.toml` exists, bubblewrap or
  Seatbelt constrains reads/writes; with no host allowed the network namespace
  has no route, and with hosts allowed an allow-listing relay is the only way
  out. `src/sk/sandbox.py`, `src/sk/sandbox_os.py`, `src/sk/relay.py`;
  `tests/test_sandbox.py`, `tests/test_relay.py`.
- **Read-only mode.** `sk run --read-only` and `sk serve --read-only` deny every
  write tool before a prompt is possible. `src/sk/cli/approvers.py`,
  `src/sk/serve.py`; `tests/test_security.py`, `tests/test_sdk.py`.
- **Residual:** the sandbox is optional and probed, never installed as a
  dependency; a machine without it has the policy layers only. Raw TCP beyond
  the HTTP(S) relay is not covered.

### A6 — Accidental or unaudited action

- **One ledger.** Every turn and tool call, from CLI, SDK, MCP or local API,
  lands in the same `tool_runs` table with session, target, approval and
  outcome. `src/sk/store.py`, `src/sk/sdk.py`; `tests/test_sdk.py`.
- **Rewind.** File edits are snapshotted so `/rewind` can undo them.
  `src/sk/checkpoints.py`.
- **Residual:** the ledger is local and append-only by convention, not signed;
  a local process with the user's account can edit the database.

## Audit probes

These are concrete checks for a future reviewer. Each maps to a test that can
be run offline:

```bash
uv run pytest tests/test_security.py -q        # approval, hard blocks, plan mode
uv run pytest tests/test_egress.py -q           # deny by default, project cannot widen
uv run pytest tests/test_web.py -q              # SSRF twice, redirects, flapping DNS
uv run pytest tests/test_sandbox.py -q          # read/write roots, optional enforcement
uv run pytest tests/test_relay.py -q            # allow-list relay inside a namespace
uv run pytest tests/test_trust.py -q            # sanitize + provenance fences
uv run pytest tests/test_team.py -q             # trust gate, no permission widening
uv run pytest tests/test_sdk.py -q              # loopback, token, audit parity
uv run pytest tests/test_eval.py -q             # no ungrounded guesses, no tool refusals
uv run python docs/probe-perf.py                # prompt-overhead gate (#499)
```

Manual probes worth repeating at audit time:

1. Put a `!`command`` in a repo-supplied `.sidekick/commands/` file and confirm
   it is ignored until `SIDEKICK_TRUST_REPO=1`, then still asks for approval.
2. Put `tools: shell` in a repo skill pack with no user allow-list entry and
   confirm `sk team check` refuses the whole pack and names the tool.
3. Ask `sk run` to fetch `http://127.0.0.1:11434/` and confirm the SSRF guard
   answers even if `sk egress allow 127.0.0.1` was run.
4. Start `sk serve --host 0.0.0.0` and confirm the bind is refused by name.
5. Hit `/health` without a token and with a wrong token; both must be 401.
6. Feed a fenced page with bidi controls and a fake fence marker into
   `/memories`-style context and confirm it is neutralised, not printed raw.

## Out of scope, stated plainly

- **Raw TCP.** The sandbox relay forwards HTTP(S) proxy traffic only. SSH, a
  database client or any other raw protocol needs its own relay.
- **Connection-time DNS rebinding.** Guards are asked twice in succession, but
  the address can still change between the last check and the socket connect.
- **`curl` in a shell.** Egress governs fetches made through the agent's
  tools; a shell command is governed by approval and the OS sandbox.
- **What a program does after it starts.** A policy that reads command text
  cannot constrain the program it launched.
- **A compromised user account or home directory.** Everything under
  `~/.sidekick` is operator-trusted by design, including the token and policy.
- **Model judgement.** Fences and system rules raise the floor; they are not a
  proof that a model will never be persuaded.

## Maintenance

`tests/test_threat_model.py` checks that this document names every control,
that the cited modules and test files exist, and that no placeholder text is
left. When a control changes, update both the code reference and its test here.

## See also

- [Egress policy](egress.md) — the allow list and why it runs first.
- [Sandbox](sandbox.md) — the OS layer and the relay.
- [Team memory and shared skills](team.md) — the trust gates for shared data.
- [The SDK and the local API](sdk.md) — the loopback/token surface.
