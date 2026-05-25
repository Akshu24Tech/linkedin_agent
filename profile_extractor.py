"""
profile_extractor.py
─────────────────────
Extracts latest posts from specific LinkedIn profiles.

For each profile in your list:
  1. Navigate to: linkedin.com/in/username/recent-activity/all/
  2. Wait for posts to load
  3. Click "see more" on each post → get FULL text
  4. Extract latest 1-2 posts only
  5. Return RawPost objects (same interface → analyzer works unchanged)

Why this beats feed scraping:
  - No sidebar, no ads, no algorithm — just that person's posts
  - You control exactly who you track
  - Profile activity pages are simpler DOM than the main feed
  - Extracting 1-2 posts per profile = low volume = low detection risk

Run standalone:
  python profile_extractor.py
"""

import asyncio
import random
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, Page, Browser

# Import RawPost from schemas (unchanged interface)
from schemas import RawPost, save_raw_posts
from profiles import load_profiles
from logger import setup_logger

log = setup_logger(__name__)

COOKIES_FILE = Path("session/linkedin_cookies.json")


# ── Browser Setup ─────────────────────────────────────────────────────────────

async def get_browser_with_session():
    """
    Returns an authenticated browser session.

    Path A (default): Local Playwright + saved LinkedIn cookies.
                      Free, works for personal low-volume use.

    Path B (USE_BROWSERBASE=true in .env): Browserbase cloud browser.
                      Stealth mode, CAPTCHA solving, verified browser.
                      Costs ~$20/month for daily use (100 browser hours).

    Switch with: USE_BROWSERBASE=true in .env
    """
    from browserbase_provider import is_browserbase_enabled

    if is_browserbase_enabled():
        log.info("[browser] Path B — Browserbase cloud browser (stealth mode)")
        from browserbase_provider import get_browserbase_browser
        return await get_browserbase_browser()

    # ── Path A: Local Playwright ───────────────────────────────────────────
    log.info("[browser] Path A — local Playwright (saved cookies)")

    if not COOKIES_FILE.exists():
        raise FileNotFoundError(
            "No cookies at session/linkedin_cookies.json\n"
            "Run: python linkedin_login.py"
        )

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,  # MUST be False — headless gets detected
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


# ── Post Extraction from Profile Page ────────────────────────────────────────

async def expand_see_more(page: Page) -> None:
    """
    Click all "see more" / "…more" buttons to expand truncated posts.
    LinkedIn truncates posts — this gets the full text.
    """
    # Multiple selectors LinkedIn uses for the expand button
    selectors = [
        "button.feed-shared-inline-show-more-text__see-more-less-toggle",
        "button[aria-label='see more']",
        "span.see-more",
        ".feed-shared-text__see-more",
        "button:has-text('…more')",
        "button:has-text('see more')",
        ".break-words button",
    ]

    for selector in selectors:
        try:
            buttons = await page.query_selector_all(selector)
            for btn in buttons:
                try:
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            continue


async def extract_posts_from_activity_page(
    page: Page,
    author_name: str,
    max_posts: int = 2,
) -> list[RawPost]:
    """
    Extract latest posts from a profile's activity page.
    Already on the page when this is called.
    """
    posts = []

    # Wait for posts to actually load
    try:
        await page.wait_for_selector(
            ".feed-shared-update-v2, "
            ".occludable-update, "
            "[data-urn], "
            ".profile-creator-shared-feed-update__container",
            timeout=12000,
        )
    except Exception:
        log.warning(f"[extract] Timeout waiting for posts — {author_name}")
        return []

    # Small pause so dynamic content settles
    await asyncio.sleep(2)

    # Expand all "see more" buttons FIRST
    await expand_see_more(page)
    await asyncio.sleep(0.8)

    # Try multiple post container selectors (LinkedIn changes these)
    post_containers = []
    for selector in [
        ".feed-shared-update-v2",
        ".occludable-update",
        "[data-urn]",
        ".profile-creator-shared-feed-update__container",
        "li.profile-creator-shared-feed-update",
    ]:
        containers = await page.query_selector_all(selector)
        if containers:
            post_containers = containers
            log.info(f"[extract] Found {len(containers)} containers via '{selector}'")
            break

    if not post_containers:
        log.warning(f"[extract] No post containers found for {author_name}")
        # Take screenshot for debugging
        Path("session").mkdir(exist_ok=True)
        await page.screenshot(path=f"session/debug_{author_name.replace(' ', '_')}.png")
        log.info(f"[extract] Screenshot saved → session/debug_{author_name.replace(' ', '_')}.png")
        return []

    # Extract from first max_posts containers
    for i, container in enumerate(post_containers[:max_posts]):
        try:
            post = await extract_single_post(container, author_name, i)
            if post:
                posts.append(post)
        except Exception as e:
            log.warning(f"[extract] Failed post {i+1} for {author_name}: {e}")
            continue

    return posts


