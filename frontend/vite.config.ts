import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies /api to the FastAPI service, so the API client uses relative URLs by default.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Optional split (always-on app, Nov 9): soccer and NFL from the delivery release's API, everything else from
    // API_PROXY_TARGET (the forecast jobs' release, whose code the research pages' definitions are bound to).
    proxy: {
      ...(process.env.API_PROXY_TARGET_DELIVERY
        ? { '/api/soccer': process.env.API_PROXY_TARGET_DELIVERY, '/api/nfl': process.env.API_PROXY_TARGET_DELIVERY }
        : {}),
      '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
});
