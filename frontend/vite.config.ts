import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies /api to the FastAPI service, so the API client uses relative URLs by default.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Optional split (always-on app, Nov 9): research and training-run pages from API_PROXY_TARGET_RESEARCH (the forecast
    // jobs' release, whose code their research definitions are bound to); everything else from API_PROXY_TARGET.
    proxy: {
      ...(process.env.API_PROXY_TARGET_RESEARCH
        ? { '/api/research': process.env.API_PROXY_TARGET_RESEARCH, '/api/training-run': process.env.API_PROXY_TARGET_RESEARCH }
        : {}),
      '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
});
