# Sidekick for VS Code

A thin editor client over Sidekick's **local API** (`sk serve`). It reviews,
explains and checks the code in your buffer — it does not embed a model, hold a
provider key, or run an agent loop of its own. The daemon owns the agent, the
tools, the approval gate and the audit ledger; the extension only carries text.

## Install and connect

The extension talks to a daemon you start yourself:

```bash
# install the CLI (if you have not already)
uv tool install "sidekick-agent"

# start the local API — read-only is the recommended default
sk serve --read-only

# in another terminal, confirm it is up
TOKEN=$(cat ~/.sidekick/serve.token)
curl -s http://127.0.0.1:8787/health -H "Authorization: Bearer $TOKEN"
```

`sk serve` prints a token and writes it to `~/.sidekick/serve.token` with
owner-only permissions. The extension reads that file — there is no token or
API key setting in VS Code, so a workspace cannot supply one.

Then install the extension from source:

```bash
cd ide/vscode
npm install
npm run build
```

Press <kbd>F5</kbd> in that folder to launch an Extension Development Host, or
package it with `npx @vscode/vsce package` and install the resulting `.vsix`
with `code --install-extension sidekick-vscode-*.vsix`.

## Commands

| Command | What it does |
|---|---|
| `Sidekick: Server status` | Checks `/health` with the token and reports the version. No agent call. |
| `Sidekick: Review buffer or selection` | Asks the daemon to review the selected text (or the whole buffer) as a diff. |
| `Sidekick: Explain selection` | Asks for a concise explanation of the selection or buffer. |
| `Sidekick: Check selection` | Asks for bugs, security issues and style problems with line references. |

All answers appear in the **Sidekick** output channel. Buffer text is wrapped
in markers and labelled as data, not instructions — the same fence the CLI
uses for repo files and web pages.

## Settings

| Key | Default | Purpose |
|---|---|---|
| `sidekick.endpoint` | `http://127.0.0.1:8787` | The local daemon. Only `127.0.0.0/8`, `::1` and `localhost` are accepted; anything else is refused before a request is made. |
| `sidekick.tokenPath` | `~/.sidekick/serve.token` | File holding the daemon's token. `~` is expanded. |
| `sidekick.session` | `vscode` | Session name, so every turn lands in `sk audit --session vscode`. |
| `sidekick.timeoutMs` | `300000` | How long to wait for an answer. |

Set them in VS Code's settings UI or in `settings.json`:

```json
{
  "sidekick.endpoint": "http://127.0.0.1:8799",
  "sidekick.tokenPath": "~/.sidekick/serve.token",
  "sidekick.session": "vscode",
  "sidekick.timeoutMs": 300000
}
```

Match the port to `sk serve --port 8799`.

## Failures are explicit

- **Missing or empty token file** → "Sidekick token not found at …; run `sk serve` first."
- **Wrong token (401)** → "Sidekick rejected the token (401). Check … matches the running `sk serve`."
- **Daemon not running** → "Cannot reach Sidekick at … . Is `sk serve` running?"
- **Non-loopback endpoint** → refused before any request leaves the editor.

Every case is reported in the output channel and as an editor notification.
Nothing falls back to a remote service.

## Safety properties

- **Loopback only** — the endpoint is validated on every request; a workspace
  setting cannot point the extension at a remote host.
- **Token from the daemon** — no credential in extension settings or workspace
  files; just the 0600 file `sk serve` wrote.
- **Read-only by default** — run `sk serve --read-only` and the agent cannot
  edit the repository. If you start the daemon without that flag, the normal
  approval gate still governs every write.
- **No second implementation** — review, explain and check are prompts sent to
  `/turn`; the daemon uses the same agent loop and writes the same audit ledger
  as a CLI turn. A workspace's prompt text cannot order the extension to edit
  files, because the extension has no write tools at all.

## Developing

```bash
cd ide/vscode
npm install
npm run build       # tsc -p ./
npm run typecheck
npm test            # node --test against a local fake of sk serve
```

The transport tests use a tiny HTTP server on `127.0.0.1` and need no Ollama,
no API key and no VS Code instance. `src/client.ts` has no `vscode` import
precisely so it can be tested this way.
