import { HashRouter, Routes, Route, useLocation } from 'react-router-dom';
import { useEffect } from 'react';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import Home from './pages/Home';
import Install from './pages/Install';
import Docs from './pages/Docs';
import Commands from './pages/Commands';
import Roadmap from './pages/Roadmap';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

function NotFound() {
  return (
    <div className="wrap notfound">
      <div style={{ fontSize: 13, color: 'var(--faint)' }}>$ sk whereami</div>
      <h1 style={{ fontSize: 64, margin: '12px 0' }}>
        4<span style={{ color: 'var(--green)' }}>0</span>4
      </h1>
      <p style={{ color: 'var(--dim)' }}>no such command. maybe try ~/home?</p>
      <a className="btn btn-primary" href="#/">
        cd ~
      </a>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <ScrollToTop />
      <Navbar />
      <main style={{ minHeight: '70vh' }}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/install" element={<Install />} />
          <Route path="/docs" element={<Docs />} />
          <Route path="/commands" element={<Commands />} />
          <Route path="/roadmap" element={<Roadmap />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </HashRouter>
  );
}
