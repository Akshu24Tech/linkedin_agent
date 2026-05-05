"""
stealth_browser.py - Day 4
Anti-detection browser launcher. Patches all the signals LinkedIn uses to detect bots.
Replaces raw playwright.launch() calls in linkedin_login.py and feed_extractor.py.
"""

import asyncio
import random
from playwright.async_api import async_playwright, BrowserContext, Page


# Real Chrome user agents - rotate each session
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# JS patches injected before every page load - hide automation signals
STEALTH_SCRIPTS = """
// 1. Hide navigator.webdriver (most common detection)
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Fix plugins array (headless has 0 plugins - dead giveaway)
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// 3. Fix languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

// 4. Remove Playwright global vars
delete window.__playwright__binding__;
delete window.__pwInitScripts;

// 5. Fix chrome object (missing in headless)
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 6. Fix permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);
"""


async def human_delay(min_ms: int = 800, max_ms: int = 2500):
    """Random human-like delay between actions."""
    delay = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay)


async def human_scroll(page: Page, scrolls: int = 5):
    """Scroll like a human - variable speed, random pauses."""
    for _ in range(scrolls):
        # Random scroll distance (humans don't scroll exactly 500px every time)
        distance = random.randint(300, 800)
        await page.evaluate(f"window.scrollBy(0, {distance})")
        await human_delay(600, 1800)


async def launch_stealth_browser(headless: bool = False):
    """
    Launch Chromium with full stealth configuration.
    Returns (playwright, browser, context) - caller must close all three.
    
    Usage:
        pw, browser, context = await launch_stealth_browser()
        page = await context.new_page()
        ...
        await browser.close()
        await pw.stop()
    """
    pw = await async_playwright().start()

    ua = random.choice(USER_AGENTS)

    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",  # Key flag
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1366,768",
            "--disable-extensions",
            "--disable-gpu" if headless else "",
            f"--user-agent={ua}",
        ],
    )

    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="Asia/Kolkata",
        # Realistic headers
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    # Inject stealth patches before every page load
    await context.add_init_script(STEALTH_SCRIPTS)

    return pw, browser, context


async def load_cookies_into_context(context: BrowserContext, cookie_file: str) -> bool:
    """Load saved cookies into a context. Returns True if cookies exist."""
    import json
    from pathlib import Path

    path = Path(cookie_file)
    if not path.exists():
        return False

    cookies = json.loads(path.read_text())
    if not cookies:
        return False

    await context.add_cookies(cookies)
    return True


async def save_cookies_from_context(context: BrowserContext, cookie_file: str):
    """Save all cookies from context to file."""
    import json
    from pathlib import Path

    cookies = await context.cookies()
    Path(cookie_file).write_text(json.dumps(cookies, indent=2))
    print(f"[stealth] Cookies saved -> {cookie_file}")


# ── Quick self-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def _test():
        print("[stealth] Launching stealth browser for self-test...")
        pw, browser, context = await launch_stealth_browser(headless=False)
        page = await context.new_page()

        # Test against a bot-detection checker
        await page.goto("https://bot.sannysoft.com", wait_until="networkidle")
        await asyncio.sleep(3)
        await page.screenshot(path="session/stealth_test.png")
        print("[stealth] Screenshot saved -> session/stealth_test.png")
        print("[stealth] Check the screenshot - all rows should be green/pass")

        await browser.close()
        await pw.stop()

    import os
    os.makedirs("session", exist_ok=True)
    asyncio.run(_test())
