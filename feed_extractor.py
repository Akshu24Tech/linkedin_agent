"""
feed_extractor.py
-----------------
Scrolls the LinkedIn feed and extracts raw post data.
No AI analysis here - just pure extraction.

This is the "eyes" of the agent.

Run standalone to test extraction:
  python feed_extractor.py
"""

import asyncio
import json
import re
from dataclasses import dataclass, asdict # used to create structured data
from datetime import datetime
from pathlib import Path
from playwright.async_api import Page


@dataclass
class RawPost:
    """Raw extracted post data before AI analysis."""
    post_id: str           # Unique ID for deduplication
    author_name: str
    author_headline: str   # "AI Engineer at Google" etc.
    post_text: str         # The actual content
    post_url: str          # Direct link to post
    has_image: bool
    has_video: bool
    likes_approx: str      # "1,234" or "245" - approximate
    comments_approx: str
    extracted_at: str      # ISO timestamp
    screenshot_path: str   # Path to screenshot (for fallback analysis)


def clean_text(text: str) -> str:
    """Clean extracted text - remove extra whitespace, LinkedIn junk."""
    if not text:
        return ""
    # Remove excessive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove leading/trailing whitespace
    text = text.strip()
    # Remove LinkedIn's "...more" suffix
    text = re.sub(r'\.{3}more$', '', text).strip()
    return text


async def scroll_feed(page: Page, scroll_count: int = 5) -> None:
    """
    Scroll down LinkedIn feed to load more posts.
    Uses realistic human-like scrolling behavior.
    """
    print(f"[->] Scrolling feed ({scroll_count} scrolls)...")
    for i in range(scroll_count):
        # Scroll down by a random-ish amount (human-like)
        scroll_amount = 600 + (i % 3) * 150
        await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        # Random pause between scrolls (1.5s - 3s)
        await asyncio.sleep(1.5 + (i % 2) * 0.8)

    print("[v] Scrolling done.")


async def extract_posts_from_page(page: Page, max_posts: int = 15) -> list[RawPost]:
    """
    Extract posts from current LinkedIn feed page.
    
    Strategy:
    1. Try DOM extraction (fast, reliable)
    2. Fallback to screenshot + text extraction if DOM fails
    """
    print(f"[->] Extracting up to {max_posts} posts from feed...")

    posts = []

    # -- DOM Extraction --------------------------------------------------------
    # LinkedIn's feed posts are inside data-id containers
    # We use multiple selector strategies for resilience
    
    post_elements = await page.query_selector_all(
        ".scaffold-finite-scroll__content > div, "
        "div.feed-shared-update-v2, "
        "div[data-urn], "
        "li[data-occludable-entity-urn], "
        ".occludable-update"
    )

    print(f"[i] Found {len(post_elements)} post containers in DOM.")

    if not post_elements:
        print("[!] DOM extraction found nothing. Feed structure may have changed.")
        print("[->] Attempting screenshot-based extraction...")
        return await extract_via_screenshot(page, max_posts)

    for i, element in enumerate(post_elements[:max_posts]):
        try:
            post = await extract_single_post(page, element, i)
            if post and len(post.post_text) > 30:  # Skip empty/tiny posts
                posts.append(post)
                print(f"    [{i+1}] Extracted: {post.author_name[:30]!r} - {post.post_text[:60]!r}...")
        except Exception as e:
            print(f"    [{i+1}] Skipped (error: {e})")
            continue

    print(f"[v] Successfully extracted {len(posts)} posts.")
    return posts


