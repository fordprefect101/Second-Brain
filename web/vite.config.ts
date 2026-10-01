import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Fixed port: api/config.py allowlists :5173 for CORS. If Vite falls back to
    // another port the API silently rejects requests, which is a confusing way to
    // spend twenty minutes at step 5.
    port: 5173,
    strictPort: true,
    // The phone reaches this server through `tailscale serve`, which only devices
    // on my own tailnet can use; Vite refuses hostnames it was not told about.
    allowedHosts: ['.ts.net'],
    // The page calls /api/..., forwarded here to the API. One address for both, so
    // the phone needs no second port and the browser no cross-origin permission.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
