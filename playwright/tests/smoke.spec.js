// Smoke tests — no authentication required.
// Verify the server is reachable and unauthenticated routes behave correctly.
const { test, expect } = require('@playwright/test');

test('login page loads', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveTitle(/Flickr/i);
  await expect(page.getByRole('link', { name: /Login with Flickr/i })).toBeVisible();
});

test('unauthenticated / redirects to /login', async ({ page }) => {
  await page.goto('/');
  expect(page.url()).toContain('/login');
});

test('unauthenticated /sync redirects to /login', async ({ page }) => {
  await page.goto('/sync');
  expect(page.url()).toContain('/login');
});

test('unauthenticated /stats redirects to /login', async ({ page }) => {
  await page.goto('/stats');
  expect(page.url()).toContain('/login');
});

test('unauthenticated /setup redirects to /login', async ({ page }) => {
  await page.goto('/setup');
  expect(page.url()).toContain('/login');
});
