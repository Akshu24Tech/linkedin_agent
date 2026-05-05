"""
health_check.py - Day 4
Run this before agent.py to validate all services are working.
Catches config issues early so you don't waste a LinkedIn session.

Usage:
    python health_check.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def check(label: str, passed: bool, detail: str = ""):
    icon = "[PASS]" if passed else "[FAIL]"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  ->  {detail}"
    print(msg)
    return passed


async def run_health_checks():
    print("\n" + "=" * 55)
    print("  LINKEDIN AGENT - HEALTH CHECK")
    print("=" * 55)

    all_ok = True

    # ── 1. Environment variables ─────────────────────────────────────────────
    print("\n[1/5] Environment Variables")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_page = os.getenv("NOTION_PARENT_PAGE_ID", "")
    li_email = os.getenv("LINKEDIN_EMAIL", "")
    li_pass = os.getenv("LINKEDIN_PASSWORD", "")

    ok = check("GEMINI_API_KEY", bool(gemini_key), f"{'set [OK]' if gemini_key else 'MISSING - add to .env'}")
    all_ok = all_ok and ok
    ok = check("NOTION_TOKEN", bool(notion_token), f"{'set [OK]' if notion_token else 'MISSING - add to .env'}")
    all_ok = all_ok and ok
    ok = check("NOTION_PARENT_PAGE_ID", bool(notion_page), f"{'set [OK]' if notion_page else 'MISSING - add to .env'}")
    all_ok = all_ok and ok
    ok = check("LINKEDIN_EMAIL", bool(li_email), f"{'set [OK]' if li_email else 'MISSING - add to .env'}")
    all_ok = all_ok and ok
    ok = check("LINKEDIN_PASSWORD", bool(li_pass), f"{'set [OK]' if li_pass else 'MISSING - add to .env'}")
    all_ok = all_ok and ok

    # ── 2. Session files ─────────────────────────────────────────────────────
    print("\n[2/5] Session Files")
    cookie_path = Path("session/linkedin_cookies.json")
    cookie_ok = cookie_path.exists() and cookie_path.stat().st_size > 100
    check(
        "LinkedIn cookies",
        cookie_ok,
        "found [OK]" if cookie_ok else "NOT FOUND - run: python linkedin_login.py first"
    )
    if not cookie_ok:
        all_ok = False

    notion_db = Path("session/notion_db_id.txt")
    db_ok = notion_db.exists()
    check(
        "Notion DB cache",
        db_ok,
        f"DB: {notion_db.read_text().strip()[:20]}..." if db_ok else "will be created on first run"
    )

    # ── 3. Gemini API ────────────────────────────────────────────────────────
    print("\n[3/5] Gemini API")
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content("Reply with just the word: READY")
            reply = resp.text.strip()
            ok = "READY" in reply.upper()
            check("Gemini Flash ping", ok, f'response: "{reply[:30]}"')
            all_ok = all_ok and ok
        except Exception as e:
            check("Gemini Flash ping", False, str(e)[:60])
            all_ok = False
    else:
        check("Gemini Flash ping", False, "skipped - no API key")
        all_ok = False

    # ── 4. Notion API ────────────────────────────────────────────────────────
    print("\n[4/5] Notion API")
    if notion_token:
        try:
            import requests
            r = requests.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {notion_token}",
                    "Notion-Version": "2022-06-28",
                },
                timeout=8,
            )
            ok = r.status_code == 200
            user = r.json().get("name", "unknown") if ok else ""
            check("Notion auth", ok, f"logged in as: {user}" if ok else f"HTTP {r.status_code}: {r.text[:60]}")
            all_ok = all_ok and ok
        except Exception as e:
            check("Notion auth", False, str(e)[:60])
            all_ok = False
    else:
        check("Notion auth", False, "skipped - no token")
        all_ok = False

    # ── 5. Playwright / Chromium ─────────────────────────────────────────────
    print("\n[5/5] Playwright + Chromium")
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("https://example.com", timeout=10000)
            title = await page.title()
            await browser.close()
        ok = "example" in title.lower()
        check("Chromium launch", ok, f"page title: {title}")
        all_ok = all_ok and ok
    except Exception as e:
        check("Chromium launch", False, str(e)[:80])
        check("Fix", False, "run: playwright install chromium")
        all_ok = False

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    if all_ok:
        print("  [PASS] ALL CHECKS PASSED - ready to run agent.py")
    else:
        print("  [FAIL] SOME CHECKS FAILED - fix issues above before running agent.py")
    print("=" * 55 + "\n")

    return all_ok


if __name__ == "__main__":
    ok = asyncio.run(run_health_checks())
    sys.exit(0 if ok else 1)
