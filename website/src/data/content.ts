export const VERSION = '0.19.0';
export const PACKAGE_NAME = 'sidekick-agent';
export const REPO_URL = 'https://github.com/Faisal-Fayaz/sidekick';
export const PYPI_URL = 'https://pypi.org/project/sidekick-agent/';

export type InstallChannel = {
  id: string;
  label: string;
  command: string;
  note: string;
};

export const INSTALL_CHANNELS: InstallChannel[] = [
  {
    id: 'uv',
    label: 'uv (recommended)',
    command: 'uv tool install sidekick-agent[voice]',
    note: 'Global `sk` binary, STT included. No clone, no build.',
  },
  {
    id: 'pipx',
    label: 'pipx',
    command: 'pipx install sidekick-agent[voice]',
    note: 'Isolated global install. Same `sk` entrypoint.',
  },
  {
    id: 'pip',
    label: 'pip',
    command: 'pip install sidekick-agent[voice]',
    note: 'Into current env. Requires Python 3.12+.',
  },
  {
    id: 'aur',
    label: 'AUR (Arch)',
    command: 'yay -S python-sidekick-agent',
    note: 'Community-maintained Arch package.',
  },
  {
    id: 'conda',
    label: 'conda-forge',
    command: 'conda install -c conda-forge sidekick-agent',
    note: 'Feedstock lives in a separate repo.',
  },
  {
    id: 'source',
    label: 'from source',
    command:
      'git clone https://github.com/Faisal-Fayaz/sidekick && cd sidekick\nuv tool install -e ".[voice]"',
    note: 'Editable dev install. Then run `sk doctor`.',
  },
];

export const QUICKSTART_STEPS = [
  {
    cmd: 'uv tool install sidekick-agent[voice]',
    comment: '# global `sk`, STT included',
  },
  { cmd: 'sk init', comment: '# guided first-run: hardware → model → verify' },
  { cmd: 'sk', comment: '# fullscreen chat — start here (`sk tui` works too)' },
];

export type CommandRow = {
  cmd: string;
  what: string;
  group: string;
};

export const COMMAND_GROUPS = [
  'Chat & Voice',
  'Runs & Jobs',
  'Memory & Todos',
  'Skills & Daemon',
  'Providers & Models',
  'System',
] as const;

