import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Sidekick docs + marketing site.
// `base: './'` keeps built assets working on GitHub Pages project sites
// and any static file host without extra config.

// Single source of truth for the version. It used to be declared three times —
// src/sk/__init__.py, website/src/data/content.ts, and website/package.json —
// with nothing joining them, so the site advertised stale versions and drifted
// silently (#323). Read the Python source at config time instead.
function readVersion(): string {
  try {
    const src = readFileSync(new URL('../src/sk/__init__.py', import.meta.url), 'utf8')
    const m = src.match(/^__version__\s*=\s*["']([^"']+)["']/m)
    if (m?.[1]) return m[1]
  } catch {
    /* fall through to the manifest */
  }
  const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
  return pkg.version as string
}

const version = readVersion()

export default defineConfig({
  plugins: [react()],
  base: './',
  // Replace a literal marker in content.ts at build time so the site can never
  // disagree with the source.
  define: {
    __SIDEKICK_VERSION__: JSON.stringify(version),
  },
  server: {
    port: 5173,
  },
  preview: {
    port: 4173,
  },
})
