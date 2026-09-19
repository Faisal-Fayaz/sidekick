# Sidekick — local terminal companion (MVP)

Local-first agent that lives in your terminal. Talks to Ollama, uses tools with approval.

## Quickstart

```bash
cd ~/sidekick
uv tool install -e .  # global `sk` in ~/.local/bin
sk doctor
sk run "summarize disk usage in ~/"
sk chat
# writes need approval:
sk run "create /tmp/demo.txt with 'hi'"        # prompts [y/N]
sk run "create /tmp/demo.txt with 'hi'" --yes  # auto-approve
sk chat --yes
# models: fast = llama3.2:3b (instant), smart = qwen2.5-coder:7b (default, slower)
sk run "say hi" --model fast
sk run "refactor script" --model smart --no-stream
```

Streaming is on by default (live tokens). Use `--no-stream` for clean markdown only.
In chat, use `@path/to/file` to inline a file. Type `exit` to quit.

## Tests

```bash
.venv/bin/pytest tests -q  # 12 passed, no Ollama needed
```

## Memory

```bash
sk remember "prefers llama3.2:3b for quick answers"
sk recall "model"
sk memories
sk forget "llama"
```
Memories use FTS5 prefix search (stdlib, no deps) + keyword fallback, auto-inject top-5. True vectors (`sqlite-vec` + `nomic-embed-text`) deferred: disk 94% full + Ollama needs `--embeddings` restart.

## Todos + brief

```bash
sk todo add "clean disk"
sk todo list
sk todo done 1
sk todo clear
sk brief
sk brief -p ~/sky-duel --smart
```

## Chat + TUI (Hermes-style /commands)

```bash
sk chat
sk tui
sk tui --model fast
```
Single chat view. Everything via slash: `/help /model /clear /yolo /confirm /remember /recall /memories /forget /todo /brief /history /oops /skills /models /exit`. Same commands work in `sk chat` and `sk tui`. Anything else goes to the agent.

## Skills + daemon

```bash
sk skills
echo '# deploy
- never push on fridays' > ~/.sidekick/skills/deploy.md
sk daemon --once
sk daemon --interval 300   # foreground loop, log ~/.sidekick/nudges.log
```

## Web

```bash
sk run "summarize https://example.com in one line"
```
`read_url` fetches public http/https only (blocks localhost/private IPs, 1MB cap, strips scripts). Stdlib HTML extract, no extra deps beyond httpx.

## Config

`~/.sidekick/config.toml` — defaults to `qwen2.5-coder:7b` on `http://localhost:11434/v1`.

```bash
sk config --show
sk config --model qwen3:4b
sk models
```

Env overrides: `SIDEKICK_MODEL`, `SIDEKICK_BASE_URL`, `SIDEKICK_API_KEY`.

## Safety

Read tools auto-run: `sysinfo, list_dir, read_file, exec(read-only)`.
Write tools require approval: `write_file, edit_file` (HOME or /tmp only, max 100KB, blocks `~/.ssh, ~/.gnupg, /etc, /usr`).
`exec` blocks: `rm, sudo, pipes, redirects, ; &&`.

## Layout

- `src/sk/cli.py` — typer commands + approval prompts
- `src/sk/agent.py` — Ollama tool loop + auto ~/ grounding + approval gate
- `src/sk/tools.py` — sysinfo, list_dir, read_file, exec + write_file/edit_file
- `src/sk/store.py` — sqlite history
- `src/sk/config.py` — toml config
```