export const COMMANDS: CommandRow[] = [
  { group: 'Chat & Voice', cmd: 'sk  /  sk tui [--continue]', what: 'Fullscreen chat, fresh session each launch. Streaming + themes.' },
  { group: 'Chat & Voice', cmd: 'sk chat [--continue]', what: 'Fallback plain-text REPL for dumb terminals, screen readers, broken TUIs.' },
  { group: 'Chat & Voice', cmd: 'sk talk [-d SECS] [--stt-model base]', what: 'Push-to-talk voice chat. Enter records, Enter stops. CPU transcription.' },
  { group: 'Chat & Voice', cmd: 'sk mic-test [-d SECS]', what: 'Mic level check: peak dB + silent / quiet / good verdict.' },
  { group: 'Chat & Voice', cmd: '/sessions · /resume <n> · /fork [n]', what: 'List, switch, delete, and branch past sessions.' },
  { group: 'Chat & Voice', cmd: '/readonly', what: 'Research mode: block all file writes. /confirm reverts.' },
  { group: 'Chat & Voice', cmd: '/compact [focus] · /diff · /review [base]', what: 'Fold history now, show the working-tree diff, ask the agent to review it.' },
  { group: 'Chat & Voice', cmd: '/rewind [n]', what: 'Undo an agent file edit — every write/edit/delete is snapshotted.' },
  { group: 'Chat & Voice', cmd: '/init', what: 'Scaffold SIDEKICK.md repo conventions in this directory.' },
  { group: 'Runs & Jobs', cmd: 'sk run "task" [--yes] [--read-only] [--model auto|fast|smart] [--json] [--bg]', what: 'Single-shot agent run. --json emits one machine-readable doc + exit codes.' },
  { group: 'Runs & Jobs', cmd: 'sk jobs [-n N]', what: 'List background jobs from `sk run --bg`.' },
  { group: 'Runs & Jobs', cmd: 'sk brief [-p PATH] [--smart]', what: 'Morning digest: system + git + todos + memories, instant without LLM.' },
  { group: 'Runs & Jobs', cmd: 'sk digest [--force]', what: 'Teammate pilot: brief + overnight failures, desktop nudge or log.' },
  { group: 'Runs & Jobs', cmd: 'sk history  /  sk oops', what: 'Shell log / explain the last failure.' },
  { group: 'Runs & Jobs', cmd: 'sk export [SESSION] [--out f.md]', what: 'Session transcript as Markdown (turns + tool calls).' },
  { group: 'Memory & Todos', cmd: 'sk remember / recall / memories / forget', what: 'Long-term memory with FTS5 search, auto-injected into prompts.' },
  { group: 'Memory & Todos', cmd: 'SIDEKICK.md · AGENTS.md auto-load', what: 'Repo conventions injected root-down; /init scaffolds them.' },
  { group: 'Memory & Todos', cmd: 'sk todo add / list / done / clear', what: 'Persistent todos surfaced in briefs.' },
  { group: 'Memory & Todos', cmd: 'sk audit [--session S] [--format md|json]', what: 'Compliance log: tool runs, approve/deny, local-vs-egress.' },
  { group: 'Memory & Todos', cmd: 'sk stats [--session S] [--format md|json]', what: 'Usage + cost estimates from audit rows.' },
  { group: 'Skills & Daemon', cmd: 'sk skills · skills-search · skills-install NAME', what: 'SKILL.md packs incl. registry + superpowers.' },
  { group: 'Skills & Daemon', cmd: 'sk plugins', what: 'User-defined tools via TOOLS.md.' },
  { group: 'Skills & Daemon', cmd: 'sk daemon [--once] · daemon-install [--schedule TXT]', what: 'Background watcher (systemd/launchd, calendar schedules).' },
  { group: 'Skills & Daemon', cmd: 'sk mcp [--allow-writes]', what: 'MCP server over stdio. 17 tools, safe defaults. Reads auto-run.' },
  { group: 'Skills & Daemon', cmd: 'sk mcp-servers', what: 'List configured MCP client servers + live tool check.' },
  { group: 'Skills & Daemon', cmd: 'sk hook-install [--write]', what: 'Bash/zsh shell logging hook.' },
  { group: 'Providers & Models', cmd: 'sk init  /  sk setup  /  sk connect', what: 'Guided first-run / full setup / provider key flow.' },
  { group: 'Providers & Models', cmd: 'sk model · sk auth add/list/status/remove', what: 'Live model list + default. Masked, validated keys.' },
  { group: 'Providers & Models', cmd: 'sk models [list | pull <id> | prune <id>]', what: 'Local Ollama model management.' },
  { group: 'Providers & Models', cmd: 'sk config [--show]', what: 'Settings incl. active project layer + ignored keys.' },
  { group: 'System', cmd: 'sk doctor [--fix]  /  sk report', what: 'Health (+auto-remediation) / redacted diagnostics bundle.' },
  { group: 'System', cmd: 'sk version  /  sk upgrade [--check]', what: 'Build info / self-update.' },
];

export const PROVIDERS = [
  'ollama',
  'openai',
  'groq',
  'together',
  'deepseek',
  'openrouter',
  'google',
  'lmstudio',
  'anthropic',
  'opencode',
  'custom',
];

