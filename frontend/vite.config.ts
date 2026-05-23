import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/static/spa/',
  build: {
    emptyOutDir: true,
    outDir: '../src/kicad_pcb_web/static/spa',
  },
})
