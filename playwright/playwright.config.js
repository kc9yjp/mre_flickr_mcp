const { defineConfig, devices } = require('@playwright/test');
const fs = require('fs');

module.exports = defineConfig({
  testDir: './tests',
  testIgnore: ['**/login.spec.js'],
  timeout: 30_000,
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:8000',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'authenticated',
      use: {
        ...devices['Desktop Chrome'],
        storageState: fs.existsSync('.auth/session.json') ? '.auth/session.json' : undefined,
      },
      testMatch: '**/sync.spec.js',
    },
  ],
});
