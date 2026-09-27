# Sidekick website

Marketing + docs site for the Sidekick project. CLI-themed, static, no backend.

- **Stack:** Vite + React + TypeScript + react-router (hash router, so `dist/` works on any static host)
- **Theme:** phosphor-terminal — JetBrains Mono, scanlines, window chrome, blinking block cursor

## Develop

```bash
cd website
npm install
npm run dev      # http://localhost:5173
```

## Build

```bash
npm run build    # tsc + vite → dist/
npm run preview
```

## Structure

- `src/pages/Home.tsx` — exposure landing (hero terminal, features, compare, FAQ)
- `src/pages/Install.tsx` — channel matrix (uv/pipx/pip/AUR/conda/source)
- `src/pages/Docs.tsx` — the main manual (mirrors README sections)
- `src/pages/Commands.tsx` — searchable command reference
- `src/pages/Roadmap.tsx` — vision + milestones (mirrors ROADMAP.md)
- `src/data/content.ts` — single source for versions, commands, providers, FAQs
- `src/components/HeroTerminal.tsx` — animated typing demo

Content sources of truth remain `README.md`, `ROADMAP.md`, and `src/sk/__init__.py` — bump `VERSION` in
`src/data/content.ts` + `package.json` when releasing.
