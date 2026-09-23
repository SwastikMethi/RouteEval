import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests', fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure', actionTimeout: 15000 },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: '../.venv/bin/python tests/start-backend.py', url: 'http://127.0.0.1:8766/api/v1/system', reuseExistingServer: !process.env.CI, timeout: 30000 },
    { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: !process.env.CI, env: { ROUTEBENCH_API_TARGET: process.env.ROUTEBENCH_API_TARGET || 'http://127.0.0.1:8766' } },
  ],
});
