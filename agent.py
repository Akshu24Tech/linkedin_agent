"""
agent.py — LinkedIn Feed Intelligence Agent
Full pipeline: Profile list → Extract → Analyze → Save to Notion

COMMANDS:
  python agent.py                      Full run
  python agent.py --dry-run            Analyze but don't save to Notion
  python agent.py --extract-only       Just extract posts, skip analysis
  python agent.py --analyze-only       Re-analyze session/raw_posts.json
  python agent.py --view-saved         Pretty-print last saved posts + comment drafts
  python agent.py --stats              Show dedup stats + profile list
  python agent.py --test-notion        Test Notion connection
  python agent.py --generate-posts     Generate LinkedIn drafts from saved angles
  python agent.py --setup-check        Validate full environment
  python agent.py --clear-seen         Reset dedup store

PROFILE MANAGEMENT (use profiles.py directly):
  python profiles.py add "Harrison Chase"
  python profiles.py add "Andrej Karpathy" --username karpathy
  python profiles.py remove "Harrison Chase"
  python profiles.py list
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


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="LinkedIn Feed Intelligence Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--extract-only",   action="store_true")
    parser.add_argument("--analyze-only",   action="store_true")
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--stats",          action="store_true")
    parser.add_argument("--test-notion",    action="store_true")
    parser.add_argument("--view-saved",     action="store_true")
    parser.add_argument("--clear-seen",     action="store_true")
    parser.add_argument("--generate-posts", action="store_true")
    parser.add_argument("--setup-check",    action="store_true")
    parser.add_argument("--threshold", type=int, default=7)
    return parser.parse_args()


# ── One-shot commands ─────────────────────────────────────────────────────────

def cmd_stats():
    from dedup_store import stats as dedup_stats
    from profiles import load_profiles, print_profiles_table

    ds = dedup_stats()
    print("\n" + "═"*55)
    print("  Agent Stats")
    print("═"*55)
    print(f"  Posts seen (dedup):  {ds['total_seen']}")
    print(f"  Last updated:        {ds['last_updated']}")
    print(f"  Log file:            {LOG_FILE}")

    if Path("session/analyzed_posts.json").exists():
        with open("session/analyzed_posts.json") as f:
            data = json.load(f)
        saved = [d for d in data if d["analysis"]["should_save"] and d["analysis"]["relevance_score"] >= 7]
        print(f"  Last run:            {len(data)} analyzed, {len(saved)} saved")

    if Path("session/notion_db_id.txt").exists():
        db_id = Path("session/notion_db_id.txt").read_text().strip()
        print(f"  Notion DB:           {db_id[:8]}...{db_id[-4:]}")

    profiles = load_profiles()
    print_profiles_table(profiles)


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
    p = Path("session/seen_posts.json")
    if p.exists():
        p.unlink()
        print("[✓] Dedup store cleared.")
    else:
        print("[i] Already empty.")


def cmd_test_notion():
    from notion_saver import get_or_create_database, get_headers
    import requests
    print("\n[→] Testing Notion...")
    try:
        res = requests.get("https://api.notion.com/v1/users/me", headers=get_headers())
        if res.status_code == 200:
            print(f"[✓] Auth OK — {res.json().get('name', 'Unknown')}")
            db_id = get_or_create_database()
            print(f"[✓] Database ready: {db_id[:8]}...{db_id[-4:]}")
        else:
            print(f"[✗] Auth failed: {res.status_code}")
    except Exception as e:
        print(f"[✗] {e}")


# ── Pipeline steps ────────────────────────────────────────────────────────────

async def step_extract() -> list:
    """Extract latest posts from all tracked profiles."""
    from profile_extractor import extract_from_all_profiles
    return await extract_from_all_profiles(max_posts_per_profile=2)


def step_analyze(posts: list, threshold: int = 7) -> tuple[list, list]:
    from dedup_store import filter_new_posts, mark_posts_seen
    from analyzer import analyze_posts_batch, filter_saved_posts

    new_posts, skipped = filter_new_posts(posts)
    if not new_posts:
        log.info("[analyze] All posts already seen.")
        return [], []

    log.info(f"[analyze] {len(new_posts)} new posts to analyze ({skipped} already seen)")
    all_results = analyze_posts_batch(new_posts, delay_between=1.5)
    mark_posts_seen(new_posts)

    saved = [
        (p, a) for p, a in all_results
        if a.is_relevant and a.relevance_score >= threshold and a.should_save
    ]
    return all_results, saved


def step_save_notion(all_results: list) -> int:
    from notion_saver import save_posts_to_notion
    return save_posts_to_notion(all_results)


def step_persist_json(all_results: list) -> None:
    Path("session").mkdir(exist_ok=True)
    output = [{"post": asdict(p), "analysis": a.model_dump()} for p, a in all_results]
    with open("session/analyzed_posts.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def load_raw_posts_from_json() -> list:
    from schemas import RawPost
    path = Path("session/raw_posts.json")
    if not path.exists():
        log.error("session/raw_posts.json not found. Run extraction first.")
        sys.exit(1)
    with open(path) as f:
        data = json.load(f)
    return [RawPost(**p) for p in data]


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(all_results, saved_posts, notion_count, dry_run=False):
    total = len(all_results)
    saved = len(saved_posts)
    print("\n" + "═"*60)
    print(f"  RUN COMPLETE  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═"*60)
    print(f"  Analyzed:       {total}")
    print(f"  Relevant (≥7):  {saved}")
    print(f"  Notion:         {'[dry run]' if dry_run else notion_count}")
    print("═"*60)

    for i, (post, analysis) in enumerate(saved_posts, 1):
        bar = "█" * analysis.relevance_score + "░" * (10 - analysis.relevance_score)
        print(f"\n  [{i}] [{bar}] {analysis.relevance_score}/10  {post.author_name}")
        print(f"       {analysis.post_summary[:100]}...")
        print(f"       💡 {analysis.key_insight[:85]}...")
        if analysis.comment_draft:
            print(f"       💬 Comment ready — run --view-saved to copy")
        if analysis.content_angle:
            print(f"       ✍️  {analysis.content_angle[:70]}...")

    if not saved_posts:
        print("\n  No posts met threshold.")
        print("  Tips: --clear-seen to reprocess | --threshold 6 to lower bar")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()

    if args.stats:          cmd_stats(); return
    if args.view_saved:     cmd_view_saved(); return
    if args.clear_seen:     cmd_clear_seen(); return
    if args.test_notion:    cmd_test_notion(); return
    if args.setup_check:
        import setup_check; setup_check.main(); return
    if args.generate_posts:
        import post_generator; post_generator.main(); return

    # ── Check profiles exist before doing anything ────────────────────────────
    from profiles import load_profiles
    if not args.analyze_only:
        profiles = load_profiles()
        if not profiles:
            print("\n[!] No profiles tracked yet. Add some first:")
            print('    python profiles.py add "Harrison Chase"')
            print('    python profiles.py add "Andrej Karpathy" --username karpathy')
            print('    python profiles.py add "Shreya Shankar"')
            print()
            return

    print("\n" + "═"*60)
    print("  LinkedIn Feed Intelligence Agent")
    print("  Profile-based extraction → Gemini analysis → Notion")
    print("═"*60)

    # ── Extract ───────────────────────────────────────────────────────────────
    if args.analyze_only:
        print("\n[STEP 1] Loading from session/raw_posts.json")
        print("─"*42)
        posts = load_raw_posts_from_json()
    else:
        print(f"\n[STEP 1] Extracting from {len(profiles)} profiles")
        print("─"*42)
        try:
            posts = await step_extract()
        except Exception as e:
            log.error(f"[extract] {e}")
            print(f"\n[✗] Extraction failed: {e}")
            sys.exit(1)

        if not posts:
            print("[!] No posts extracted.")
            print("    Check session/debug_*.png screenshots for clues.")
            print("    Or verify profiles: python profiles.py list")
            return

    if args.extract_only:
        print(f"\n[✓] Extracted {len(posts)} posts → session/raw_posts.json")
        return

    # ── Analyze ───────────────────────────────────────────────────────────────
    print(f"\n[STEP 2] AI Analysis ({len(posts)} posts)")
    print("─"*42)
    try:
        all_results, saved_posts = step_analyze(posts, threshold=args.threshold)
    except Exception as e:
        log.error(f"[analyze] {e}")
        print(f"\n[✗] Analysis failed: {e}")
        sys.exit(1)

    if not all_results:
        print("[i] Nothing new to analyze.")
        return

    step_persist_json(all_results)

    # ── Notion ────────────────────────────────────────────────────────────────
    notion_count = 0
    if not args.dry_run:
        print(f"\n[STEP 3] Saving to Notion")
        print("─"*42)
        try:
            notion_count = step_save_notion(all_results)
        except Exception as e:
            log.error(f"[notion] {e}")
            print(f"[!] Notion save failed: {e}")
            print("    Posts saved locally to session/analyzed_posts.json")
    else:
        print("\n[STEP 3] Notion SKIPPED (--dry-run)")

    print_summary(all_results, saved_posts, notion_count, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
