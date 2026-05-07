"""
agent.py
─────────
LinkedIn Feed Intelligence Agent — full hardened pipeline.

COMMANDS:
  python agent.py                  # Full run: extract → analyze → save
  python agent.py --extract-only   # Just grab posts, save raw JSON
  python agent.py --analyze-only   # Re-analyze session/raw_posts.json (no LinkedIn)
  python agent.py --dry-run        # Analyze but don't save to Notion
  python agent.py --stats          # Show run history and dedup stats
  python agent.py --test-notion    # Test Notion connection only
  python agent.py --view-saved     # Pretty-print last saved posts from JSON
  python agent.py --clear-seen     # Reset dedup store (re-process all posts)
"""

import asyncio
import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from dataclasses import asdict

load_dotenv()

from logger import setup_logger, LOG_FILE
log = setup_logger("agent")


# ── CLI Parser ────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="LinkedIn Feed Intelligence Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent.py                  Full run (extract + analyze + save)
  python agent.py --dry-run        Full run but skip Notion save
  python agent.py --analyze-only   Re-analyze last extraction (no browser)
  python agent.py --test-notion    Verify Notion connection
  python agent.py --stats          Show dedup stats and run history
  python agent.py --view-saved     Print last saved posts to terminal
        """
    )
    parser.add_argument("--extract-only",  action="store_true", help="Extract posts, skip analysis")
    parser.add_argument("--analyze-only",  action="store_true", help="Analyze raw_posts.json, skip extraction")
    parser.add_argument("--dry-run",       action="store_true", help="Analyze but don't save to Notion")
    parser.add_argument("--stats",         action="store_true", help="Show agent stats and exit")
    parser.add_argument("--test-notion",   action="store_true", help="Test Notion connection and exit")
    parser.add_argument("--view-saved",    action="store_true", help="Pretty-print last analyzed posts")
    parser.add_argument("--clear-seen",    action="store_true", help="Reset dedup store")
    parser.add_argument("--posts", type=int, default=None, help="Override POSTS_TO_COLLECT")
    parser.add_argument("--threshold", type=int, default=7, help="Min score to save (default: 7)")
    return parser.parse_args()


# ── Individual Commands ───────────────────────────────────────────────────────

def cmd_stats():
    from dedup_store import stats as dedup_stats
    ds = dedup_stats()
    print("\n" + "═"*50)
    print("  Agent Stats")
    print("═"*50)
    print(f"  Dedup store:    {ds['total_seen']} posts seen")
    print(f"  Last updated:   {ds['last_updated']}")

    if Path("session/analyzed_posts.json").exists():
        with open("session/analyzed_posts.json") as f:
            data = json.load(f)
        saved = [d for d in data if d["analysis"]["should_save"] and d["analysis"]["relevance_score"] >= 7]
        print(f"  Last run:       {len(data)} analyzed, {len(saved)} saved")

    print(f"  Log file:       {LOG_FILE}")
    if Path("session/notion_db_id.txt").exists():
        db_id = Path("session/notion_db_id.txt").read_text().strip()
        print(f"  Notion DB:      {db_id[:8]}...{db_id[-4:]}")
    print()


def cmd_view_saved():
    path = Path("session/analyzed_posts.json")
    if not path.exists():
        print("[!] No analyzed_posts.json found. Run the agent first.")
        return

    with open(path) as f:
        data = json.load(f)

    saved = [d for d in data if d["analysis"]["should_save"] and d["analysis"]["relevance_score"] >= 7]

    print(f"\n{'═'*60}")
    print(f"  Saved Posts ({len(saved)} from last run)")
    print(f"{'═'*60}")

    for i, item in enumerate(saved, 1):
        p = item["post"]
        a = item["analysis"]
        score_bar = "█" * a["relevance_score"] + "░" * (10 - a["relevance_score"])

        print(f"\n[{i}] {p['author_name']}  [{score_bar}] {a['relevance_score']}/10")
        print(f"     {p['author_headline'][:70]}")
        print(f"\n     SUMMARY:")
        print(f"     {a['post_summary']}")
        print(f"\n     💡 INSIGHT:")
        print(f"     {a['key_insight']}")

        if a["comment_draft"]:
            print(f"\n     💬 COMMENT DRAFT:")
            print(f"     {a['comment_draft']}")

        if a["content_angle"]:
            print(f"\n     ✍️  CONTENT ANGLE:")
            print(f"     {a['content_angle']}")

        if p["post_url"]:
            print(f"\n     🔗 {p['post_url'][:80]}")

        print("\n     " + "─"*55)

    if not saved:
        print("\n  No posts met save threshold in last run.")
    print()


def cmd_clear_seen():
    store_path = Path("session/seen_posts.json")
    if store_path.exists():
        store_path.unlink()
        print("[✓] Dedup store cleared. All posts will be re-analyzed next run.")
    else:
        print("[i] Dedup store was already empty.")


def cmd_test_notion():
    from notion_saver import get_or_create_database, get_headers
    import requests

    print("\n[→] Testing Notion connection...")
    try:
        headers = get_headers()
        # Test auth
        res = requests.get("https://api.notion.com/v1/users/me", headers=headers)
        if res.status_code == 200:
            user = res.json()
            print(f"[✓] Notion auth OK — integration: {user.get('name', 'Unknown')}")
        else:
            print(f"[✗] Auth failed: {res.status_code} {res.text[:100]}")
            return

        # Test DB access
        db_id = get_or_create_database()
        print(f"[✓] Database ready: {db_id[:8]}...{db_id[-4:]}")
        print("[✓] Notion connection fully working!")

    except Exception as e:
        print(f"[✗] Notion error: {e}")


# ── Core Pipeline Steps ───────────────────────────────────────────────────────

async def step_extract(max_posts: int) -> list:
    """Step 1: Open LinkedIn, extract posts."""
    from linkedin_login import get_authenticated_browser
    from feed_extractor import extract_feed_posts

    log.info(f"[extract] Starting browser, targeting {max_posts} posts")
    pw, browser, context, page = await get_authenticated_browser()

    try:
        posts = await extract_feed_posts(page, max_posts=max_posts)

        # Vision fallback if DOM extraction returned nothing useful
        real_posts = [p for p in posts if p.post_id != "screenshot_mode"]
        if not real_posts and Path("session/feed_screenshot.png").exists():
            log.warning("[extract] DOM extraction empty — trying vision fallback...")
            from vision_fallback import run_vision_fallback
            posts = run_vision_fallback("session/feed_screenshot.png")
            if posts:
                log.info(f"[extract] Vision fallback got {len(posts)} posts")
            else:
                log.error("[extract] Vision fallback also failed. Check screenshot manually.")

        return posts

    finally:
        await browser.close()
        await pw.stop()
        log.info("[extract] Browser closed.")


def step_analyze(posts: list, threshold: int = 7) -> tuple[list, list]:
    """Step 2: Filter new posts, run Gemini analysis."""
    from dedup_store import filter_new_posts, mark_posts_seen
    from analyzer import analyze_posts_batch, filter_saved_posts

    # Skip already-seen posts
    new_posts, skipped = filter_new_posts(posts)

    if not new_posts:
        log.info("[analyze] All posts already seen — nothing new to analyze.")
        return [], []

    log.info(f"[analyze] Analyzing {len(new_posts)} new posts ({skipped} skipped as seen)")
    all_results = analyze_posts_batch(new_posts, delay_between=1.5)

    # Mark all as seen (regardless of whether saved — don't re-check next run)
    mark_posts_seen(new_posts)

    # Filter by threshold
    saved = [
        (p, a) for p, a in all_results
        if a.is_relevant and a.relevance_score >= threshold and a.should_save
    ]

    return all_results, saved


def step_save_notion(all_results: list) -> int:
    """Step 3: Save relevant posts to Notion."""
    from notion_saver import save_posts_to_notion
    return save_posts_to_notion(all_results)


def step_persist_json(all_results: list) -> None:
    """Always save full results to JSON as local backup."""
    Path("session").mkdir(exist_ok=True)
    output = [{"post": asdict(p), "analysis": a.model_dump()} for p, a in all_results]
    with open("session/analyzed_posts.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log.info("[persist] Saved → session/analyzed_posts.json")


def load_raw_posts_from_json() -> list:
    """Load previously extracted raw posts from JSON (for --analyze-only)."""
    path = Path("session/raw_posts.json")
    if not path.exists():
        log.error("[load] session/raw_posts.json not found. Run extraction first.")
        sys.exit(1)

    from feed_extractor import RawPost
    with open(path) as f:
        data = json.load(f)

    posts = [RawPost(**p) for p in data]
    log.info(f"[load] Loaded {len(posts)} posts from raw_posts.json")
    return posts


# ── Print Summary ─────────────────────────────────────────────────────────────

def print_run_summary(all_results, saved_posts, notion_count, dry_run=False):
    total = len(all_results)
    saved = len(saved_posts)

    print("\n" + "═"*60)
    print(f"  RUN COMPLETE  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═"*60)
    print(f"  Analyzed:      {total} posts")
    print(f"  Relevant:      {saved} posts (scored ≥7/10)")

    if dry_run:
        print(f"  Notion:        [DRY RUN — not saved]")
    else:
        print(f"  Saved Notion:  {notion_count}")

    print(f"  Log:           {LOG_FILE}")
    print("═"*60)

    if saved_posts:
        print()
        for i, (post, analysis) in enumerate(saved_posts, 1):
            score_bar = "█" * analysis.relevance_score + "░" * (10 - analysis.relevance_score)
            print(f"  [{i}] [{score_bar}] {analysis.relevance_score}/10  {post.author_name}")
            print(f"       {analysis.post_summary[:100]}...")
            print(f"       💡 {analysis.key_insight[:85]}...")
            if analysis.comment_draft:
                print(f"       💬 Comment drafted — run --view-saved to copy it")
            if analysis.content_angle:
                print(f"       ✍️  {analysis.content_angle[:70]}...")
            print()
    else:
        print("\n  No posts met threshold. Tips:")
        print("  • Run --clear-seen to reprocess old posts")
        print("  • Lower --threshold to 6 to capture more")
        print("  • Try scrolling more (increase POSTS_TO_COLLECT in .env)")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()

    # ── One-shot commands ─────────────────────────────────────────────────────
    if args.stats:
        cmd_stats()
        return

    if args.view_saved:
        cmd_view_saved()
        return

    if args.clear_seen:
        cmd_clear_seen()
        return

    if args.test_notion:
        cmd_test_notion()
        return

    # ── Pipeline ──────────────────────────────────────────────────────────────
    max_posts = args.posts or int(os.getenv("POSTS_TO_COLLECT", "15"))

    print("\n" + "═"*60)
    print("  LinkedIn Feed Intelligence Agent")
    print("═"*60)

    # ── Extract ───────────────────────────────────────────────────────────────
    if args.analyze_only:
        print("\n[STEP 1] Loading from session/raw_posts.json (--analyze-only)")
        print("─"*42)
        posts = load_raw_posts_from_json()
    else:
        print(f"\n[STEP 1] Extraction ({max_posts} posts)")
        print("─"*42)
        try:
            posts = await step_extract(max_posts)
        except Exception as e:
            log.error(f"[extract] Fatal error: {e}")
            print(f"\n[✗] Extraction failed: {e}")
            print("    Check session/agent.log for details")
            sys.exit(1)

        if not posts:
            print("[✗] No posts extracted. Exiting.")
            sys.exit(1)

    if args.extract_only:
        print(f"\n[✓] Extracted {len(posts)} posts. Saved to session/raw_posts.json")
        print("    Run again without --extract-only to analyze.")
        return

    # ── Analyze ───────────────────────────────────────────────────────────────
    print(f"\n[STEP 2] AI Analysis")
    print("─"*42)
    try:
        all_results, saved_posts = step_analyze(posts, threshold=args.threshold)
    except Exception as e:
        log.error(f"[analyze] Fatal error: {e}")
        print(f"\n[✗] Analysis failed: {e}")
        sys.exit(1)

    if not all_results:
        print("[i] Nothing new to analyze this run.")
        return

    step_persist_json(all_results)

    # ── Save to Notion ────────────────────────────────────────────────────────
    notion_count = 0
    if not args.dry_run:
        print(f"\n[STEP 3] Saving to Notion")
        print("─"*42)
        try:
            notion_count = step_save_notion(all_results)
        except Exception as e:
            log.error(f"[notion] Save failed: {e}")
            print(f"[!] Notion save failed: {e}")
            print("    Posts saved locally to session/analyzed_posts.json")
    else:
        print("\n[STEP 3] Notion save SKIPPED (--dry-run)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print_run_summary(all_results, saved_posts, notion_count, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())