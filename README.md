# Sidekick — local-first terminal companion

**Sidekick lives in your terminal, runs on your hardware, and remembers you.** No cloud account, no API bill — just Ollama, SQLite, and a 2–8B model on a 4GB GPU.

Built for fun as a long-term systems/CLI experiment. It talks, runs read-only commands, writes files with approval, remembers facts across sessions, tracks todos, briefs your morning, explains shell failures, and fetches the web — from a REPL, a Textual TUI, or one-shot runs.

## Demo

```console
$ sk brief
╭─ sidekick brief  Sat 2026-09-19 11:58 ─╮
│ CPU: AMD Ryzen 7 4800H (16 threads)     │
│ Mem: 7.2Gi · GPU: GTX 1650 4GB          │
│ /dev/nvme0n1p8  133G  117G  8.5G  94% / │
│ qwen3:4b · qwen2.5-coder:7b · llama3.2  │
├─ projects ──────────────────────────────┤
│ ~/ecomind  improve/async-and-js  0 changed │
╭─ warnings ──────────────────────────────╮
│ ! disk 94% full — clean ~/Downloads…    │
╰─────────────────────────────────────────╯

$ sk run "what is the ideal llm i can run on my device"
• qwen3:4b (2.5 GB): fits comfortably in your 4096 MiB VRAM.
• llama3.2:3b (2.0 GB): another good option.
# grounded in real sysinfo — never guesses

$ sk tui
sidekick online. `/help` for commands, `/model fast` for speed.
/model fast   →  model → `llama3.2:3b`
/todo add clean disk  →  Added todo #1.
/copy  →  copied last answer via xclip
```

## Features

- **Agent loop** — Ollama tool-calling (native + text-JSON fallback for coders), streaming tokens, reasoning-model aware
- **14 tools** — `sysinfo, list_dir, read_file, exec, write_file, edit_file, remember, recall, todo_add/list/done, read_url`
- **Approval gate** — reads auto-run, writes prompt `[y/N]` (or `--yes` / `/yolo`)
- **Memory + todos** — SQLite with FTS5 prefix search, auto-injected into every prompt
- **Hermes-style `/commands`** — `/help /model /clear /yolo /remember /todo /brief /history /oops /skills /copy…` in both REPL and TUI
- **Auto model router** — `sk run` picks fast (chat) vs smart (code) itself
- **Shell hook** — logs commands, `sk oops` explains the last failure
- **Daemon** — disk / failure / dirty-repo watcher with state
- **Eval harness** — 13 regression tests lock in every past quality bug fix

## Install

```bash
git clone https://github.com/Faisal01011/sidekick && cd sidekick
uv tool install -e .   # global `sk` in ~/.local/bin
sk doctor               # checks Ollama + model
```

Requires Python 3.12+ and Ollama (`ollama serve`, pull `qwen2.5-coder:7b` for smarts or `llama3.2:3b` for speed) — or any OpenAI-compatible API.

## Providers (BYO key)

```bash
sk config --provider openai --api-key sk-...          # OpenAI
sk config --provider groq --api-key gsk-...           # Groq
sk config --provider openrouter --api-key sk-or-...   # OpenRouter (incl. Claude)
/provider groq      # same switch inside chat/TUI
sk doctor           # validates key + reachability
```

Presets: `ollama|openai|groq|together|deepseek|openrouter|lmstudio|custom`. Any OpenAI-compatible endpoint works via `--provider custom --base-url https://... --api-key ...`. Preferred: `SIDEKICK_API_KEY` env (never touches disk); file keys are chmod 600 and masked in `--show`. Your old config keeps working unchanged. Note: true Anthropic-native API isn't wrapped — reach Claude via OpenRouter.

## Command reference

| Command | What |
|---|---|
| `sk chat` / `sk tui` | Interactive chat (REPL / fullscreen), `/help` inside. TUI keys: Enter sends, ctrl+j/alt+enter newline, ↑/↓ history, ctrl+y copies. Answers stream live, thinking dimmed, footer shows last-turn time/tokens |
| `sk run "task" [--yes] [--model auto\|fast\|smart\|name]` | Single-shot agent run |
| `sk brief [-p PATH] [--smart]` | Morning digest, instant without LLM |
| `sk remember/recall/memories/forget` | Long-term memory |
| `sk todo add/list/done/clear` | Todos |
| `sk history` / `sk oops` | Shell log / explain last failure |
| `sk hook-install [--write]` | Bash/zsh logging hook |
| `sk skills` / `sk daemon [--once]` | Skill packs / background watcher |
| `sk doctor` / `sk models` / `sk config` | Health / models / settings |

## Architecture

```
sk (typer CLI / Textual TUI)
 └─ slash.py — /commands (local-first, no LLM)
 └─ agent.py — Ollama loop: stream → tools → synthesize
     ├─ auto-grounding: ~/paths listed, URLs fetched, sysinfo snapshotted
     │   before the model sees the prompt — it cannot hallucinate or refuse
     ├─ tools.py — 14 tools, allowlists, SSRF guard, 100KB write caps
     ├─ store.py — SQLite: history, memories (FTS5), todos, shell log
     ├─ router.py — fast/smart pick from task text
     └─ skills/brief/daemon/clip — packs, digest, watcher, clipboard
```

Design bets that paid off: **deterministic grounding beats prompt instructions** (small models ignore rules but can't argue with injected facts), **text-JSON fallback** (coders emit tools as text over the OpenAI endpoint), **FTS5 over vectors** (zero deps, instant, no embedding server on a 4GB box).

## Tests

```bash
.venv/bin/pytest tests -q   # 69 passed, no Ollama needed
```

Unit + regression + Textual pilot tests. Suite-wide fixture guarantees tests never touch your live `~/.sidekick/config.toml` (a real bug we caught: `/model` overwrote it mid-suite).

## Config

`~/.sidekick/config.toml` (`qwen2.5-coder:7b` @ `http://localhost:11434/v1` by default). Env overrides: `SIDEKICK_MODEL`, `SIDEKICK_BASE_URL`, `SIDEKICK_API_KEY`. Data stays home: `history.db`, `skills/`, `nudges.log`.

## Safety

Reads auto-run. Writes need approval, HOME/`/tmp` only, ≤100KB, never `~/.ssh`, `~/.gnupg`, `/etc`, `/usr`. `exec` blocks `rm/sudo/pipes/redirects`. `read_url` blocks localhost/private IPs, 1MB cap.

## License

MIT
