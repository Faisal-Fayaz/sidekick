# Sidekick — local-first terminal companion you can talk to

**Sidekick lives in your terminal, runs on your hardware, and remembers you.** Local models via Ollama by default, any OpenAI-compatible API with your own key — plus push-to-talk voice that never leaves your machine.

Built for fun as a long-term systems/CLI experiment. It talks *and listens*, runs read-only commands, writes files with approval, remembers facts across sessions, tracks todos, briefs your morning, explains shell failures, searches and fetches the web, and reasons with obra/superpowers skills — from a REPL, a Textual TUI, or one-shot runs.

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
sidekick online. Enter sends · ctrl+j newline · ↑ history · ctrl+t to talk.
/model fast   →  model → `llama3.2:3b`
/todo add clean disk  →  Added todo #1.
/copy  →  copied last answer via xclip

$ sk talk
[Enter] to record, [Enter] to stop. /quit exits.
● REC — Enter to stop...
heard> what files are in the sidekick repo
# transcribed locally by faster-whisper int8, answered with streaming
```

## Features

- **Voice-first option** — `sk talk` CLI + TUI mic pill (`ctrl+t`): arecord capture, local faster-whisper STT, transcript lands editable in the prompt. `sk mic-test` diagnoses levels.
- **Agent loop** — Ollama/OpenAI-compatible tool-calling (native + text-JSON fallback for coders), streaming tokens, reasoning-model aware, repeat-call guard
- **17 tools** — `sysinfo, list_dir, read_file, exec (read-only), shell (approval), write_file, edit_file, make_dir, delete_file, remember, recall, todo_add/list/done, read_url, web_search, skill`
- **Approval gate** — reads auto-run, writes prompt `[y/N]` (or `--yes` / `/yolo`); every write backed up for `/rollback`-style recovery thinking
- **Memory + todos** — SQLite with FTS5 prefix search, auto-injected into every prompt
- **Hermes-style `/commands`** — `/help /model /provider /clear /yolo /remember /todo /brief /history /oops /skills /copy…` in REPL, TUI, and voice loop
- **Superpowers skills** — `sk skills-install superpowers` (15 obra packs); relevance-ranked index in prompt, full bodies on demand via `skill`
- **Auto model router** — `sk run` picks fast (chat) vs smart (code) itself, per provider tiers
- **Shell hook** — logs commands, `sk oops` explains the last failure
- **Daemon** — disk / failure / dirty-repo watcher with state (`--once` for cron)
- **Eval harness** — prompt-assembly + regression tests lock in every past quality bug fix

## Install

```bash
git clone https://github.com/Faisal01011/sidekick && cd sidekick
uv tool install -e ".[voice]"   # global `sk` in ~/.local/bin, STT included
sk doctor                        # checks provider + model
```

Requires Python 3.12+. Without `[voice]` you get everything except Talk/mic (installs on first use instead). Local path needs Ollama (`ollama serve`, pull `qwen2.5-coder:7b` for smarts or `llama3.2:3b` for speed).

## Providers (BYO key)

```bash
sk auth add groq            # hidden prompt, validates live before saving
sk auth status              # per-provider reachability + key health
sk auth list                # masked key overview
sk model                    # guided picker: provider → live model list
sk setup                    # wizard: provider → key → model → hook → test run
sk config --provider openai --api-key sk-...   # scriptable alternative
/provider groq              # same switch inside chat/TUI
```

Presets: `ollama|openai|groq|together|deepseek|openrouter|google|lmstudio|custom`. Any OpenAI-compatible endpoint works via `--provider custom --base-url https://... --api-key ...`. Preferred: `SIDEKICK_API_KEY` env (never touches disk); file keys are chmod 600 and masked in `--show`. Your old config keeps working unchanged. Note: true Anthropic-native API isn't wrapped — reach Claude via OpenRouter.

## Command reference