async def extract_single_post(container, author_name: str, index: int) -> RawPost | None:
    """Extract data from one post container element."""

    # ── Post text ─────────────────────────────────────────────────────────────
    post_text = ""
    text_selectors = [
        ".feed-shared-update-v2__description",
        ".feed-shared-text",
        ".update-components-text",
        "span[dir='ltr']",
        ".break-words span",
        ".feed-shared-text-view span",
    ]
    for sel in text_selectors:
        try:
            el = await container.query_selector(sel)
            if el:
                text = await el.inner_text()
                text = text.strip()
                if len(text) > 30:
                    post_text = text
                    break
        except Exception:
            continue

    # Fallback: grab all text from container, filter noise
    if not post_text:
        try:
            full_text = await container.inner_text()
            lines = [l.strip() for l in full_text.split("\n") if len(l.strip()) > 40]
            # Skip lines that look like UI chrome
            noise = {"like", "comment", "repost", "send", "follow", "connect",
                     "connections", "see more", "reactions", "•", "ago", "1st", "2nd", "3rd"}
            content_lines = [
                l for l in lines
                if not any(n in l.lower() for n in noise)
                and not l.isdigit()
            ]
            if content_lines:
                post_text = "\n".join(content_lines[:15])
        except Exception:
            pass

    if not post_text or len(post_text) < 30:
        return None

    # ── Post URL ──────────────────────────────────────────────────────────────
    post_url = ""
    for sel in [
        "a[href*='/posts/']",
        "a[href*='/feed/update/']",
        "a[href*='activity']",
    ]:
        try:
            el = await container.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                if href:
                    post_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
                    break
        except Exception:
            continue

    # ── Engagement ────────────────────────────────────────────────────────────
    likes = ""
    comments = ""
    try:
        likes_el = await container.query_selector(
            ".social-details-social-counts__reactions-count, "
            "[aria-label*='reaction'], "
            ".social-counts-reactions"
        )
        if likes_el:
            likes = (await likes_el.inner_text()).strip()
    except Exception:
        pass

    # ── Media detection ───────────────────────────────────────────────────────
    has_image = False
    has_video = False
    try:
        has_image = bool(await container.query_selector("img.feed-shared-image__image, .update-components-image"))
        has_video = bool(await container.query_selector("video, .feed-shared-linkedin-video"))
    except Exception:
        pass

    # ── URN / Post ID ─────────────────────────────────────────────────────────
    post_id = f"{author_name}_{index}_{hash(post_text[:40])}"
    try:
        data_urn = await container.get_attribute("data-urn")
        if data_urn:
            post_id = data_urn
    except Exception:
        pass

    return RawPost(
        post_id=post_id,
        author_name=author_name,
        author_headline="",  # Not needed — we know who this is
        post_text=post_text,
        post_url=post_url,
        has_image=has_image,
        has_video=has_video,
        likes_approx=likes,
        comments_approx=comments,
        extracted_at=datetime.now().isoformat(),
        screenshot_path="",
    )


# ── Main Profile Loop ─────────────────────────────────────────────────────────

