"""
browserbase_provider.py
────────────────────────
Path B: Browserbase cloud browser — stealth mode, CAPTCHA solving,
verified browser, persistent LinkedIn session via Contexts API.

How it works:
  1. First run: creates a Browserbase Context (persistent browser profile)
  2. Logs into LinkedIn inside that context → saves auth state to cloud
  3. Every subsequent run: reuses the same context — no re-login needed
  4. All extraction happens in Browserbase's verified browser (bot detection bypassed)

Drop-in replacement for local Playwright in profile_extractor.py.
Switch between Path A and Path B using USE_BROWSERBASE=true in .env

Setup:
  1. Sign up at browserbase.com (free tier: 1 browser hour)
  2. Get API key: browserbase.com/settings
  3. Get Project ID: browserbase.com/settings
  4. Add to .env:
       BROWSERBASE_API_KEY=bb_live_xxxxx
       BROWSERBASE_PROJECT_ID=proj_xxxxx
       USE_BROWSERBASE=true

Cost estimate for this project:
  10 profiles × 2 min each = 20 min/day = ~10 hrs/month
  Free tier: 1 hour total → exhausted in 3 days
  Developer plan ($20/mo): 100 hours → ~3 months of daily use
  Startup plan ($99/mo): 500 hours → well over a year

Run standalone to test:
  python browserbase_provider.py
"""

import os
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CONTEXT_ID_FILE = Path("session/bb_context_id.txt")
COOKIES_FILE = Path("session/linkedin_cookies.json")


# ── Check if Browserbase is enabled ──────────────────────────────────────────

def is_browserbase_enabled() -> bool:
    """Check if USE_BROWSERBASE=true in .env"""
    return os.getenv("USE_BROWSERBASE", "false").lower() == "true"


def check_browserbase_deps() -> bool:
    """Verify browserbase package is installed and keys are set."""
    try:
        import browserbase  # noqa
    except ImportError:
        print("[!] browserbase not installed. Run: pip install 'browserbase'")
        return False

    if not os.getenv("BROWSERBASE_API_KEY"):
        print("[!] BROWSERBASE_API_KEY not set in .env")
        return False

    if not os.getenv("BROWSERBASE_PROJECT_ID"):
        print("[!] BROWSERBASE_PROJECT_ID not set in .env")
        return False

    return True


# ── Context Management (persistent LinkedIn session) ─────────────────────────

def get_or_create_context() -> str:
    """
    Get existing Browserbase Context ID or create a new one.
    Contexts persist cookies/auth across sessions — LinkedIn stays logged in.
    """
    from browserbase import Browserbase

    bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])

    # Check cached context
    if CONTEXT_ID_FILE.exists():
        context_id = CONTEXT_ID_FILE.read_text().strip()
        if context_id:
            print(f"[bb] Reusing existing context: {context_id[:12]}...")
            return context_id

    # Create new context
    print("[bb] Creating new Browserbase Context (LinkedIn auth will persist)...")
    context = bb.contexts.create(
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
    )
    context_id = context.id

    Path("session").mkdir(exist_ok=True)
    CONTEXT_ID_FILE.write_text(context_id)
    print(f"[bb] Context created: {context_id[:12]}...")
    return context_id


# ── Session Creation ──────────────────────────────────────────────────────────

def create_bb_session(context_id: str) -> object:
    """
    Create a Browserbase session with:
    - Verified browser (recognized by bot protection systems)
    - CAPTCHA auto-solving
    - Stealth mode
    - Persistent context (LinkedIn stays logged in)
    """
    from browserbase import Browserbase

    bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])

    session = bb.sessions.create(
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
        proxies=True,                    # Residential proxy rotation
        browser_settings={
            "solveCaptchas": True,       # Auto-solve CAPTCHAs
            "blockAds": True,            # Block ads (cleaner DOM)
            "recordSession": True,       # Record for debugging (view at browserbase.com/sessions)
            "context": {
                "id": context_id,
                "persist": True,         # Save auth state back to context after session
            },
        },
    )

    print(f"[bb] Session created: {session.id[:12]}...")
    print(f"[bb] Live view: https://browserbase.com/sessions/{session.id}")
    return session


