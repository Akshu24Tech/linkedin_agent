/**
 * background.js (Service Worker)
 * ───────────────────────────────
 * Handles extension lifecycle events.
 * In MV3, the service worker replaces the background page.
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log("[LinkedIn Agent] Extension installed.");
});
