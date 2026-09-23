import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { '/api': { target: process.env.ROUTEBENCH_API_TARGET || 'http://127.0.0.1:8765', changeOrigin: false } } },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts?(x)'], setupFiles: ['src/test-setup.ts'] },
});
