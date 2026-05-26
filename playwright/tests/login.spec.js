// Interactive OAuth login — run once with: npm run login
//
// Opens a visible browser window, navigates to /login, and clicks the Flickr
// auth button.  You complete the authorization on Flickr's site; the script
// waits (up to 2 minutes) for the callback redirect, then saves the session
// to .auth/session.json for use by sync.spec.js.
//
// Usage:
//   npm run login
//
// After success, re-run the default test suite:
//   npm test

const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

test('complete Flickr OAuth login and save session', async ({ page, context }) => {
  const authDir = path.join(__dirname, '..', '.auth');
  fs.mkdirSync(authDir, { recursive: true });

  await page.goto('/login');
  await expect(page.getByRole('link', { name: /Login with Flickr/i })).toBeVisible();

  console.log('\n\nA browser window has opened.');
  console.log('Click "Login with Flickr" and complete the authorization on Flickr\'s site.');
  console.log('The script will continue automatically once you are redirected back.\n');

  // Click starts the OAuth redirect chain: /login/start → Flickr → /oauth/callback → /
  await page.getByRole('link', { name: /Login with Flickr/i }).click();

  // Wait for the server to redirect back after OAuth (up to 2 minutes for manual auth)
  await page.waitForURL(/\?msg=ok/, { timeout: 120_000 });

  await context.storageState({ path: path.join(authDir, 'session.json') });
  console.log('Session saved to playwright/.auth/session.json');
  console.log('Run `npm test` to execute the authenticated tests.\n');
});
