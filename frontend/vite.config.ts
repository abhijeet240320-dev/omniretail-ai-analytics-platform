import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to the FastAPI app so the browser sees ONE
// origin. Without the proxy the frontend would need an absolute backend URL
// baked in at build time, and every other developer (and every environment
// where the API is on a different host) would have to edit the source.
//
// The API key is NOT proxied or injected. It is entered in the UI and held in
// memory and sessionStorage only - see src/api/client.ts. A key in a bundle is
// a key in git history, and a key in localStorage survives a shared machine.
//
// `loadEnv(mode, '.')` rather than `process.env`: Vite does not define
// `process` in the browser build, and pulling in @types/node purely to read one
// variable would make the type check pass for the wrong reason. `loadEnv` is
// Vite's own API and is what Vite reads its config from.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.API_ORIGIN || 'http://127.0.0.1:8000',
          // README default is 8000. If your backend lives on a different port,
          // set API_ORIGIN in an untracked frontend/.env.local or on the vite CLI
          // — never commit a wrong port in source, because that breaks every
          // developer's dev proxy.
          ws: false,
          configure: (proxy, _options) => {
            proxy.on('error', (err, _req, _res) => {
              console.error('[proxy error]', err.message);
            });
          },
        },
      },
    },
    build: { outDir: 'dist', sourcemap: true },
  }
})
