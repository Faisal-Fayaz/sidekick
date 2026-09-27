import { Link } from 'react-router-dom';
import { REPO_URL, VERSION } from '../data/content';

export default function Footer() {
  return (
    <footer>
      <div className="foot-inner">
        <div style={{ maxWidth: 420 }}>
          <div style={{ color: 'var(--ink)', fontWeight: 800, marginBottom: 8 }}>
            <span style={{ color: 'var(--green)' }}>$ </span>sidekick
          </div>
          <div>
            A local-first terminal companion you can talk to — chat, voice, and 17 tools, on your hardware. MIT
            licensed. No cloud account required.
          </div>
          <div style={{ marginTop: 10 }}>
            <span className="ver-pill">v{VERSION}</span>{' '}
            <span style={{ fontSize: 12 }}>package `sidekick-agent` · binary `sk`</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 36, flexWrap: 'wrap' }}>
          <div>
            <div style={{ color: 'var(--ink)', marginBottom: 8 }}>site</div>
            <div>
              <Link to="/install">install</Link>
            </div>
            <div>
              <Link to="/docs">docs</Link>
            </div>
            <div>
              <Link to="/commands">commands</Link>
            </div>
            <div>
              <Link to="/roadmap">roadmap</Link>
            </div>
          </div>
          <div>
            <div style={{ color: 'var(--ink)', marginBottom: 8 }}>project</div>
            <div>
              <a href={REPO_URL} target="_blank" rel="noreferrer">
                github
              </a>
            </div>
            <div>
              <a href={`${REPO_URL}/releases`} target="_blank" rel="noreferrer">
                releases
              </a>
            </div>
            <div>
              <a href={`${REPO_URL}/issues`} target="_blank" rel="noreferrer">
                issues
              </a>
            </div>
            <div>
              <a href="https://pypi.org/project/sidekick-agent/" target="_blank" rel="noreferrer">
                pypi
              </a>
            </div>
          </div>
        </div>
      </div>
      <div className="wrap" style={{ paddingBottom: 22, color: 'var(--faint)', fontSize: 12 }}>
        $ echo "your files, memory, and voice never leave your machine" <span className="typed-caret" style={{ width: 7, height: 13 }} />
      </div>
    </footer>
  );
}
