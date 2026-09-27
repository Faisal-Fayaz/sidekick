import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import HeroTerminal from '../components/HeroTerminal';
import { CodeBlock, SectionHeading } from '../components/ui';
import {
  COMPARISON,
  FAQS,
  FEATURES,
  PROVIDERS,
  QUICKSTART_STEPS,
  TUI_KEYS,
} from '../data/content';

const ASCII = ` ___ _    _      _   _      _
/ __(_)__| |___| |_(_)__--| |__
\\__ \\ / _\` / -_) / / /_-<| / /
|___/_\\__,_\\___/_\\_\\__/___|_\\_\\`;

export default function Home() {
  const [faqOpen, setFaqOpen] = useState<number | null>(0);
  const quickstartCode = useMemo(
    () => QUICKSTART_STEPS.map((s) => `${s.cmd}  ${s.comment}`).join('\n'),
    []
  );

  return (
    <>
      <div className="wrap hero">
        <div className="hero-grid">
          <div>
            <pre className="ascii">{ASCII}</pre>
            <span className="eyebrow">
              <span className="dot-live" /> v0.17.0 · 480 tests passing · local-first
            </span>
            <h1 className="hero-title">
              A terminal companion <br />
              you can <span className="accent">talk to.</span>
            </h1>
            <p className="hero-sub">
              <b>Chat, voice, and 17 tools, on your hardware.</b> No cloud account required. No API bill by
              default. Your files, memory, and voice never leave your machine unless you hand it a key.
            </p>
            <div className="hero-cta">
              <Link to="/install" className="btn btn-primary">
                $ sk init — get started
              </Link>
              <Link to="/docs" className="btn">
                ./docs — read the manual
              </Link>
            </div>
            <div className="codeblock" style={{ marginBottom: 16 }}>
              <pre>
                <code>
                  <span className="c-comment"># install · init · chat — that's the whole onboarding</span>
                  {'\n'}
                  <span className="c-cmd">uv tool install sidekick-agent[voice] && sk init && sk</span>
                </code>
              </pre>
            </div>
            <div className="hero-meta">
              <span>python 3.12+</span>
              <span>textual TUI</span>
              <span>ollama + any OpenAI-compatible</span>
              <span>MIT</span>
            </div>
          </div>
          <HeroTerminal />
        </div>
      </div>

      <div className="wrap">
        <section className="block">
          <div className="stat-row">
            {[
              { n: '17', l: 'gated tools · reads auto-run, writes approved' },
              { n: '480', l: 'tests incl. prompt-regression evals' },
              { n: '11', l: 'provider presets incl. ollama + custom URL' },
              { n: '0', l: 'cloud accounts required · $0 default bill' },
            ].map((s) => (
              <div className="card stat" key={s.l}>
                <div className="n">{s.n}</div>
                <div className="l">{s.l}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="block">
          <SectionHeading
            prompt="$ sk why --"
            title="Why sidekick"
            sub="Cloud agents guess. Sidekick grounds: deterministic facts about your system are injected before the model sees the prompt — small local models can't argue with facts."
          />
          <div className="grid-3">
            {FEATURES.map((f) => (
              <div className="card" key={f.title}>
                <span className="tag">{f.tag}</span>
                <h3>
                  <span className="ic">{f.icon}</span>
                  {f.title}
                </h3>
                <p>{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="block">
          <SectionHeading prompt="$ diff --" title="Sidekick vs. typical cloud agent" />
          <div className="compare">
            <div className="h">capability</div>
            <div className="h">sidekick</div>
            <div className="h col-typical">typical cloud agent</div>
            {COMPARISON.map((r) => (
              <Fragment key={r.label}>
                <div>{r.label}</div>
                <div className="yes">
                  ✓ {r.sidekick}
                </div>
                <div className="no col-typical">
                  ✗ {r.typical}
                </div>
              </Fragment>
            ))}
          </div>
        </section>

        <section className="block">
          <SectionHeading
            prompt="$ sk quickstart --"
            title="Up in three commands"
            sub="No clone, no build — installs straight from PyPI. Without [voice] you get everything except Talk/mic (installs on first use instead)."
          />
          <div className="grid-2">
            <CodeBlock code={quickstartCode} />
            <div className="card">
              <h3>
                <span className="ic">›</span>TUI keys you’ll actually use
              </h3>
              <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                {TUI_KEYS.map((k) => (
                  <div key={k.action} style={{ fontSize: 13, color: 'var(--dim)' }}>
                    {k.keys.map((key) => (
                      <span key={key} className="kbd" style={{ marginRight: 6 }}>
                        {key}
                      </span>
                    ))}{' '}
                    — {k.action}
                  </div>
                ))}
              </div>
              <p style={{ marginTop: 12 }}>
                Type <span className="kbd">/</span> for fuzzy slash-autocomplete over 20+ commands. F1 opens a
                generated cheatsheet built from the same tables as the dispatcher — it can’t rot.
              </p>
            </div>
          </div>
        </section>

        <section className="block">
          <SectionHeading
            prompt="$ sk connect --"
            title="BYO key, or stay offline"
            sub="One guided flow: numbered provider list (local ones skip keys), live validation before anything saves, curated models, and a 5-token ping. Any OpenAI-compatible endpoint works via --provider custom."
          />
          <div className="marquee">
            <div className="marquee-track">
              {[...PROVIDERS, ...PROVIDERS].map((p, i) => (
                <span className="prov" key={i}>
                  <b>›</b> {p}
                </span>
              ))}
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <CodeBlock code={`sk connect     # pick provider → paste key (hidden) → pick model → ping. Done.\nsk models opencode   # live list; fast/smart → nemotron-3.5-lightning-free / muse-spark`} />
          </div>
        </section>

        <section className="block">
          <SectionHeading prompt="$ man faq --" title="FAQ" sub="The five-minute version of the README." />
          <div className="faq">
            {FAQS.map((f, i) => (
              <div className="faq-item" key={f.q}>
                <button className="faq-q" onClick={() => setFaqOpen(faqOpen === i ? null : i)}>
                  <span className="arrow">{faqOpen === i ? '▾' : '▸'}</span> {f.q}
                </button>
                {faqOpen === i && <p className="faq-a">{f.a}</p>}
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 18, flexWrap: 'wrap' }}>
            <Link to="/docs" className="btn btn-primary">
              read the full docs →
            </Link>
            <Link to="/commands" className="btn">
              $ sk --help · command index
            </Link>
          </div>
        </section>
      </div>
    </>
  );
}
