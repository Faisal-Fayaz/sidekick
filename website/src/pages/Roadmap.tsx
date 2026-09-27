import { Link } from 'react-router-dom';
import { CodeBlock } from '../components/ui';
import { ROADMAP_DONE, ROADMAP_NEXT } from '../data/content';

export default function Roadmap() {
  return (
    <div className="wrap" style={{ paddingTop: 36 }}>
      <div className="sec-head">
        <span className="prompt">$ cat ROADMAP.md --</span>
        <h2>Roadmap</h2>
      </div>
      <p className="sec-sub">
        Shared plan for the canonical repo — changes by pull request only. Thesis: own the users cloud agents
        structurally can’t serve (offline, regulated, no-API-bill) — don’t chase their feature list. Explicit
        non-goals: cloud-hosted version, frontier parity, native mobile apps.
      </p>

      <div className="grid-2">
        <div className="card">
          <span className="tag">next — v0.21.0 · unplanned, propose by PR</span>
          {ROADMAP_NEXT.map((r) => (
            <div key={r.v}>
              <h3>{r.v}</h3>
              <ul style={{ color: 'var(--dim)', fontSize: 13.5, lineHeight: 1.7 }}>
                {r.items.map((i) => (
                  <li key={i}>
                    <code className="inline" style={{ color: 'var(--green)' }}>{i}</code>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <h3 style={{ marginTop: 14 }}>Parking lot</h3>
          <ul style={{ color: 'var(--dim)', fontSize: 13.5, lineHeight: 1.7 }}>
            <li>Air-gapped install — offline bundle for locked-down environments</li>
            <li>Team memory + shared skills — org SKILL.md packs with permissions</li>
            <li>Thin IDE extension — VS Code client on the local daemon</li>
            <li>Daemon as a teammate — scheduled briefs, dirty-repo/CI watch</li>
            <li>Python SDK + localhost HTTP API — library and CLI from one core</li>
          </ul>
        </div>
        <div>
          <div className="card">
            <span className="tag">shipped · most recent first</span>
            <h3>Done</h3>
            <ul style={{ color: 'var(--dim)', fontSize: 13.5, lineHeight: 1.8 }}>
              {ROADMAP_DONE.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
          <div style={{ marginTop: 14 }}>
            <CodeBlock code={`# full history + release notes\ngh releases --repo Faisal-Fayaz/sidekick --limit 20`} />
          </div>
          <div style={{ marginTop: 12 }}>
            <Link to="/docs" className="btn">
              ← back to docs
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
