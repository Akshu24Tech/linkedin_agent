"""
agent.py
─────────
Main entry point for the LinkedIn Feed Intelligence Agent.

Day 1 version: Login + Feed Extraction only.
Days 2+ will add: AI analysis, interest matching, Notion saving.

Run:
  python agent.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


async def run_day1():
    """
    Day 1 Task:
    [v] Open LinkedIn automatically
    [v] Persist session via cookies (no re-login every time)
    [v] Scroll feed
    [v] Extract posts (DOM + screenshot fallback)
    [v] Save raw output for inspection
    """
    from linkedin_login import get_authenticated_browser
    from feed_extractor import extract_feed_posts

    max_posts = int(os.getenv("POSTS_TO_COLLECT", "15"))

    print("\n" + "="*55)
    print("  LinkedIn Feed Intelligence Agent - Day 1")
    print("="*55)
    print(f"  Target: {max_posts} posts")
    print(f"  Headless: {os.getenv('HEADLESS', 'False')}")
    print("="*55 + "\n")

    # Step 1: Get authenticated browser
    print("[STEP 1] Authentication")
    print("-" * 30)
    pw, browser, context, page = await get_authenticated_browser()

    try:
        # Step 2: Extract feed posts
        print("\n[STEP 2] Feed Extraction")
        print("-" * 30)
        posts = await extract_feed_posts(page, max_posts=max_posts)

        # Step 3: Summary
        print("\n" + "="*55)
        print(f"  DAY 1 COMPLETE [v]")
        print("="*55)
        print(f"  Posts extracted:   {len(posts)}")
        print(f"  Raw data saved:    session/raw_posts.json")
        print(f"  Screenshot:        session/feed_screenshot.png (if fallback used)")
        print("\n  Next: Day 2 - AI interest matching with Gemini")
        print("="*55 + "\n")

        # Print a quick preview
        print("PREVIEW (first 3 posts):")
        print("-" * 40)
        for post in posts[:3]:
            print(f"\n  [Author] {post.author_name}")
            if post.author_headline:
                print(f"     {post.author_headline[:70]}")
            print(f"  [Text] {post.post_text[:150]}...")
            if post.post_url:
                print(f"  [Link] {post.post_url[:70]}")

    finally:
        await browser.close()
        await pw.stop()
        print("\n[v] Browser closed cleanly.")


if __name__ == "__main__":
    asyncio.run(run_day1())
