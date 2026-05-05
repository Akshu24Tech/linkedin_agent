"""
agent.py - Day 4 (Hardened)
Full pipeline: Auth -> Extract -> Analyze -> Save -> Log

New in Day 4:
- Stealth browser (anti-detection patches)
- Retry engine with exponential backoff
- Screenshot fallback when DOM fails
- RunStats tracker
- Graceful shutdown (Ctrl+C safe)
- Pre-run cookie freshness check
"""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from stealth_browser import launch_stealth_browser, load_cookies_into_context, human_delay, human_scroll
from retry_engine import RunStats, with_retry

# ── Config ────────────────────────────────────────────────────────────────────
MAX_POSTS = int(os.getenv("MAX_POSTS", "15"))        # Posts to analyze per run
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", "7"))  # Min score to save
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
COOKIE_FILE = "session/linkedin_cookies.json"
RAW_POSTS_FILE = "session/raw_posts.json"
ANALYZED_FILE = "session/analyzed_posts.json"


# ── Graceful shutdown ─────────────────────────────────────────────────────────
_shutdown = False

def _handle_sigint(sig, frame):
    global _shutdown
    print("\n\n[agent] Ctrl+C received - finishing current post then stopping...")
    _shutdown = True

signal.signal(signal.SIGINT, _handle_sigint)


# ── Step 1: Auth ──────────────────────────────────────────────────────────────

async def check_session_fresh() -> bool:
    """Check if saved cookies are likely still valid (< 7 days old)."""
    p = Path(COOKIE_FILE)
    if not p.exists():
        return False
    age_hours = (time.time() - p.stat().st_mtime) / 3600
    if age_hours > 168:  # 7 days
        print(f"[auth] Cookies are {age_hours:.0f}h old - may need re-login")
        return False
    print(f"[auth] Cookies are {age_hours:.1f}h old - should be fresh [OK]")
    return True


async def verify_linkedin_session(page) -> bool:
    """Navigate to LinkedIn and check if we're actually logged in."""
    try:
        await page.goto("https://www.linkedin.com/feed/", timeout=20000, wait_until="domcontentloaded")
        await human_delay(2000, 3500)

        # If redirected to login page, we're not authenticated
        current_url = page.url
        if "login" in current_url or "authwall" in current_url:
            print("[auth] [FAIL] Session invalid - cookies rejected by LinkedIn")
            return False

        # Check for feed-specific element
        try:
            await page.wait_for_selector(".scaffold-layout__main, .feed-identity-module", timeout=8000)
            print("[auth] [OK] Session valid - feed loaded")
            return True
        except Exception:
            print("[auth] [WARN] Feed element not found - may need fresh login")
            return False
    except Exception as e:
        print(f"[auth] Error checking session: {e}")
        return False


async def do_fresh_login(page, context) -> bool:
    """Perform a fresh LinkedIn login and save cookies."""
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")

    if not email or not password:
        print("[auth] [FAIL] LINKEDIN_EMAIL / LINKEDIN_PASSWORD not set in .env")
        return False

    print("[auth] Performing fresh login...")
    await page.goto("https://www.linkedin.com/login", wait_until="networkidle", timeout=20000)
    await human_delay(1500, 2500)

    # Type email like a human
    await page.fill("#username", "")
    await human_delay(300, 600)
    for char in email:
        await page.type("#username", char, delay=random.randint(50, 150))
    await human_delay(400, 800)

    # Type password
    await page.fill("#password", "")
    await human_delay(200, 500)
    for char in password:
        await page.type("#password", char, delay=random.randint(40, 120))
    await human_delay(500, 1000)

    await page.click('[data-litms-control-urn="login-submit"]')
    await human_delay(3000, 5000)

    # Handle CAPTCHA / verification
    current = page.url
    if "challenge" in current or "checkpoint" in current:
        print("[auth] [SEC] LinkedIn is asking for verification - solve it manually in the browser")
        print("[auth] Waiting up to 120 seconds...")
        for _ in range(24):
            await asyncio.sleep(5)
            if "feed" in page.url:
                break
        else:
            print("[auth] [FAIL] Timeout waiting for manual verification")
            return False

    if "feed" not in page.url and "login" in page.url:
        print("[auth] [FAIL] Login failed - check credentials in .env")
        return False

    # Save fresh cookies
    from stealth_browser import save_cookies_from_context
    await save_cookies_from_context(context, COOKIE_FILE)
    print("[auth] [OK] Fresh login successful, cookies saved")
    return True


# ── Step 2: Feed Extraction ───────────────────────────────────────────────────

