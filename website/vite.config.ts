import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Sidekick docs + marketing site.
// `base: './'` keeps built assets working on GitHub Pages project sites
// and any static file host without extra config.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5173,
  },
  preview: {
    port: 4173,
  },
})
