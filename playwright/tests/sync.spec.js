// Authenticated sync tests — requires a saved session from login.spec.js.
// Playwright loads .auth/session.json (set in playwright.config.js) so these
// tests run as the logged-in user.
//
// If the session file is missing, tests are skipped with a clear message.

const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const sessionFile = path.join(__dirname, '..', '.auth', 'session.json');
test.skip(!fs.existsSync(sessionFile), 'No session found — run `npm run login` first to authenticate.');

test('sync page loads and shows trigger buttons', async ({ page }) => {
  await page.goto('/sync');
  await expect(page).toHaveURL(/\/sync/);
  await expect(page.getByRole('button', { name: /Sync All/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Photos/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Albums/i })).toBeVisible();
});

test('trigger photos sync', async ({ page }) => {
  await page.goto('/sync');

  // Click the Photos sync button — it POSTs and redirects back to /sync
  await page.getByRole('button', { name: 'Photos' }).click();

  await expect(page).toHaveURL(/\/sync/);
  // After triggering, we land back on the sync page
  await expect(page.getByRole('heading', { name: 'Sync' })).toBeVisible();
});

test('stats page shows collection data', async ({ page }) => {
  await page.goto('/stats');
  await expect(page).toHaveURL(/\/stats/);
  await expect(page.getByRole('heading', { name: /Stats/i })).toBeVisible();
});

test('setup page shows MCP config snippets', async ({ page }) => {
  await page.goto('/setup');
  await expect(page).toHaveURL(/\/setup/);
  // Config snippets are rendered in <pre> elements
  await expect(page.locator('pre').first()).toBeVisible();
});