export const FEATURES = [
  {
    icon: '$',
    title: 'Runs fully offline',
    body: 'Ollama by default. Deterministic grounding injects ~/paths, URLs and sysinfo before the model ever sees the prompt — small models stay honest.',
    tag: 'offline-first',
  },
  {
    icon: '◉',
    title: 'Voice on your CPU',
    body: 'OS-native capture (arecord / sox / ffmpeg) + local faster-whisper int8. Transcripts land editable in the prompt. Nothing leaves the machine.',
    tag: 'sk talk · ctrl+g',
  },
  {
    icon: '▚',
    title: 'Two surfaces, one brain',
    body: 'Fullscreen Textual TUI with streaming Markdown, approvals-as-cards and sessions drawer — plus a plain REPL for dumb terminals and screen readers.',
    tag: 'sk · sk chat',
  },
  {
    icon: '⚙',
    title: '17 tools, gated right',
    body: 'Reads auto-run. Writes, deletes and shell need approval. HOME//tmp only, ≤100KB, hard-refusals on rm -rf /, mkfs, fork bombs. SSRF guards on fetchers.',
    tag: 'safety gates',
  },
  {
    icon: '✦',
    title: 'Skills you can read',
    body: 'SKILL.md packs with relevance-ranked index + on-demand loading. Registry, superpowers, and user-defined TOOLS.md — no black boxes.',
    tag: 'sk skills',
  },
  {
    icon: '⎇',
    title: 'BYO key, any provider',
    body: 'One guided `sk connect` flow: numbered providers, live validation, curated models, 5-token ping. Ollama to Anthropic native API to any OpenAI-compatible URL.',
    tag: '11 presets',
  },
];

export const WHATNEW = [
  {
    tag: '/readonly',
    title: 'Research mode that cannot write',
    body: 'A read-only rung between confirm and yolo — the agent researches, every write is denied. Also `sk run --read-only`.',
    link: '/commands',
  },
  {
    tag: '/rewind',
    title: 'Undo any agent edit',
    body: 'Every write/edit/delete is snapshotted per session. Bad turn? Rewind to the exact pre-edit bytes.',
    link: '/commands',
  },
  {
    tag: '/compact · /diff · /review',
    title: 'Session command parity',
    body: 'Fold history on demand, inspect the working-tree diff, and ask the agent to review it — from inside any session.',
    link: '/docs#chat',
  },
  {
    tag: 'project memory',
    title: 'Repo conventions, auto-loaded',
    body: 'SIDEKICK.md / AGENTS.md / CLAUDE.md chain injected root-down. `/init` scaffolds one with detected facts.',
    link: '/docs#memory',
  },
  {
    tag: 'MCP client',
    title: 'Consume external MCP servers',
    body: 'Sidekick already served MCP — now it consumes it too. Server tools arrive as approval-gated `mcp__*` tools.',
    link: '/docs#mcp',
  },
];

export const COMPARISON: { label: string; sidekick: string; typical: string }[] = [
  { label: 'Runs fully offline (Ollama)', sidekick: 'yes', typical: 'no' },
  { label: 'Voice input, transcribed on CPU', sidekick: 'yes', typical: 'no' },
  { label: 'Copy/paste that works in-terminal', sidekick: 'drag-select, ctrl+y, /copy', typical: 'varies' },
  { label: 'Answers grounded in your system', sidekick: 'deterministic grounding', typical: 'prompt-only' },
  { label: 'Readable skill packs (SKILL.md)', sidekick: 'yes', typical: 'varies' },
  { label: '543-test suite incl. evals', sidekick: 'yes', typical: 'rare' },
];

export const TUI_KEYS = [
  { keys: ['Enter'], action: 'send' },
  { keys: ['ctrl+j', 'alt+enter'], action: 'newline' },
  { keys: ['↑', '↓'], action: 'history' },
  { keys: ['ctrl+y'], action: 'copy' },
  { keys: ['ctrl+g'], action: 'push-to-talk' },
  { keys: ['pgup', 'pgdn'], action: 'scroll' },
  { keys: ['F1'], action: 'help cheatsheet' },
  { keys: ['F2'], action: 'dark / light theme' },
  { keys: ['F3'], action: 'sessions drawer' },
];

