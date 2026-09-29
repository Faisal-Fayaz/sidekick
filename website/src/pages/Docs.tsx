import { useEffect, useMemo, useState } from 'react';
import { CodeBlock, SectionHeading } from '../components/ui';

const SECTIONS = [
  { id: 'getting-started', label: 'Getting started' },
  { id: 'chat', label: 'Chat (TUI + REPL)' },
  { id: 'voice', label: 'Voice' },
  { id: 'providers', label: 'Providers (BYO key)' },
  { id: 'mcp', label: 'MCP server + client' },
  { id: 'memory', label: 'Memory · todos · sessions' },
  { id: 'skills', label: 'Skills & plugins' },
  { id: 'daemon', label: 'Daemon & jobs' },
  { id: 'config', label: 'Config' },
  { id: 'safety', label: 'Safety' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'tests', label: 'Tests & contributing' },
  { id: 'troubleshooting', label: 'Troubleshooting' },
];

const inline = (t: string) => <code className="inline">{t}</code>;

export default function Docs() {
  const [active, setActive] = useState('getting-started');
  const [q, setQ] = useState('');

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return SECTIONS;
    return SECTIONS.filter((s) => s.label.toLowerCase().includes(needle) || s.id.includes(needle));
  }, [q]);

  useEffect(() => {
    const onScroll = () => {
      let current = SECTIONS[0].id;
      for (const s of SECTIONS) {
        const el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top < 140) current = s.id;
      }
      setActive(current);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const go = (id: string) => {
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="wrap" style={{ paddingTop: 36 }}>
      <SectionHeading
        prompt="$ man sidekick --"
        title="Docs"
        sub="The whole manual on one page: install once, then live in sk. Mirrors the README — if the CLI and this page ever disagree, the CLI wins (sk --help)."
      />
      <div className="docs-layout">
        <aside className="docs-side">
          <input
            className="search"
            placeholder="$ grep docs…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <div className="grp">manual</div>
          {visible.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className={active === s.id ? 'on' : ''}
              onClick={(e) => {
                e.preventDefault();
                go(s.id);
              }}
            >
              ./{s.id}
            </a>
          ))}
        </aside>

        <article className="doc-article">
          <h2 id="getting-started">Getting started</h2>
          <p>
            Install from PyPI ({inline('sidekick-agent')}, binary stays {inline('sk')}), run the wizard, start
            chatting. No clone, no build. Requires Python 3.12+.
          </p>
          <CodeBlock
            code={`uv tool install sidekick-agent[voice]   # global \`sk\`, STT included\nsk init                                  # hardware → model → verify\nsk                                       # fullscreen chat — start here`}
          />
          <p>
            Without {inline('[voice]')} you get everything except Talk/mic. Local path needs Ollama:{' '}
            {inline('ollama serve')}, pull {inline('qwen2.5-coder:7b')} for smarts or {inline('llama3.2:3b')}{' '}
            for speed. From source: clone + {inline('uv tool install -e ".[voice]"')} then {inline('sk doctor')}.
          </p>

          <h2 id="chat">Chat (TUI + REPL)</h2>
          <p>One input, two surfaces — fullscreen TUI and plain-text REPL share every command:</p>
          <CodeBlock code={`sk                   # fullscreen chat with streaming + themes — start here\nsk tui --model fast  # same, explicit form\nsk chat              # fallback REPL: dumb terminals, screen readers, broken TUIs`} />
          <p>
            Type {inline('/')} and an autocomplete popup filters all 30+ commands — Enter completes, Tab too,
            Esc dismisses, ↑/↓ navigates. {inline('F1')} opens a generated cheatsheet (keys + commands, built
            from the same tables as the dispatcher, so it can’t rot).
          </p>
          <p>
            TUI keys: <b>Enter</b> sends · <b>ctrl+j</b>/<b>alt+enter</b> newline · <b>↑/↓</b> history ·{' '}
            <b>ctrl+y</b> copies · <b>ctrl+g</b> push-to-talk · <b>pgup/pgdn</b> scroll · <b>F1</b> help ·{' '}
            <b>F2</b> theme picker · <b>F3</b> sessions drawer · <b>F4</b> plan mode · <b>F5</b> model
            picker. Answers stream live as Markdown with role colors; approvals arrive as cards with timeout;
            the status bar shows model · session · last-turn time/tokens.
          </p>

          <h2 id="voice">Voice</h2>
          <CodeBlock code={`sk talk [-d SECS] [--stt-model base] [--device hw:2,0]  # Enter records, Enter stops\nsk mic-test                                             # peak dB + verdict`} />
          <p>
            Capture via the OS-native recorder (arecord/ALSA on Linux, sox/ffmpeg on macOS), transcription via
            local faster-whisper int8, transcript lands editable in the prompt. In the TUI, {inline('ctrl+g')}{' '}
            (or the mic pill) does the same. Voice never leaves your machine; recordings are temp files, deleted
            after each take.
          </p>

          <h2 id="providers">Providers (BYO key)</h2>
          <CodeBlock code={`sk connect     # pick provider → paste key (hidden) → pick model → ping. Done.`} />
          <p>
            One guided flow: numbered provider list (local ones skip keys), live validation <i>before</i>{' '}
            anything saves, curated model list, and a 5-token ping instead of a full agent turn. Advanced paths:{' '}
            {inline('sk auth add/list/status/remove')}, {inline('sk model')}, {inline('sk setup')} (connect +
            hook), {inline('sk config --provider openai --api-key …')}, {inline('/provider groq')} inside chat.
          </p>
          <p>
            Presets: {inline('ollama|openai|groq|together|deepseek|openrouter|google|lmstudio|anthropic|opencode|custom')}{' '}
            (anthropic speaks the native Messages API; the rest are OpenAI-compatible). Switch mid-session
            without typing names: <b>F5</b> pops a model picker covering every usable provider (current ●,
            fast/smart, live lists — selecting auto-switches provider too; filter, Enter switches, Esc dismisses). Any OpenAI-compatible
            endpoint works via {inline('--provider custom --base-url https://…')}. Preferred:{' '}
            {inline('SIDEKICK_API_KEY')} env (never touches disk); file keys are chmod 600 and masked in{' '}
            {inline('--show')}.
          </p>
          <p>
            The {inline('opencode')} preset points at OpenCode Zen with free, tools-capable models. The anonymous
            free tier is restricted to its own app — add a free OpenCode account key first (
            {inline('OPENCODE_API_KEY')} or {inline('sk auth add opencode')}). Fast/smart resolve to{' '}
            {inline('nemotron-3.5-lightning-free')} / {inline('muse-spark')}; run{' '}
            {inline('sk models opencode')} for the live list.
          </p>

          <h2 id="mcp">MCP server + client</h2>
          <CodeBlock code={`sk mcp [--allow-writes]   # JSON-RPC 2.0 over stdio, zero new deps`} />
          <p>
            All 19 tools, same safety policy (SSRF guards, write blocklists, hard-refusals). Reads auto-run;
            shell/writes/delete need {inline('--allow-writes')}, else a clean denied error. Stdout carries
            protocol only. Claude Desktop snippet:
          </p>
          <CodeBlock code={`{ "mcpServers": { "sidekick": { "command": "sk", "args": ["mcp"] } } }`} />
          <p>
            The reverse direction works too: {inline('sk mcp-servers')} consumes external stdio MCP servers
            (configured under {inline('[mcp_servers.*]')} in the global config file) as approval-gated{' '}
            {inline('mcp__*')} agent tools — asked by default, denied in read-only mode, audit-logged like
            builtins. Remote servers join the same way: an {inline('[mcp_servers.name]')} section with{' '}
            {inline('url=')} (Streamable HTTP, optional {inline('headers=')}) instead of {inline('command=')}.
            See {inline('docs/mcp-client.md')}.
          </p>

          <h2 id="memory">Memory · todos · sessions</h2>
          <CodeBlock
            code={`sk remember "deploy runs on Fridays"   # long-term memory (FTS5, auto-injected)\nsk recall "deploy"  /  sk memories  /  sk forget <id>\nsk search "deploy friday" [--session S]  # full-text transcript search\nsk todo add "fix flaky eval"  /  sk todo list  /  sk todo done 1\n/init                                    # scaffold SIDEKICK.md repo conventions here\nSIDEKICK.md · AGENTS.md · CLAUDE.md      # auto-loaded root-down, merged with .sidekick.toml docs\n/sessions  ·  /resume <n>  ·  /fork [n]   # list, switch, branch past sessions\n/rewind [n]  ·  /compact [focus]          # undo a file edit · fold history now\n/diff  ·  /review [base]                 # show working-tree diff · ask the agent to review it\nsk export --out session.md     # transcript as Markdown\nsk audit --format md           # compliance log   ·   sk stats  # usage + cost`}
          />

          <h2 id="skills">Skills & plugins</h2>
          <p>
            Packs use the {inline('SKILL.md')} frontmatter format. The prompt carries a relevance-ranked index;
            the agent loads full instructions on demand via the {inline('skill')} tool.{' '}
            {inline('fast')}/{inline('smart')} resolve per provider (Ollama: llama3.2:3b/qwen2.5-coder:7b, Groq:
            gpt-oss-20b/120b).
          </p>
          <CodeBlock
            code={`sk skills                    # list installed packs\nsk skills-search "pdf"       # search registry\nsk skills-install NAME       # install a pack\nsk plugins                   # user-defined tools (TOOLS.md, see docs/plugins.md)`}
          />
          <p>
            Custom slash commands turn Markdown files into first-class {inline('/commands')}: drop a file in{' '}
            {inline('~/.sidekick/commands/')} (global) or {inline('.sidekick/commands/')} (project, overrides
            global) with an optional description frontmatter and a {inline('{{args}}')} (or{' '}
            {inline('$ARGUMENTS')}) template — shared, reviewable, no code. Builtins win name collisions;{' '}
            {inline('/help')} lists yours separately.
          </p>

          <h2 id="daemon">Daemon & jobs</h2>
          <CodeBlock
            code={`sk run "task" --bg --allow shell:pytest,write_file   # detach, skip prompts for listed tools\nsk jobs -n 5                    # list background jobs\nsk brief                        # morning digest, instant without LLM\nsk digest --force               # teammate pilot: brief + overnight failures\nsk daemon --once                # background watcher one-shot\nsk daemon-install --schedule "daily 08:00"   # systemd/launchd persistent`}
          />

          <h2 id="config">Config</h2>
          <p>
            {inline('~/.sidekick/config.toml')} ({inline('provider')}, {inline('model')},{' '}
            {inline('base_url')} override, {inline('api_key')}, …). Env overrides:{' '}
            {inline('SIDEKICK_PROVIDER')}, {inline('SIDEKICK_MODEL')}, {inline('SIDEKICK_BASE_URL')},{' '}
            {inline('SIDEKICK_API_KEY')}. Data stays home: {inline('history.db')}, {inline('skills/')},{' '}
            {inline('nudges.log')}, {inline('input_history')}, {inline('tui-errors.log')}.
          </p>
          <p>
            <b>History budget:</b> {inline('history_budget_tokens')} (default 3000) caps per-turn history;
            over-budget sessions compact to a rolling summary via the current model (DB history stays complete).
            {inline('/compact')} forces the same squeeze on demand with a token report.
          </p>
          <p>
            <b>Plan mode:</b> {inline('/plan')} (or <b>F4</b>) proposes without implementing — file writes are
            denied while approved shell exploration still works. {inline('/build')} flips back to build mode;
            {inline('sk run --plan')} plans one-shot.
          </p>
          <p>
            <b>Per-project config:</b> a {inline('.sidekick.toml')} in any repo layers over the global file. It
            may set {inline('provider')}, {inline('model')}, {inline('max_steps')}, {inline('temperature')},
            plus a {inline('[project]')} table. {inline('api_key')}/{inline('base_url')} are{' '}
            <i>never</i> read from project files — {inline('sk config --show')} prints the active project and any
            ignored keys. {inline('sk --cwd PATH')} runs any command as if in that directory.
          </p>

          <h2 id="safety">Safety</h2>
          <p>
            Reads auto-run. Writes, deletes, and general shell need approval (inline {inline('[y/N]')} in TUI,
            prompt in CLI), HOME/{inline('/tmp')} only, ≤100KB, never {inline('~/.ssh')},{' '}
            {inline('~/.gnupg')}, {inline('/etc')}, {inline('/usr')}. Multi-tool turns with destructive actions
            get <b>one plan review</b> up front instead of per-tool prompts (silent in {inline('--yes')}/
            {inline('/yolo')}; denials execute nothing). Need a guarantee of zero writes? {inline('/readonly')}{' '}
            (or {inline('sk run --read-only')}) denies every approval-gated tool, and {inline('/rewind')} restores
            pre-edit snapshots of any file the agent touched ({inline('shell')} mutations excepted — use git
            for those). Need a shell whose directory and env survive across calls? The{' '}
            {inline('shell_session')} tool runs a persistent bash per name (same approval + hard-refusals
            as {inline('shell')}; {inline('exit')} closes one). {inline('shell')} hard-refuses {inline('rm -rf /')},{' '}
            {inline('mkfs')}, {inline('dd')} to devices, fork bombs even with approval.{' '}
            {inline('read_url')}/{inline('web_search')} block localhost/private IPs. API keys chmod 600, masked
            in output.
          </p>
          <p>
            <b>Event hooks:</b> {inline('[[hooks.PreToolUse]]')} entries in the config run your own command with
            the pending tool call as JSON — non-zero exit or timeout denies the call fail-closed (secret-blockers,
            policy checks). {inline('SessionStart')}/{inline('PostToolUse')} observe without blocking.{' '}
            {inline('sk hooks')} lists handlers, {inline('sk hooks --check')} dry-runs them. See{' '}
            {inline('docs/hooks.md')}.
          </p>

          <h2 id="architecture">Architecture</h2>
          <CodeBlock
            code={`you → sk / sk run ─→ slash.py (/commands, no LLM)\n   → sk tui (autocomplete, streaming, mic pill)\n   → sk talk (arecord + faster-whisper)\n   → agent.py (stream → tools → synthesize)\n        ├─ grounding: ~/paths, URLs, sysinfo (before the model)\n        ├─ tools.py: 19 tools, allowlists, hard-blocks, SSRF guard\n        ├─ store.py: history, memories FTS5, todos, shell log\n        └─ skills: relevance-ranked SKILL.md index`}
          />
          <p>
            Design bets that paid off: <b>deterministic grounding beats prompt instructions</b>,{' '}
            <b>text-JSON fallback</b> for coders without native tools, <b>FTS5 over vectors</b> (zero deps),{' '}
            <b>parallel reads</b> with failures isolated, <b>capability profiles over hope</b> (the router reads
            declared tool protocols instead of paying a probing 400).
          </p>

          <h2 id="tests">Tests & contributing</h2>
          <CodeBlock
            code={`uv run --python 3.12 --with ".[test]" pytest tests -q   # 780 passed, no Ollama needed`}
          />
          <p>
            The eval harness ({inline('tests/test_eval.py')}) locks in every past quality bug as an offline
            regression test. A suite-wide fixture guarantees tests never touch your live {inline('~/.sidekick/')}.
            See CONTRIBUTING.md for workflow; ROADMAP.md changes by PR only.
          </p>

          <h2 id="troubleshooting">Troubleshooting</h2>
          <h3>Ollama not reachable</h3>
          <p>
            Run {inline('ollama serve')}, then {inline('sk models list')} and {inline('sk doctor')}.{inline('doctor --fix')}{' '}
            pulls the missing model and repairs config.
          </p>
          <h3>Mic hears nothing</h3>
          <p>
            Run {inline('sk mic-test')} for peak dB + silent/quiet/good verdict. Try {inline('--device hw:2,0')}{' '}
            on Linux or a larger {inline('--stt-model base')}.
          </p>
          <h3>TUI looks broken</h3>
          <p>
            Fall back to {inline('sk chat')} (dumb-terminal safe), check {inline('~/.sidekick/tui-errors.log')},
            and bundle {inline('sk report')} (redacted) when filing an issue.
          </p>
        </article>
      </div>
    </div>
  );
}
