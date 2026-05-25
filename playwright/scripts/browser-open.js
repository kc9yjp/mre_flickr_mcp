#!/usr/bin/env node
// Navigates the active browser tab to the given URL.
//
// Connects to a Chrome/Chromium/Edge instance via the Chrome DevTools Protocol.
// The browser must be started with --remote-debugging-port=9222:
//
//   macOS:   open -a "Google Chrome" --args --remote-debugging-port=9222
//   Linux:   google-chrome --remote-debugging-port=9222
//   Windows: start chrome --remote-debugging-port=9222
//
// Usage: node browser-open.js <url>
//
// Reuses the first open Flickr tab if one exists; otherwise uses the last tab.

const { chromium } = require('playwright');

const url = process.argv[2];
if (!url) {
  console.error('Usage: node browser-open.js <url>');
  process.exit(1);
}

(async () => {
  let browser;
  try {
    browser = await chromium.connectOverCDP('http://localhost:9222');
  } catch {
    console.error(
      'Cannot connect to browser on localhost:9222.\n' +
      'Start Chrome with: google-chrome --remote-debugging-port=9222\n' +
      '  macOS: open -a "Google Chrome" --args --remote-debugging-port=9222'
    );
    process.exit(1);
  }

  const [context] = browser.contexts();
  const pages = context?.pages() ?? [];

  if (!pages.length) {
    console.error('No open tabs found.');
    await browser.close();
    process.exit(1);
  }

  const page =
    pages.find(p => p.url().includes('flickr.com')) ??
    pages[pages.length - 1];

  await page.bringToFront();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await browser.close();
})();