async def extract_from_all_profiles(
    max_posts_per_profile: int = 2,
    delay_between_profiles: float = 4.0,
) -> list[RawPost]:
    """
    Main pipeline: iterate through all tracked profiles,
    extract latest posts from each.

    Args:
        max_posts_per_profile: How many recent posts to grab (1 or 2 recommended)
        delay_between_profiles: Seconds to wait between profiles (human pacing)
    """
    profiles = load_profiles()

    if not profiles:
        log.error("[profiles] No profiles tracked. Add some first:")
        log.error('  python profiles.py add "Harrison Chase"')
        return []

    log.info(f"[profiles] Tracking {len(profiles)} profiles, {max_posts_per_profile} posts each")

    pw, browser, context, page = await get_browser_with_session()
    all_posts = []

    try:
        for i, profile in enumerate(profiles, 1):
            name = profile["name"]
            activity_url = profile["activity_url"]

            log.info(f"\n[{i}/{len(profiles)}] {name}")
            log.info(f"  URL: {activity_url}")

            # ── Skip check (saves browser time + tokens) ─────────────────
            try:
                from memory import should_skip_person
                skip, reason = should_skip_person(profile["username"])
                if skip:
                    log.info(f"  → Skipped: {reason}")
                    continue
            except Exception:
                pass  # skip check failure → proceed normally

            try:
                # Navigate to activity page
                await page.goto(activity_url, wait_until="domcontentloaded", timeout=20000)

                # Check if we got redirected to login (session expired)
                if "login" in page.url or "authwall" in page.url:
                    log.error("[profiles] Session expired — re-run linkedin_login.py")
                    break

                # Human-like pause after navigation
                await asyncio.sleep(2 + random.random() * 1.5)

                # Extract posts
                posts = await extract_posts_from_activity_page(
                    page, name, max_posts=max_posts_per_profile
                )

                log.info(f"  → Extracted {len(posts)} posts")
                for p in posts:
                    log.info(f"     {p.post_text[:80]}...")

                all_posts.extend(posts)

                # Update memory — posts_seen count (full stats updated after analysis)
                try:
                    from memory import update_person_after_run
                    update_person_after_run(
                        username=profile["username"],
                        posts_seen=len(posts),
                        posts_saved=0,
                        scores=[],
                        topics=[],
                    )
                except Exception:
                    pass

            except Exception as e:
                log.error(f"  → Failed: {e}")
                try:
                    await page.screenshot(
                        path=f"session/debug_{name.replace(' ', '_')}.png"
                    )
                except Exception:
                    pass
                continue

            # Human-paced delay between profiles
            if i < len(profiles):
                delay = delay_between_profiles + random.random() * 2
                log.info(f"  Waiting {delay:.1f}s before next profile...")
                await asyncio.sleep(delay)

    finally:
        await browser.close()
        await pw.stop()
        log.info("[profiles] Browser closed.")

    log.info(f"\n[profiles] Total posts extracted: {len(all_posts)}")
    save_raw_posts(all_posts)
    return all_posts


# ── Async wrapper for agent.py ────────────────────────────────────────────────

async def extract_feed_posts(page=None, max_posts: int = 20) -> list[RawPost]:
    """
    Drop-in replacement called by agent.py.
    'page' and 'max_posts' ignored — we use profiles.json instead.
    max_posts_per_profile is hardcoded to 2 (latest 1-2 per person).
    """
    return await extract_from_all_profiles(max_posts_per_profile=2)


# ── Standalone test ───────────────────────────────────────────────────────────

async def main():
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 58)
    print("  Profile Extractor Test")
    print("=" * 58)

    profiles = load_profiles()
    if not profiles:
        print("\n  No profiles found. Add some first:")
        print('  python profiles.py add "Harrison Chase"')
        print('  python profiles.py add "Andrej Karpathy"')
        return

    print(f"\n  Profiles to check: {len(profiles)}")
    for p in profiles:
        print(f"    · {p['name']} → {p['activity_url']}")
    print()

    posts = await extract_from_all_profiles(max_posts_per_profile=2)

    print(f"\n{'='*58}")
    print(f"  RESULTS: {len(posts)} posts extracted")
    print(f"{'='*58}")

    for i, post in enumerate(posts, 1):
        print(f"\n[{i}] {post.author_name}")
        print(f"     {len(post.post_text)} chars | Likes: {post.likes_approx or '?'}")
        print(f"     {post.post_text[:200]}...")
        if post.post_url:
            print(f"     {post.post_url[:80]}")

    print(f"\n  Raw data → session/raw_posts.json")

    if not posts:
        print("\n  0 posts extracted. Common fixes:")
        print("  1. Check debug screenshots in session/ folder")
        print("  2. Verify profile URLs: python profiles.py list")
        print("  3. Re-login: python linkedin_login.py")
        print("  4. Some profiles have private activity — try different people")


if __name__ == "__main__":
    asyncio.run(main())