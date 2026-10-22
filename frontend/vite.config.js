import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// PANTHEON_API=http://<host>:8000 points the dev server at another backend (e.g. the test
// instance). The backend's origin check sees the dev server's Origin, so it is rewritten to
// the target's own origin for proxied requests.
const API = process.env.PANTHEON_API || 'http://localhost:8000'
const sameOrigin = { Origin: API }

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: API,
        changeOrigin: true,
        headers: sameOrigin,
      },
      '/ws': {
        target: API.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
        headers: sameOrigin,
      },
    },
  },
})
