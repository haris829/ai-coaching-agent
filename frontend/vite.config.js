import react from '@vitejs/plugin-react';
// `vitest/config` re-exports Vite's defineConfig with the `test` block typed.
import { defineConfig } from 'vitest/config';
/**
 * The admin UI talks to the FastAPI backend through a dev-server proxy, so the browser only ever
 * makes same-origin `/api/...` requests and there is no base URL to misconfigure. In production
 * the two are served behind a single reverse proxy the same way.
 *
 * Set `VITE_BACKEND_PORT` to point at a backend on a non-default port.
 */
const backendPort = process.env.VITE_BACKEND_PORT ?? '8000';
const backendHost = process.env.VITE_BACKEND_HOST ?? '127.0.0.1';
export default defineConfig({
    plugins: [react()],
    server: {
        port: Number(process.env.VITE_PORT ?? 5173),
        proxy: {
            '/api': {
                target: `http://${backendHost}:${backendPort}`,
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: true,
    },
    test: {
        environment: 'node',
        include: ['src/**/*.test.ts'],
    },
});
