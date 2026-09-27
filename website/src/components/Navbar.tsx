import { useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { VERSION } from '../data/content';

export default function Navbar() {
  const [open, setOpen] = useState(false);
  return (
    <nav className="nav">
      <div className="nav-inner">
        <Link to="/" className="brand" onClick={() => setOpen(false)}>
          <span className="brand-mark">$</span>
          <span>
            sidekick <small>local-first agent</small>
          </span>
        </Link>
        <div className={`nav-links ${open ? 'open' : ''}`}>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')} onClick={() => setOpen(false)}>
            ~/home
          </NavLink>
          <NavLink to="/install" className={({ isActive }) => (isActive ? 'active' : '')} onClick={() => setOpen(false)}>
            ./install
          </NavLink>
          <NavLink to="/docs" className={({ isActive }) => (isActive ? 'active' : '')} onClick={() => setOpen(false)}>
            ./docs
          </NavLink>
          <NavLink to="/commands" className={({ isActive }) => (isActive ? 'active' : '')} onClick={() => setOpen(false)}>
            ./commands
          </NavLink>
          <NavLink to="/roadmap" className={({ isActive }) => (isActive ? 'active' : '')} onClick={() => setOpen(false)}>
            ./roadmap
          </NavLink>
        </div>
        <div className="nav-right">
          <span className="ver-pill">v{VERSION}</span>
          <a
            className="btn btn-ghost"
            href="https://github.com/Faisal-Fayaz/sidekick"
            target="_blank"
            rel="noreferrer"
            style={{ padding: '8px 11px' }}
          >
            ★ github
          </a>
          <button className="btn btn-ghost mobile-toggle" onClick={() => setOpen((o) => !o)} aria-label="menu">
            ☰
          </button>
        </div>
      </div>
    </nav>
  );
}