# ── Main: Get authenticated browser via Browserbase ──────────────────────────

async def get_browserbase_browser():
    """
    Returns (playwright, browser, context, page) using Browserbase cloud.
    Drop-in replacement for profile_extractor.get_browser_with_session().

    First run: logs in to LinkedIn, saves to context.
    Subsequent runs: context already has auth — starts at feed immediately.
    """
    from playwright.async_api import async_playwright

    if not check_browserbase_deps():
        raise RuntimeError(
            "Browserbase setup incomplete. Check .env for "
            "BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID."
        )

    context_id = get_or_create_context()
    session = create_bb_session(context_id)

    pw = await async_playwright().start()

    # Connect to Browserbase's remote browser via CDP
    browser = await pw.chromium.connect_over_cdp(session.connect_url)

    # Use the existing context (has our LinkedIn auth if context is warm)
    bb_context = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = bb_context.pages[0] if bb_context.pages else await bb_context.new_page()

    # Check if we're already logged in
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    current_url = page.url
    if "login" in current_url or "authwall" in current_url:
        # First run or context expired — need to login
        print("[bb] Not logged in yet — performing LinkedIn login...")
        await _do_linkedin_login(page)
        print("[bb] Login complete. Context will persist auth for future sessions.")
    else:
        print("[bb] Already logged in via persistent context ✓")

    return pw, browser, bb_context, page


async def _do_linkedin_login(page) -> None:
    """Log into LinkedIn inside the Browserbase session."""
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")

    if not email or not password:
        raise ValueError(
            "LINKEDIN_EMAIL and LINKEDIN_PASSWORD must be set in .env"
        )

    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    await page.fill("#username", email)
    await asyncio.sleep(0.8)
    await page.fill("#password", password)
    await asyncio.sleep(0.8)
    await page.click('button[type="submit"]')

    try:
        await page.wait_for_url("**/feed/**", timeout=20000)
        print("[bb] Login successful")
    except Exception:
        url = page.url
        if "checkpoint" in url:
            print("[bb] LinkedIn checkpoint detected.")
            print("[bb] Open the live view URL printed above and complete it manually.")
            print("[bb] Waiting 60 seconds...")
            await asyncio.sleep(60)
        elif "feed" in url:
            print("[bb] Login successful (alternate flow)")
        else:
            raise RuntimeError(f"LinkedIn login failed. Current URL: {url}")


# ── Standalone test ───────────────────────────────────────────────────────────

async def main():
    print("=" * 58)
    print("  Browserbase Provider Test")
    print("=" * 58)

    if not is_browserbase_enabled():
        print("\n  USE_BROWSERBASE is not set to 'true' in .env")
        print("  To enable: add USE_BROWSERBASE=true to your .env")
        print("\n  Required .env vars:")
        print("    BROWSERBASE_API_KEY=bb_live_xxxxx")
        print("    BROWSERBASE_PROJECT_ID=proj_xxxxx")
        print("    USE_BROWSERBASE=true")
        return

    if not check_browserbase_deps():
        print("\n  Fix the above issues then re-run.")
        return

    print("\n[→] Testing Browserbase connection...")
    try:
        pw, browser, context, page = await get_browserbase_browser()
        print(f"\n[✓] Connected! Current URL: {page.url}")
        print(f"[✓] Browserbase is working correctly.")

        # Screenshot proof
        Path("session").mkdir(exist_ok=True)
        await page.screenshot(path="session/bb_test.png")
        print(f"[✓] Screenshot saved → session/bb_test.png")

        await browser.close()
        await pw.stop()

    except Exception as e:
        print(f"\n[✗] Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