export const FAQS = [
  {
    q: 'Do I need a cloud account or API key?',
    a: 'No. Install, `sk init`, pull qwen2.5-coder:7b (smarts) or llama3.2:3b (speed), and everything runs on Ollama. Keys are strictly bring-your-own and optional.',
  },
  {
    q: 'Where does my data go?',
    a: 'Nowhere by default. Files, memory (history.db), skills and voice recordings stay in ~/.sidekick/ on your machine. Recordings are temp files, deleted after each take. API keys are chmod 600 and masked in output.',
  },
  {
    q: 'Published name vs command?',
    a: 'PyPI package is `sidekick-agent` (the `sidekick` name was taken) — the binary stays `sk`. Version is a single source of truth in src/sk/__init__.py.',
  },
  {
    q: 'Which model should I run locally?',
    a: '`sk run` auto-routes: fast → llama3.2:3b, smart → qwen2.5-coder:7b. `sk brief` even shows what fits in your VRAM, e.g. qwen3:4b (2.5GB) on a 4GB card.',
  },
  {
    q: 'How do I use Claude / ChatGPT with it?',
    a: 'Two directions: `sk connect` points sidekick at any provider (OpenAI, Groq, Anthropic native, OpenCode Zen, custom URL), and `sk mcp` exposes all 17 tools to Claude Desktop over stdio. The reverse works too: `sk mcp-servers` consumes external MCP servers as approval-gated `mcp__*` agent tools.',
  },
  {
    q: 'Is it safe to let it run shell?',
    a: 'Reads auto-run; shell/writes/deletes need approval (one plan review per turn, silent only in --yes). Need zero writes? /readonly (or sk run --read-only) blocks them all, and /rewind undoes any file edit from snapshots. Shell hard-refuses rm -rf /, mkfs, dd-to-device and fork bombs even with approval. URL tools block localhost/private IPs.',
  },
];

export const ROADMAP_NEXT = [
  {
    v: 'v0.18.0',
    items: [
      '`doctor --fix` + `sk report` (#70) — auto-remediation + redacted diagnostics',
      'Release policy (#71) — minor/patch rules, changelog, pre-release checklist',
      'Contributor growth (#72) — good-first-issues, onboarding, review SLAs',
    ],
  },
];

export const ROADMAP_DONE = [
  'v0.19.0 — agent-capability batch: read-only mode (#98), session slash parity (#100), checkpoints + /rewind (#103), project memory (#105), MCP client (#107)',
  'v0.18.0 — support + adoption: doctor --fix/report (#70), release policy + guard (#71), contributor growth + good-first pipeline (#72)',
  'v0.17.0 — transparency gaps: anthropic streaming, thinking display, stale-card rejection',
  'v0.16.0 — version-bump release publishing the v0.14.0 skills set to PyPI',
  'v0.15.0 — skills compound publish (v0.14.0 tagged in code, never published)',
  'v0.14.0 — skills compound: plugin design, user tools, skill registry',
  'v0.12.0 — daemon compounds: scheduled tasks, background runs, digest',
  'v0.10.0 — bundle: sk run --json, /fork, MCP server, keyring/spend caps',
  'v0.5.0 — parallel dispatch, Anthropic caching, plan-review gate, compaction',
];

export const HERO_SCRIPT: { prompt: string; typed: string; output: string[] }[] = [
  {
    prompt: 'sk brief',
    typed: 'sk brief',
    output: [
      '╭─ sidekick brief  Sat 2026-09-19 11:58 ─╮',
      '│ CPU: AMD Ryzen 7 4800H (16 threads)     │',
      '│ Mem: 7.2Gi · GPU: GTX 1650 4GB          │',
      '│ /dev/nvme0n1p8  133G  117G  8.5G  94% / │',
      '╰─────────────────────────────────────────╯',
      '│ ! disk 94% full — clean ~/Downloads…    │',
    ],
  },
  {
    prompt: 'sk run',
    typed: 'sk run "ideal llm for my device?"',
    output: [
      '• qwen3:4b (2.5 GB): fits comfortably in your 4096 MiB VRAM.',
      '• llama3.2:3b (2.0 GB): another good option.',
    ],
  },
];