async def extract_single_post(page: Page, element, index: int) -> RawPost | None:
    """Extract data from a single post element."""

    # -- Author name -----------------------------------------------------------
    author_name = ""
    for selector in [
        ".feed-shared-actor__name",
        ".update-components-actor__name",
        "span.visually-hidden",  # LinkedIn sometimes hides name here
        ".actor-name",
    ]:
        try:
            el = await element.query_selector(selector)
            if el:
                author_name = clean_text(await el.inner_text())
                if author_name:
                    break
        except Exception:
            continue

    # -- Author headline -------------------------------------------------------
    author_headline = ""
    for selector in [
        ".feed-shared-actor__description",
        ".update-components-actor__description",
        ".actor-description",
    ]:
        try:
            el = await element.query_selector(selector)
            if el:
                author_headline = clean_text(await el.inner_text())
                if author_headline:
                    break
        except Exception:
            continue

    # -- Post text -------------------------------------------------------------
    post_text = ""
    for selector in [
        ".feed-shared-update-v2__description",
        ".feed-shared-text",
        ".update-components-text",
        "span[dir='ltr']",
        ".break-words",
    ]:
        try:
            el = await element.query_selector(selector)
            if el:
                post_text = clean_text(await el.inner_text())
                if len(post_text) > 20:
                    break
        except Exception:
            continue

    # -- Post URL --------------------------------------------------------------
    post_url = ""
    for selector in [
        "a[href*='/posts/']",
        "a[href*='/feed/update/']",
        "a[data-control-name='overlay']",
    ]:
        try:
            el = await element.query_selector(selector)
            if el:
                href = await el.get_attribute("href")
                if href:
                    post_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
                    break
        except Exception:
            continue

    # -- Media detection -------------------------------------------------------
    has_image = bool(await element.query_selector(".feed-shared-image, img[data-delayed-url]"))
    has_video = bool(await element.query_selector(".feed-shared-linkedin-video, video"))

    # -- Engagement numbers ----------------------------------------------------
    likes_approx = ""
    comments_approx = ""
    try:
        likes_el = await element.query_selector(
            ".social-details-social-counts__reactions-count, "
            "span[data-test-id='social-actions__reaction-count']"
        )
        if likes_el:
            likes_approx = clean_text(await likes_el.inner_text())
    except Exception:
        pass

    try:
        comments_el = await element.query_selector(
            ".social-details-social-counts__comments, "
            "li[data-test-id='social-actions__comments']"
        )
        if comments_el:
            comments_approx = clean_text(await comments_el.inner_text())
    except Exception:
        pass

    # -- Post ID (for deduplication) -------------------------------------------
    post_id = f"post_{index}_{hash(post_text[:50])}"
    try:
        data_id = await element.get_attribute("data-id")
        if data_id:
            post_id = data_id
    except Exception:
        pass

    # Skip posts with no meaningful content
    if not post_text and not author_name:
        return None

    return RawPost(
        post_id=post_id,
        author_name=author_name or "Unknown",
        author_headline=author_headline,
        post_text=post_text,
        post_url=post_url,
        has_image=has_image,
        has_video=has_video,
        likes_approx=likes_approx,
        comments_approx=comments_approx,
        extracted_at=datetime.now().isoformat(),
        screenshot_path="",
    )


async def extract_via_screenshot(page: Page, max_posts: int) -> list[RawPost]:
    """
    FALLBACK: Take a screenshot and use Gemini vision to read posts.
    Used when LinkedIn's DOM structure has changed and selectors break.
    """
    print("[->] Screenshot fallback: capturing feed...")
    Path("session").mkdir(exist_ok=True)
    screenshot_path = "session/feed_screenshot.png"
    await page.screenshot(path=screenshot_path, full_page=False)
    print(f"[v] Screenshot saved: {screenshot_path}")
    print("[i] Screenshot ready for vision analysis (handled in main agent).")

    # Return a placeholder that signals screenshot mode
    return [RawPost(
        post_id="screenshot_mode",
        author_name="[Screenshot Mode]",
        author_headline="DOM extraction failed",
        post_text="[See session/feed_screenshot.png for vision-based extraction]",
        post_url="",
        has_image=True,
        has_video=False,
        likes_approx="",
        comments_approx="",
        extracted_at=datetime.now().isoformat(),
        screenshot_path=screenshot_path,
    )]


async def extract_feed_posts(page: Page, max_posts: int = 15) -> list[RawPost]:
    """
    Full pipeline: navigate to feed -> scroll -> extract posts.
    This is the main function called by the agent.
    """
    from linkedin_login import LINKEDIN_FEED_URL

    # Go to feed if not already there
    if "feed" not in page.url:
        print("[->] Navigating to LinkedIn feed...")
        await page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)

    # Scroll to load more posts
    scroll_count = max(3, max_posts // 4)
    await scroll_feed(page, scroll_count=scroll_count)

    # Extract posts
    posts = await extract_posts_from_page(page, max_posts=max_posts)

    # Save raw extraction for debugging
    save_raw_posts(posts)

    return posts


def save_raw_posts(posts: list[RawPost], path: str = "session/raw_posts.json") -> None:
    """Save extracted posts to JSON for inspection/debugging."""
    Path("session").mkdir(exist_ok=True)
    data = [asdict(p) for p in posts]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[v] Raw posts saved to {path}")


# -- Standalone test -----------------------------------------------------------
async def main():
    """Run this directly to test extraction (requires login first)."""
    from dotenv import load_dotenv
    from linkedin_login import get_authenticated_browser

    load_dotenv()

    print("=" * 50)
    print("Feed Extraction Test")
    print("=" * 50)

    pw, browser, context, page = await get_authenticated_browser()

    try:
        posts = await extract_feed_posts(page, max_posts=10)

        print(f"\n{'='*50}")
        print(f"EXTRACTED {len(posts)} POSTS")
        print(f"{'='*50}")

        for i, post in enumerate(posts, 1):
            print(f"\n[Post {i}]")
            print(f"  Author:    {post.author_name}")
            print(f"  Headline:  {post.author_headline[:80]}")
            print(f"  Text:      {post.post_text[:200]}...")
            print(f"  URL:       {post.post_url[:80]}")
            print(f"  Likes:     {post.likes_approx}")

    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
