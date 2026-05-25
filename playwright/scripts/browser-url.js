#!/usr/bin/env node
// Prints the URL of the active browser tab to stdout.
//
// Connects to a Chrome/Chromium/Edge instance via the Chrome DevTools Protocol.
// The browser must be started with --remote-debugging-port=9222:
//
//   macOS:   open -a "Google Chrome" --args --remote-debugging-port=9222
//   Linux:   google-chrome --remote-debugging-port=9222
//   Windows: start chrome --remote-debugging-port=9222
//
// Prefers the first open Flickr tab; falls back to the last open tab.

const { chromium } = require('playwright');

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

  console.log(page.url());
  await browser.close();
})();
