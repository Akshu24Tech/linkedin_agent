"""
post_saver.py
─────────────
Automatically saves high-scoring LinkedIn posts to your LinkedIn Saved Posts
by clicking the three-dot menu → Save on each qualifying post URL.

This keeps all decision-making (react, comment, share) with you.
The agent only does the mechanical bookmarking — so nothing slips through.

How LinkedIn Save works:
  1. Open the post URL
  2. Click the three-dot (•••) menu on the post
  3. Click "Save" from the dropdown
  4. Post lands in LinkedIn → My Items → Saved Posts

Run standalone (saves from last analyzed run):
  python post_saver.py

Or called automatically by agent.py after analysis.
"""

import asyncio
import json
import os
import random
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from playwright.async_api import async_playwright, Page

from logger import setup_logger

log = setup_logger(__name__)

COOKIES_FILE = Path("session/linkedin_cookies.json")
ANALYZED_POSTS_FILE = Path("session/analyzed_posts.json")

# Minimum score to auto-save (matches agent threshold)
DEFAULT_THRESHOLD = 7


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SaveResult:
    post_url: str
    author_name: str
    score: int
    success: bool
    status: str   # "saved" | "already_saved" | "skipped_no_url" | "failed"


# ── Browser helpers ───────────────────────────────────────────────────────────

async def get_browser_with_session():
    """
    Returns authenticated browser session using saved cookies.
    Identical setup to profile_extractor.py — same anti-detection config.
    """
    if not COOKIES_FILE.exists():
        raise FileNotFoundError(
            "No cookies at session/linkedin_cookies.json\n"
            "Run: python linkedin_login.py"
        )

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="Asia/Kolkata",
    )

    with open(COOKIES_FILE) as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)

    page = await context.new_page()
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return pw, browser, context, page


# ── Core save action ─────────────────────────────────────────────────────────

async def save_single_post(page: Page, post_url: str, author_name: str) -> str:
    """
    Navigate to a post URL and click the LinkedIn Save button.

    Returns one of:
      "saved"         — successfully saved
      "already_saved" — post was already in Saved Posts
      "failed"        — could not find/click the save button
    """
    log.info(f"  [save] Navigating to post — {author_name}")

    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        log.warning(f"  [save] Navigation failed: {e}")
        return "failed"

    # Check for session expiry
    if "login" in page.url or "authwall" in page.url:
        log.error("[save] Session expired — re-run linkedin_login.py")
        return "failed"

    # Let the page settle
    await asyncio.sleep(2 + random.random())

    # ── Step 1: Find and click the three-dot menu ────────────────────────────
    opened = await _click_three_dot_menu(page)
    if not opened:
        log.warning(f"  [save] Could not open three-dot menu for {author_name}")
        # Take a debug screenshot
        Path("session").mkdir(exist_ok=True)
        safe_name = author_name.replace(" ", "_").replace("/", "_")
        await page.screenshot(path=f"session/debug_save_{safe_name}.png")
        return "failed"

    # Small wait for dropdown to animate in
    await asyncio.sleep(0.5)

    # ── Step 2: Click Save (or detect already-saved) ─────────────────────────
    result = await _click_save_in_dropdown(page)
    return result


async def _click_three_dot_menu(page: Page) -> bool:
    """
    Click the three-dot (more options) menu on the first visible post.
    LinkedIn uses several different selectors across its layouts.

    Returns True if the menu was successfully opened.
    """
    # Selectors for the three-dot / more-options button on a post
    selectors = [
        # Primary — the control icon on feed posts
        "button[aria-label='Open control menu']",
        "button[aria-label='More actions']",
        "button[aria-label*='more']",
        "button[aria-label*='More']",
        # Data-control-name patterns
        "[data-control-name='feed.entity.more']",
        "[data-control-name='more']",
        # Icon-based selectors
        "button.feed-shared-control-menu__trigger",
        "button.update-components-more-icon",
        # Generic: any button containing the three-dot SVG icon class
        "button:has(.artdeco-icon[aria-hidden='true'])",
        # Fallback: li element wrapping the icon
        "li-icon[type='ellipsis-horizontal-icon']",
    ]

    for selector in selectors:
        try:
            el = await page.query_selector(selector)
            if el:
                await el.scroll_into_view_if_needed()
                await asyncio.sleep(0.2)
                await el.click()
                log.info(f"  [save] Opened menu via: {selector}")
                return True
        except Exception:
            continue

    # Last resort: find ALL buttons with aria-labels and look for one containing 'more'
    try:
        buttons = await page.query_selector_all("button[aria-label]")
        for btn in buttons:
            label = (await btn.get_attribute("aria-label") or "").lower()
            if "more" in label or "option" in label or "action" in label:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                log.info(f"  [save] Opened menu via aria-label scan: '{label}'")
                return True
    except Exception:
        pass

    return False


