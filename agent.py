"""
agent.py - Day 2: Extract → Analyze → Filter
Run: python agent.py
Test analyzer only (no LinkedIn): python analyzer.py
"""

import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import asdict

load_dotenv()


def print_summary(all_results, saved_posts):
    total = len(all_results)
    saved = len(saved_posts)
    print("\n" + "="*58)
    print(f"  RUN COMPLETE: {saved} saved / {total} analyzed")
    print("="*58)
    for i, (post, analysis) in enumerate(saved_posts, 1):
        print(f"\n  [{i}] {post.author_name} ({analysis.relevance_score}/10)")
        print(f"       {analysis.post_summary[:110]}...")
        print(f"       Insight: {analysis.key_insight[:90]}...")
        if analysis.should_comment:
            print(f"       Comment drafted")
        if analysis.content_angle:
            print(f"       Angle: {analysis.content_angle[:70]}...")
    if not saved_posts:
        print("\n  No posts met threshold. Try running on a fuller feed session.")
    print()


async def run():
    from linkedin_login import get_authenticated_browser
    from feed_extractor import extract_feed_posts
    from analyzer import analyze_posts_batch, filter_saved_posts

    max_posts = int(os.getenv("POSTS_TO_COLLECT", "15"))

    print("\n" + "="*58)
    print("  LinkedIn Feed Intelligence Agent — Day 2")
    print("="*58 + "\n")

    # Step 1: Auth + Extract
    print("[STEP 1] Auth + Extraction")
    print("-" * 35)
    pw, browser, context, page = await get_authenticated_browser()

    try:
        posts = await extract_feed_posts(page, max_posts=max_posts)
        if not posts:
            print("[!] No posts extracted.")
            return
    finally:
        await browser.close()
        await pw.stop()
        print("[v] Browser closed.")

    # Step 2: AI Analysis
    print("\n[STEP 2] AI Analysis")
    print("-" * 35)
    all_results = analyze_posts_batch(posts, delay_between=1.5)
    saved_posts = filter_saved_posts(all_results)

    # Step 3: Persist
    Path("session").mkdir(exist_ok=True)
    output = [{"post": asdict(p), "analysis": a.model_dump()} for p, a in all_results]
    with open("session/analyzed_posts.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("[v] Saved -> session/analyzed_posts.json")

    print_summary(all_results, saved_posts)


if __name__ == "__main__":
    asyncio.run(run())