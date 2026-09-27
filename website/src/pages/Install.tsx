import { useState } from 'react';
import { CodeBlock, SectionHeading } from '../components/ui';
import { INSTALL_CHANNELS } from '../data/content';

export default function Install() {
  const [active, setActive] = useState(INSTALL_CHANNELS[0]);
  return (
    <div className="wrap" style={{ paddingTop: 36 }}>
      <SectionHeading
        prompt="$ sk install --"
        title="Install"
        sub="Published name is sidekick-agent (sidekick was taken on PyPI); the command stays sk. Requires Python 3.12+. Version is a single source of truth in src/sk/__init__.py."
      />
      <div className="tabs">
        {INSTALL_CHANNELS.map((c) => (
          <button key={c.id} className={active.id === c.id ? 'on' : ''} onClick={() => setActive(c)}>
            {c.label}
          </button>
        ))}
      </div>
      <CodeBlock code={active.command} />
      <p style={{ color: 'var(--dim)', fontSize: 13.5 }}>{'› '}{active.note}</p>

      <div className="grid-2" style={{ marginTop: 18 }}>
        <div className="card">
          <span className="tag">first run</span>
          <h3>Guided setup</h3>
          <div style={{ marginTop: 10 }}>
            <CodeBlock
              code={`sk init      # hardware → model → verify\nsk doctor    # health check\nsk models list   # what you have locally`}
            />
          </div>
          <p style={{ marginTop: 10 }}>
            Local path needs Ollama (`ollama serve`, pull `qwen2.5-coder:7b` for smarts or `llama3.2:3b` for
            speed).
          </p>
        </div>
        <div className="card">
          <span className="tag">dev install</span>
          <h3>From source</h3>
          <div style={{ marginTop: 10 }}>
            <CodeBlock
              code={`git clone https://github.com/Faisal-Fayaz/sidekick && cd sidekick\nuv tool install -e ".[voice]"\nsk doctor`}
            />
          </div>
          <p style={{ marginTop: 10 }}>Editable install for contributors. See docs → contributing.</p>
        </div>
      </div>
    </div>
  );
}
