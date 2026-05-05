"""
linkedin_login.py
-----------------
Handles LinkedIn login + cookie session persistence.

Flow:
  1. Check if saved cookies exist -> load them (skip login)
  2. If no cookies -> do real login -> save cookies for next time
  3. Verify session is valid by checking feed loads correctly

Run this file standalone first to test login:
  python linkedin_login.py
"""

import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page

COOKIES_FILE = Path("session/linkedin_cookies.json")
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"


async def save_cookies(context: BrowserContext) -> None:
    """Save current browser cookies to disk for future sessions."""
    COOKIES_FILE.parent.mkdir(exist_ok=True)
    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"[v] Cookies saved to {COOKIES_FILE}")


async def load_cookies(context: BrowserContext) -> bool:
    """
    Load saved cookies into browser context.
    Returns True if cookies found, False if no saved session.
    """
    if not COOKIES_FILE.exists():
        print("[i] No saved session found. Will do fresh login.")
        return False

    with open(COOKIES_FILE) as f:
        cookies = json.load(f)

    await context.add_cookies(cookies)
    print(f"[v] Loaded {len(cookies)} cookies from saved session.")
    return True


async def do_linkedin_login(page: Page, email: str, password: str) -> bool:
    """
    Perform actual LinkedIn username/password login.
    Returns True on success, False on failure.
    """
    print("[->] Navigating to LinkedIn login page...")
    try:
        await page.goto(LINKEDIN_LOGIN_URL, wait_until="networkidle", timeout=60000)
    except Exception:
        # networkidle may time out on slow connections; that's fine, continue
        pass
    await asyncio.sleep(3)  # Human-like pause

    # Diagnose what we actually loaded
    current_url = page.url
    print(f"[i] Current URL after navigation: {current_url}")

    # Handle security check / CAPTCHA before even trying to fill form
    if "checkpoint" in current_url or "captcha" in current_url or "challenge" in current_url:
        print("[!] LinkedIn security check detected. Please solve it manually in the browser.")
        print("[!] Waiting up to 60 seconds for you to complete it...")
        await asyncio.sleep(60)
        current_url = page.url
        print(f"[i] URL after waiting: {current_url}")

    # Wait for the login form to actually appear
    try:
        await page.wait_for_selector("#username", timeout=30000)
    except Exception:
        # Take a screenshot to see what's on the page
        Path("session").mkdir(exist_ok=True)
        await page.screenshot(path="session/login_debug.png")
        print(f"[x] Could not find #username field. URL: {page.url}")
        print("[i] Screenshot saved to session/login_debug.png - check what LinkedIn showed.")
        return False

    # Fill email
    await page.fill("#username", email)
    await asyncio.sleep(0.8)

    # Fill password
    await page.fill("#password", password)
    await asyncio.sleep(0.8)

    # Click Sign In
    await page.click('button[type="submit"]')
    print("[->] Submitted login form, waiting for redirect...")

    # Wait for navigation - LinkedIn redirects to feed on success
    try:
        await page.wait_for_url("**/feed/**", timeout=20000)
        print("[v] Login successful! Redirected to feed.")
        return True
    except Exception:
        # Check if we hit a checkpoint/captcha
        current_url = page.url
        if "checkpoint" in current_url or "captcha" in current_url or "challenge" in current_url:
            print("[!] LinkedIn showing captcha/checkpoint. Complete it manually in the browser.")
            print("[!] Waiting 60 seconds for manual completion...")
            await asyncio.sleep(60)
            return True
        elif "feed" in current_url:
            return True
        else:
            Path("session").mkdir(exist_ok=True)
            await page.screenshot(path="session/login_debug.png")
            print(f"[x] Login may have failed. Current URL: {current_url}")
            print("[i] Screenshot saved to session/login_debug.png")
            return False


async def verify_session(page: Page) -> bool:
    """
    Quick check: does the feed load with actual content?
    Returns True if session is valid.
    """
    print("[->] Verifying session by loading feed...")
    await page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)

    current_url = page.url

    # If redirected to login, session expired
    if "login" in current_url or "authwall" in current_url:
        print("[x] Session expired - need to re-login.")
        return False

    # Check if feed content is present
    try:
        # LinkedIn feed has a main feed container
        await page.wait_for_selector(
            "div[data-finite-scroll-hotspot], .scaffold-finite-scroll, main",
            timeout=8000
        )
        print("[v] Session valid - feed loaded successfully!")
        return True
    except Exception:
        print("[?] Feed structure unclear, but not on login page - assuming valid.")
        return True


async def get_authenticated_browser():
    """
    Main entry point: returns (playwright, browser, context, page) 
    with a valid LinkedIn session.
    
    Usage:
        pw, browser, context, page = await get_authenticated_browser()
        # ... do your work ...
        await browser.close()
        await pw.stop()
    """
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")
    headless = os.getenv("HEADLESS", "False").lower() == "true"

    if not email or not password:
        raise ValueError(
            "LINKEDIN_EMAIL and LINKEDIN_PASSWORD must be set in .env\n"
            "Copy .env.example to .env and fill in your credentials."
        )

    pw = await async_playwright().start()

    # Use chromium with settings that reduce bot detection
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-extensions",
        ]
    )

    # Create context with realistic browser fingerprint
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="Asia/Kolkata",
    )

    page = await context.new_page()

    # Stealth: mask automation fingerprints
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)

    # -- Try cookie-based login first -----------------------------------------
    cookies_loaded = await load_cookies(context)

    if cookies_loaded:
        # Verify cookies are still valid
        session_valid = await verify_session(page)
        if session_valid:
            print("[v] Using existing session. Ready to go!")
            return pw, browser, context, page
        else:
            print("[i] Saved cookies expired. Doing fresh login...")

    # -- Fresh login -----------------------------------------------------------
    success = await do_linkedin_login(page, email, password)

    if not success:
        await browser.close()
        await pw.stop()
        raise RuntimeError("LinkedIn login failed. Check credentials in .env")

    # Save cookies for next time
    await save_cookies(context)

    return pw, browser, context, page


# -- Standalone test -----------------------------------------------------------
async def main():
    """Run this directly to test your login setup."""
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 50)
    print("LinkedIn Session Test")
    print("=" * 50)

    try:
        pw, browser, context, page = await get_authenticated_browser()
        print("\n[v] All good! LinkedIn session is working.")
        print(f"    Current URL: {page.url}")

        # Screenshot as proof
        Path("session").mkdir(exist_ok=True)
        await page.screenshot(path="session/login_test.png")
        print("    Screenshot saved to session/login_test.png")

        await asyncio.sleep(3)
        await browser.close()
        await pw.stop()

    except Exception as e:
        print(f"\n[x] Error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