async def _click_save_in_dropdown(page: Page) -> str:
    """
    After the three-dot dropdown is open, click Save.
    If "Unsave" appears instead, the post is already saved.

    Returns "saved" | "already_saved" | "failed"
    """
    # Check for "Unsave" first — means already saved
    unsave_selectors = [
        "span:text('Unsave')",
        "[aria-label*='Unsave']",
        "div[aria-label*='Unsave']",
        "button:has-text('Unsave')",
    ]
    for selector in unsave_selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                log.info("  [save] Already saved (Unsave button found) ✓")
                # Close the menu by pressing Escape
                await page.keyboard.press("Escape")
                return "already_saved"
        except Exception:
            continue

    # Click "Save" in the dropdown
    save_selectors = [
        "span:text('Save')",
        "div[aria-label*='Save']",
        "button:has-text('Save')",
        "[data-control-name='save']",
        "li:has-text('Save') >> span",
    ]

    for selector in save_selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                await el.click()
                log.info("  [save] Clicked Save ✓")
                # Brief wait to let the save register
                await asyncio.sleep(1)
                return "saved"
        except Exception:
            continue

    # Could not find Save button — close menu and report failure
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    log.warning("  [save] Save option not found in dropdown")
    return "failed"


# ── Batch saver ──────────────────────────────────────────────────────────────

async def save_posts_to_linkedin(
    posts_with_analyses: list[tuple],
    threshold: int = DEFAULT_THRESHOLD,
    delay_between: float = 3.0,
) -> list[SaveResult]:
    """
    Main entry point called by agent.py.

    Args:
        posts_with_analyses: List of (RawPost, PostAnalysis) tuples
        threshold:           Min relevance score to save (default 7)
        delay_between:       Seconds between saves (human pacing)

    Returns:
        List of SaveResult objects (one per qualifying post)
    """
    # Filter to only posts that meet threshold and have a URL
    qualifying = [
        (post, analysis)
        for post, analysis in posts_with_analyses
        if analysis.is_relevant
        and analysis.relevance_score >= threshold
        and analysis.should_save
    ]

    if not qualifying:
        log.info("[saver] No posts meet threshold — nothing to save to LinkedIn")
        return []

    log.info(f"[saver] {len(qualifying)} posts qualify for LinkedIn Save (score ≥ {threshold})")

    # Posts without URLs can't be saved
    saveable = [(p, a) for p, a in qualifying if p.post_url]
    no_url   = [(p, a) for p, a in qualifying if not p.post_url]

    results: list[SaveResult] = []

    for post, analysis in no_url:
        log.warning(f"  [saver] No URL — skipping {post.author_name}")
        results.append(SaveResult(
            post_url="",
            author_name=post.author_name,
            score=analysis.relevance_score,
            success=False,
            status="skipped_no_url",
        ))

    if not saveable:
        return results

    # Open browser and save each post
    pw, browser, context, page = await get_browser_with_session()

    try:
        for i, (post, analysis) in enumerate(saveable, 1):
            log.info(f"\n[saver] [{i}/{len(saveable)}] {post.author_name} — score {analysis.relevance_score}/10")

            # Skip if already saved to LinkedIn in a previous run
            try:
                from memory import is_post_linkedin_saved
                if is_post_linkedin_saved(post.post_id, post.post_url):
                    log.info("  ✓ Already saved in a previous run — skipping")
                    results.append(SaveResult(
                        post_url=post.post_url,
                        author_name=post.author_name,
                        score=analysis.relevance_score,
                        success=True,
                        status="already_saved",
                    ))
                    continue
            except Exception:
                pass  # memory check failure — proceed to save anyway

            status = await save_single_post(page, post.post_url, post.author_name)
            success = status in ("saved", "already_saved")

            if status == "saved":
                log.info(f"  ✓ Saved to LinkedIn Saved Posts")
                # Record in memory so we don't re-save next run
                try:
                    from memory import mark_post_linkedin_saved
                    mark_post_linkedin_saved(post.post_id, post.post_url)
                except Exception:
                    pass
            elif status == "already_saved":
                log.info(f"  ✓ Already in Saved Posts")
                try:
                    from memory import mark_post_linkedin_saved
                    mark_post_linkedin_saved(post.post_id, post.post_url)
                except Exception:
                    pass
            else:
                log.warning(f"  ✗ Could not save")

            results.append(SaveResult(
                post_url=post.post_url,
                author_name=post.author_name,
                score=analysis.relevance_score,
                success=success,
                status=status,
            ))

            # Human-paced delay between saves
            if i < len(saveable):
                delay = delay_between + random.random() * 2
                log.info(f"  Waiting {delay:.1f}s...")
                await asyncio.sleep(delay)

    finally:
        await browser.close()
        await pw.stop()
        log.info("[saver] Browser closed.")

    saved_count   = sum(1 for r in results if r.status == "saved")
    already_count = sum(1 for r in results if r.status == "already_saved")
    failed_count  = sum(1 for r in results if r.status == "failed")

    log.info(
        f"\n[saver] Done — {saved_count} saved | "
        f"{already_count} already saved | {failed_count} failed"
    )

    return results


