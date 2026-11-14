# The SDK and the local API

Sidekick is one core with two faces: the CLI people use, and a library other
programs can build on. Both go through the same turn loop, the same tools, the
same approval gate and the same audit ledger — so a feature that lands in one
lands in the other.

## The library

```python
from sk.sdk import Agent

agent = Agent(session="docs-bot")
print(agent.turn("what does this repository do?"))
```

`Agent` keeps the conversation, so a second turn has the first one:

```python
agent = Agent(session="docs-bot")
agent.turn("what does src/sdk.py do?")
print(agent.turn("and who calls it?"))
```

A one-shot turn, without keeping anything:

```python
from sk.sdk import turn

print(turn("summarise the README", session="docs-bot"))
```

A tool, called directly — the same path an agent tool call takes, so the audit
shows it:

```python
agent = Agent(session="docs-bot")
print(agent.tool("repo_map", {"path": "src"}))
```

### Approval

An SDK call has nobody to ask, so `Agent` auto-approves by default: an SDK
turn has the same power as `sk run --yes`. Put your own gate in front of it:

```python
def approve(name, args):
    return name in ("read_file", "search_code", "repo_map")

agent = Agent(approve=approve)
agent.turn("what does this repository do?")
```

The callback gets the tool name and its arguments, and returns whether the call
may run — the same contract the CLI's approval uses.

### The audit

Every turn and every tool call lands in the same ledger the CLI writes, under
the session name you pass:

```bash
sk audit --session docs-bot
```

That is the point of the parity: an action taken by your program is
indistinguishable from one taken by the CLI, and it can be read back the same
way.

## The local HTTP surface

```bash
sk serve                  # 127.0.0.1:8787
sk serve --port 9000
sk serve --read-only      # the agent may read, but not write
```

The server prints a token at startup and writes it to
`~/.sidekick/serve.token` with owner-only permissions.

```bash
TOKEN=$(cat ~/.sidekick/serve.token)

curl -s http://127.0.0.1:8787/health \
  -H "Authorization: Bearer $TOKEN"

curl -s http://127.0.0.1:8787/turn \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "what does this repository do?", "session": "curl"}'

curl -s http://127.0.0.1:8787/tool \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "repo_map", "args": {"path": "src"}}'
```

### Why it is bound the way it is

**Loopback only.** The server refuses to bind anything that is not a loopback
address — `0.0.0.0` is refused by name. A service that can run the agent must
not be reachable from the network.

**A token, always.** Without it, a page in your browser could POST to the local
port and make the agent act. The same-origin policy stops a page from *reading*
the token, so it cannot send a valid one.

**Writes are a decision.** `--read-only` puts the approval gate in front of the
tools that change anything: the local API can read your repository, but it
cannot edit it unless you said so.

## See also

- [Checks](checks.md) — the project's own checks, and the verification gate.
- [Sandbox](sandbox.md) — what a command may reach.
- [Review](review.md) — evidence-backed review.
