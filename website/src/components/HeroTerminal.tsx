import { useEffect, useState } from 'react';
import { HERO_SCRIPT } from '../data/content';

function useTypewriterLoop() {
  const [scriptIdx, setScriptIdx] = useState(0);
  const [charCount, setCharCount] = useState(0);
  const [showOutput, setShowOutput] = useState(false);

  useEffect(() => {
    const script = HERO_SCRIPT[scriptIdx];
    if (!showOutput) {
      if (charCount < script.typed.length) {
        const t = setTimeout(() => setCharCount((c) => c + 1), 42);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setShowOutput(true), 420);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => {
      // hold output, then advance
      setScriptIdx((i) => (i + 1) % HERO_SCRIPT.length);
      setCharCount(0);
      setShowOutput(false);
    }, 4200);
    return () => clearTimeout(t);
  }, [scriptIdx, charCount, showOutput]);

  return { scriptIdx, charCount, showOutput };
}

export default function HeroTerminal() {
  const { scriptIdx, charCount, showOutput } = useTypewriterLoop();
  const [tab, setTab] = useState<'demo' | 'tui' | 'voice'>('demo');

  return (
    <div className="term" role="region" aria-label="Sidekick live demo terminal">
      <div className="term-bar">
        <span className="traffic">
          <i />
          <i />
          <i />
        </span>
        <span className="term-title">sidekick — zsh — 80×24</span>
        <span className="term-tabs">
          <button className={tab === 'demo' ? 'on' : ''} onClick={() => setTab('demo')}>
            demo
          </button>
          <button className={tab === 'tui' ? 'on' : ''} onClick={() => setTab('tui')}>
            tui
          </button>
          <button className={tab === 'voice' ? 'on' : ''} onClick={() => setTab('voice')}>
            voice
          </button>
        </span>
      </div>
      <div className="term-body">
        {tab === 'demo' && (
          <>
            {HERO_SCRIPT.slice(0, scriptIdx).map((s, i) => (
              <div key={i}>
                <div>
                  <span className="ps1">➜ </span>
                  <span className="path">~ </span>
                  <span>{s.typed}</span>
                </div>
                {s.output.map((line, j) => (
                  <div
                    key={j}
                    className={`t-out ${line.startsWith('╭') || line.startsWith('│ !') ? 'box' : ''} ${
                      line.startsWith('•') ? 'green' : ''
                    }`}
                  >
                    {line}
                  </div>
                ))}
              </div>
            ))}
            <div>
              <span className="ps1">➜ </span>
              <span className="path">~ </span>
              <span>{HERO_SCRIPT[scriptIdx].typed.slice(0, charCount)}</span>
              {!showOutput && <span className="typed-caret" />}
            </div>
            {showOutput &&
              HERO_SCRIPT[scriptIdx].output.map((line, j) => (
                <div
                  key={j}
                  className={`t-out ${line.startsWith('╭') || line.startsWith('│ !') ? 'box' : ''} ${
                    line.startsWith('•') ? 'green' : ''
                  }`}
                >
                  {line}
                </div>
              ))}
            {showOutput && (
              <div>
                <span className="ps1">➜ </span>
                <span className="path">~ </span>
                <span className="typed-caret" />
              </div>
            )}
            <div className="t-dim" style={{ marginTop: 8 }}>
              ── 543 tests passing · offline · no API bill ──
            </div>
          </>
        )}

        {tab === 'tui' && (
          <>
            <div>
              <span className="ps1">$ </span>sk
            </div>
            <div className="t-out">
              {`┌ sidekick · qwen2.5-coder:7b · session 12 ─────────────┐
│ › what files are in the sidekick repo?              │
│ ● src/sk/agent.py  ● src/sk/tools/  ● tests/ …      │
│ streamed live as Markdown · approvals as cards      │
└ [Enter] send · [ctrl+j] newline · [F1] help ────────┘`}
            </div>
            <div>
              <span className="ps1">/ </span>sk<span className="typed-caret" />
            </div>
            <div className="t-dim">slash autocomplete: 30+ commands · fuzzy filter · Tab completes</div>
          </>
        )}

        {tab === 'voice' && (
          <>
            <div>
              <span className="ps1">$ </span>sk talk
            </div>
            <div className="t-out">
              {`[Enter] to record, [Enter] to stop. /quit exits.
heard› what files are in the sidekick repo
  → transcribed locally (faster-whisper int8, CPU)
  → transcript lands editable in the prompt`}
            </div>
            <div>
              <span className="ps1">◉ </span>
              <span className="t-out green" style={{ display: 'inline' }}>
                rec ● 00:03 — peak -18dB · good
              </span>
              <span className="typed-caret" />
            </div>
            <div className="t-dim">voice never leaves your machine · temp files deleted after each take</div>
          </>
        )}
      </div>
    </div>
  );
}
