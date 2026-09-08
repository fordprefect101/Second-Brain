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
  },
});