async def extract_feed_posts(page, stats: RunStats) -> list[dict]:
    """
    Scroll the feed and extract posts.
    Tries multiple DOM selectors, falls back to screenshot if all fail.
    """
    print("\n[extract] Starting feed extraction...")
    posts = []
    seen_texts = set()

    # Multiple selector strategies - LinkedIn changes these frequently
    POST_SELECTORS = [
        "div.feed-shared-update-v2",
        "div[data-urn*='activity']",
        "li.occludable-update",
        "div.update-components-text",
    ]

    TEXT_SELECTORS = [
        ".feed-shared-update-v2__description .break-words",
        ".update-components-text .break-words",
        ".feed-shared-text .break-words span[dir='ltr']",
        ".update-components-text span[dir='ltr']",
        ".attributed-text-segment-list__content",
    ]

    AUTHOR_SELECTORS = [
        ".update-components-actor__name span[aria-hidden='true']",
        ".feed-shared-actor__name",
        ".update-components-actor__title span[aria-hidden='true']",
    ]

    ROLE_SELECTORS = [
        ".update-components-actor__description span[aria-hidden='true']",
        ".feed-shared-actor__sub-description .visually-hidden",
    ]

    URL_SELECTORS = [
        "a.app-aware-link[href*='/posts/']",
        "a[href*='activity']",
    ]

    scroll_rounds = 0
    max_scrolls = 6

    while len(posts) < MAX_POSTS and scroll_rounds < max_scrolls and not _shutdown:
        # Try to find post containers
        dom_worked = False
        for post_sel in POST_SELECTORS:
            try:
                containers = await page.query_selector_all(post_sel)
                if len(containers) >= 2:
                    dom_worked = True

                    for container in containers:
                        if len(posts) >= MAX_POSTS:
                            break

                        # Extract text
                        text = ""
                        for txt_sel in TEXT_SELECTORS:
                            try:
                                el = await container.query_selector(txt_sel)
                                if el:
                                    text = (await el.inner_text()).strip()
                                    if len(text) > 30:
                                        break
                            except Exception:
                                continue

                        if not text or len(text) < 30:
                            continue

                        # Dedup by first 100 chars
                        key = text[:100]
                        if key in seen_texts:
                            continue
                        seen_texts.add(key)

                        # Extract author
                        author = "Unknown"
                        for a_sel in AUTHOR_SELECTORS:
                            try:
                                el = await container.query_selector(a_sel)
                                if el:
                                    author = (await el.inner_text()).strip()
                                    if author:
                                        break
                            except Exception:
                                continue

                        # Extract role
                        role = ""
                        for r_sel in ROLE_SELECTORS:
                            try:
                                el = await container.query_selector(r_sel)
                                if el:
                                    role = (await el.inner_text()).strip()
                                    if role:
                                        break
                            except Exception:
                                continue

                        # Extract URL
                        url = ""
                        for u_sel in URL_SELECTORS:
                            try:
                                el = await container.query_selector(u_sel)
                                if el:
                                    href = await el.get_attribute("href")
                                    if href:
                                        url = href.split("?")[0]  # Remove tracking params
                                        break
                            except Exception:
                                continue

                        posts.append({
                            "author": author,
                            "role": role,
                            "text": text[:2000],  # Cap at 2000 chars
                            "url": url,
                            "source": "dom",
                            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        stats.posts_dom_scraped += 1

                    if dom_worked:
                        break  # Found working selector, don't try others
            except Exception as e:
                stats.log_error("dom", f"Selector '{post_sel}' failed: {type(e).__name__}")
                continue

        # Screenshot fallback if DOM completely failed
        if not dom_worked and scroll_rounds == 0:
            print("[extract] [WARN] All DOM selectors failed - trying screenshot fallback")
            try:
                fallback_posts = await screenshot_extract_posts_simple(page)
                for p in fallback_posts:
                    posts.append(p)
                    stats.posts_screenshot_fallback += 1
            except Exception as e:
                stats.log_error("dom", f"Screenshot fallback also failed: {e}")

        scroll_rounds += 1
        if len(posts) < MAX_POSTS:
            print(f"[extract] Scroll {scroll_rounds}/{max_scrolls} - {len(posts)} posts so far")
            await human_scroll(page, scrolls=3)

    stats.posts_found = len(posts)
    print(f"[extract] Done - {len(posts)} unique posts extracted")

    # Save raw posts
    Path(RAW_POSTS_FILE).write_text(json.dumps(posts, indent=2, ensure_ascii=False))
    return posts


async def screenshot_extract_posts_simple(page) -> list[dict]:
    """Simple screenshot fallback using Gemini Vision."""
    import base64
    import google.generativeai as genai
    from PIL import Image
    import io

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash")

    shot = await page.screenshot(full_page=False)
    img = Image.open(io.BytesIO(shot))

    prompt = """LinkedIn feed screenshot. Extract all visible posts as JSON array.
Each item: {"author":"name","role":"job title","text":"post content","url":"","source":"screenshot"}
Return ONLY valid JSON array. No markdown, no explanation."""

    resp = model.generate_content([prompt, img])
    raw = resp.text.strip().strip("```json").strip("```").strip()
    return json.loads(raw)


# ── Step 3: Analysis ──────────────────────────────────────────────────────────

async def analyze_posts(posts: list[dict], stats: RunStats) -> list[dict]:
    """Run Gemini interest-matching analysis on all posts."""
    # Import analyzer from Day 2
    try:
        from analyzer import PostAnalyzer
        analyzer = PostAnalyzer()
    except ImportError:
        print("[analyze] [FAIL] analyzer.py not found - skipping analysis")
        return posts

    print(f"\n[analyze] Analyzing {len(posts)} posts with Gemini...")
    analyzed = []
    scores = []

    for i, post in enumerate(posts, 1):
        if _shutdown:
            print("[analyze] Shutdown requested - stopping analysis")
            break

        print(f"\n  [{i}/{len(posts)}] {post.get('author', 'Unknown')[:30]}")

        try:
            result = await with_retry(
                analyzer.analyze_post,
                post,
                max_attempts=3,
                base_delay=2.0,
                label=f"analyze_post_{i}",
            )
            stats.posts_analyzed += 1
            scores.append(result.relevance_score)

            if result.should_save:
                stats.posts_relevant += 1
                analyzed.append({**post, "analysis": result.dict()})
            else:
                stats.posts_skipped += 1

        except Exception as e:
            stats.log_error("analysis", f"Post {i} by {post.get('author','?')}: {e}")
            stats.posts_skipped += 1

        # Small delay between API calls - don't hammer Gemini
        await asyncio.sleep(0.5)

    if scores:
        stats.avg_relevance_score = round(sum(scores) / len(scores), 1)

    print(f"\n[analyze] Done - {stats.posts_relevant} relevant / {stats.posts_analyzed} analyzed")
    Path(ANALYZED_FILE).write_text(json.dumps(analyzed, indent=2, ensure_ascii=False))
    return analyzed


# ── Step 4: Notion Save ───────────────────────────────────────────────────────

async def save_to_notion(analyzed: list[dict], stats: RunStats):
    """Save all relevant posts to Notion with retry."""
    try:
        from notion_saver import NotionSaver
        saver = NotionSaver()
    except ImportError:
        print("[notion] [FAIL] notion_saver.py not found - skipping Notion save")
        return

    print(f"\n[notion] Saving {len(analyzed)} posts to Notion...")

    for i, post in enumerate(analyzed, 1):
        if _shutdown:
            break

        try:
            result = await with_retry(
                saver.save_post,
                post,
                max_attempts=3,
                base_delay=3.0,
                label=f"notion_save_{i}",
            )

            if result == "exists":
                stats.posts_already_existed += 1
                print(f"  [{i}] Already in Notion (dedup)")
            elif result:
                stats.posts_saved_notion += 1
                print(f"  [{i}] Saved: {post.get('author','?')[:25]}")

        except Exception as e:
            stats.log_error("notion", f"Post {i}: {e}")

        await asyncio.sleep(0.3)  # Notion rate limit: 3 req/sec


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def main():
    stats = RunStats()
    pw = browser = context = page = None

    print("\n" + "=" * 55)
    print("  LINKEDIN AGENT - DAY 4 (HARDENED)")
    print("=" * 55)

    try:
        # ── Browser setup ──────────────────────────────────────────────────
        print("\n[1/4] Setting up stealth browser...")
        pw, browser, context = await launch_stealth_browser(headless=HEADLESS)
        page = await context.new_page()

        # ── Auth ───────────────────────────────────────────────────────────
        print("\n[2/4] Authentication...")
        cookies_loaded = await load_cookies_into_context(context, COOKIE_FILE)

        if cookies_loaded:
            session_ok = await verify_linkedin_session(page)
        else:
            session_ok = False

        if not session_ok:
            print("[auth] No valid session - performing fresh login")
            login_ok = await do_fresh_login(page, context)
            if not login_ok:
                print("[auth] [FAIL] Cannot authenticate - stopping")
                return
            # Navigate to feed after login
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await human_delay(2000, 3000)

        # ── Extract ────────────────────────────────────────────────────────
        print("\n[3/4] Extracting feed posts...")
        posts = await extract_feed_posts(page, stats)

        # Close browser - no longer needed after extraction
        await browser.close()
        await pw.stop()
        pw = browser = context = page = None
        print("[browser] Browser closed [OK]")

        if not posts:
            print("[agent] No posts extracted - nothing to analyze")
            return

        # ── Analyze ────────────────────────────────────────────────────────
        print("\n[4/4] Analyzing posts + saving to Notion...")
        analyzed = await analyze_posts(posts, stats)

        if analyzed:
            await save_to_notion(analyzed, stats)
        else:
            print("[agent] No relevant posts found this run")

    except Exception as e:
        import traceback
        print(f"\n[agent] [FAIL] Unhandled error: {e}")
        traceback.print_exc()

    finally:
        # Always close browser if still open
        try:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass

        # Always save stats
        stats.finish()
        stats.save()
        stats.print_summary()


if __name__ == "__main__":
    import random  # needed for do_fresh_login
    asyncio.run(main())