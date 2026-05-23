import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/static/spa/',
  build: {
    emptyOutDir: true,
    outDir: '../src/kicad_pcb_web/static/spa',
  },
})