| Command | What |
|---|---|
| `sk chat` / `sk tui [--mouse on|off]` | Interactive chat (REPL / fullscreen), `/help` inside. TUI keys: Enter sends, ctrl+j/alt+enter newline, ↑/↓ history, ctrl+y copies, ctrl+t or clickable ● mic for push-to-talk, ctrl+b/f page-scroll, ctrl+home/end jump, `/mouse` toggles tracking live. Mouse on: click + wheel work, drag-select + `ctrl+y` copies selection (else last answer); hold Shift to select natively. Answers stream live, thinking dimmed, footer shows last-turn time/tokens. `/copy [n]` uses native clipboard → wl-copy/xclip/xsel → OSC52 (`sudo apt install xclip` on plain X11) |
| `sk talk [-d SECS] [--stt-model base] [--device hw:2,0]` | Push-to-talk voice chat: Enter records, Enter stops. Transcribed locally by faster-whisper int8 (installs on first run, ~800MB + model). Voice never leaves your machine |
| `sk mic-test [-d SECS]` | Check mic levels: peak dB + verdict (silent/quiet/good) with fix hints |
| `sk run "task" [--yes] [--model auto\|fast\|smart\|name]` | Single-shot agent run |
| `sk brief [-p PATH] [--smart]` | Morning digest, instant without LLM |
| `sk remember/recall/memories/forget` | Long-term memory |
| `sk todo add/list/done/clear` | Todos |
| `sk history` / `sk oops` | Shell log / explain last failure |
| `sk hook-install [--write]` | Bash/zsh logging hook |
| `sk skills` / `sk skills-install superpowers` / `sk daemon [--once]` | Skill packs (obra/superpowers) / background watcher |
| `sk doctor` / `sk models` / `sk config` | Health / models / settings |

Packs use the `SKILL.md` frontmatter format. The prompt carries a relevance-ranked index; the agent loads full instructions on demand via the `skill` tool. `fast`/`smart` resolve per provider (Ollama: llama3.2:3b/qwen2.5-coder:7b, Groq: gpt-oss-20b/120b).

## Architecture

```
sk (typer CLI / Textual TUI)
 ├─ slash.py — /commands (local-first, no LLM)
 ├─ tui.py — single-pane chat: live answer, role colors, history, mic pill
 ├─ voice.py — arecord capture (SIGINT stop), faster-whisper int8, mic levels
 ├─ agent.py — provider loop: stream → tools → synthesize
 │   ├─ auto-grounding: ~/paths listed, URLs fetched, searches run, sysinfo
 │   │   snapshotted before the model sees the prompt — it cannot hallucinate
 │   │   or refuse; repeats served from per-turn cache; greetings+dates instant
  │   ├─ tools.py — 17 tools, allowlists, shell hard-blocks, SSRF guard, 100KB write caps
 │   ├─ store.py — SQLite: history, memories (FTS5), todos, shell log
 │   ├─ router.py — fast/smart pick from task text, per provider tiers
 │   └─ skills/brief/daemon/clip — packs, digest, watcher, clipboard
```

Design bets that paid off: **deterministic grounding beats prompt instructions** (small models ignore rules but can't argue with injected facts), **text-JSON fallback** (coders emit tools as text over the OpenAI endpoint), **FTS5 over vectors** (zero deps, instant, no embedding server on a 4GB box).

## Tests

```bash
.venv/bin/pytest tests -q   # 117 passed, mic/STT subprocess calls mocked, no Ollama needed
```

Unit + regression + Textual pilot tests. Suite-wide fixture guarantees tests never touch your live `~/.sidekick/config.toml` (a real bug we caught: `/model` overwrote it mid-suite).

## Config

`~/.sidekick/config.toml` (`provider`, `model`, `base_url` override, `api_key`, …). Env overrides: `SIDEKICK_PROVIDER`, `SIDEKICK_MODEL`, `SIDEKICK_BASE_URL`, `SIDEKICK_API_KEY`. Data stays home: `history.db`, `skills/`, `nudges.log`, `input_history`, `tui-errors.log`.

## Safety

Reads auto-run. Writes, deletes, and general shell need approval, HOME/`/tmp` only, ≤100KB, never `~/.ssh`, `~/.gnupg`, `/etc`, `/usr`. `exec` blocks `rm/sudo/pipes/redirects`. `shell` hard-refuses `rm -rf /`, `mkfs`, `dd` to devices, fork bombs even with approval. `read_url`/`web_search` block localhost/private IPs (1MB cap). Mic recordings are temp files, deleted after each take. API keys chmod 600, masked in output.

## License

MIT