# ── Standalone: save from last analyzed run ───────────────────────────────────

async def save_from_last_run(threshold: int = DEFAULT_THRESHOLD) -> list[SaveResult]:
    """
    Loads session/analyzed_posts.json and saves qualifying posts to LinkedIn.
    Used by `python agent.py --save-posts` and `python post_saver.py`.
    """
    if not ANALYZED_POSTS_FILE.exists():
        log.error("[saver] No session/analyzed_posts.json found.")
        log.error("        Run the full pipeline first: python agent.py")
        return []

    with open(ANALYZED_POSTS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    from schemas import RawPost, PostAnalysis

    pairs = []
    for item in data:
        try:
            post     = RawPost(**{k: v for k, v in item["post"].items()
                                  if k in RawPost.__dataclass_fields__})
            analysis = PostAnalysis(**item["analysis"])
            pairs.append((post, analysis))
        except Exception as e:
            log.warning(f"[saver] Skipping malformed entry: {e}")
            continue

    log.info(f"[saver] Loaded {len(pairs)} posts from last run")
    return await save_posts_to_linkedin(pairs, threshold=threshold)


# ── Summary printer ───────────────────────────────────────────────────────────

def print_save_summary(results: list[SaveResult]) -> None:
    """Print a clean summary of save results to stdout."""
    if not results:
        print("\n  No posts were processed.")
        return

    saved   = [r for r in results if r.status == "saved"]
    already = [r for r in results if r.status == "already_saved"]
    failed  = [r for r in results if r.status == "failed"]
    no_url  = [r for r in results if r.status == "skipped_no_url"]

    print("\n" + "═" * 55)
    print("  LinkedIn Save Results")
    print("═" * 55)
    print(f"  ✓ Newly saved:      {len(saved)}")
    print(f"  ✓ Already saved:    {len(already)}")
    print(f"  ✗ Failed:           {len(failed)}")
    if no_url:
        print(f"  ⚠ No URL (skipped): {len(no_url)}")
    print("═" * 55)

    for r in saved + already:
        status_icon = "✓ Saved" if r.status == "saved" else "✓ Already saved"
        print(f"\n  [{r.score}/10] {r.author_name}")
        print(f"  {status_icon}")
        if r.post_url:
            print(f"  {r.post_url[:75]}")

    for r in failed:
        print(f"\n  [✗] {r.author_name} — could not save")
        if r.post_url:
            print(f"       {r.post_url[:75]}")

    print()
    print("  → Check LinkedIn → Me → My Items → Saved posts")
    print()


# ── Standalone entry point ────────────────────────────────────────────────────

async def main():
    from dotenv import load_dotenv
    load_dotenv()

    threshold = int(os.environ.get("SAVE_THRESHOLD", DEFAULT_THRESHOLD))

    print("\n" + "═" * 55)
    print("  LinkedIn Post Saver")
    print(f"  Saving posts with score ≥ {threshold} from last run")
    print("═" * 55 + "\n")

    results = await save_from_last_run(threshold=threshold)
    print_save_summary(results)


if __name__ == "__main__":
    import os
    asyncio.run(main())
