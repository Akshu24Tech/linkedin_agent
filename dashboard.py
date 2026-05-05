"""
dashboard.py - Day 4
Terminal dashboard. Shows run history, post stats, and saved posts.

Usage:
    python dashboard.py              # Show last 10 runs + stats
    python dashboard.py --posts      # Show all analyzed posts
    python dashboard.py --errors     # Show error log
"""

import json
import sys
from pathlib import Path


# ANSI colors
G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
B = "\033[94m"   # blue
C = "\033[96m"   # cyan
W = "\033[97m"   # white bold
DIM = "\033[2m"
RST = "\033[0m"


def score_color(score: int) -> str:
    if score >= 8:
        return G
    elif score >= 6:
        return Y
    else:
        return R


def show_runs():
    run_log = Path("session/run_log.json")
    if not run_log.exists():
        print(f"{Y}No run history yet. Run agent.py first.{RST}")
        return

    runs = json.loads(run_log.read_text())
    if not runs:
        print(f"{Y}No runs recorded yet.{RST}")
        return

    print(f"\n{W}{'=' * 60}{RST}")
    print(f"{W}  LINKEDIN AGENT DASHBOARD{RST}")
    print(f"{W}{'=' * 60}{RST}")

    # Totals across all runs
    total_found = sum(r.get("posts_found", 0) for r in runs)
    total_analyzed = sum(r.get("posts_analyzed", 0) for r in runs)
    total_saved = sum(r.get("posts_saved_notion", 0) for r in runs)
    total_relevant = sum(r.get("posts_relevant", 0) for r in runs)

    print(f"\n{C}  ALL TIME STATS ({len(runs)} runs){RST}")
    print(f"  Posts found      : {total_found}")
    print(f"  Posts analyzed   : {total_analyzed}")
    print(f"  Posts relevant   : {G}{total_relevant}{RST}")
    print(f"  Saved to Notion  : {G}{total_saved}{RST}")
    if total_analyzed:
        hit_rate = round(total_relevant / total_analyzed * 100)
        print(f"  Hit rate         : {hit_rate}% relevant")

    print(f"\n{C}  RECENT RUNS{RST}")
    print(f"  {'Date':<20} {'Found':>6} {'Relevant':>9} {'Saved':>6} {'Dur':>6}  {'Status'}")
    print(f"  {'-'*20} {'-'*6} {'-'*9} {'-'*6} {'-'*6}  {'-'*10}")

    for run in reversed(runs[-10:]):
        date = run.get("started_at", "?")[:16]
        found = run.get("posts_found", 0)
        relevant = run.get("posts_relevant", 0)
        saved = run.get("posts_saved_notion", 0)
        dur = f"{run.get('duration_seconds', 0):.0f}s"

        errors = run.get("errors", {})
        total_errs = sum(len(v) for v in errors.values())
        status = f"{R}[!]  {total_errs} err{RST}" if total_errs else f"{G}[OK] clean{RST}"

        rel_col = G if relevant > 0 else DIM
        print(f"  {date:<20} {found:>6} {rel_col}{relevant:>9}{RST} {saved:>6} {dur:>6}  {status}")

    print()


def show_posts():
    analyzed = Path("session/analyzed_posts.json")
    if not analyzed.exists():
        print(f"{Y}No analyzed posts yet. Run agent.py first.{RST}")
        return

    posts = json.loads(analyzed.read_text())
    if not posts:
        print(f"{Y}No relevant posts in last run.{RST}")
        return

    print(f"\n{W}{'=' * 60}{RST}")
    print(f"{W}  RELEVANT POSTS - LAST RUN ({len(posts)} posts){RST}")
    print(f"{W}{'=' * 60}{RST}\n")

    for i, post in enumerate(posts, 1):
        analysis = post.get("analysis", {})
        score = analysis.get("relevance_score", 0)
        col = score_color(score)

        print(f"  {W}[{i}]{RST} {post.get('author', 'Unknown')[:40]}")
        print(f"      {DIM}{post.get('role', '')[:60]}{RST}")
        print(f"      Score: {col}{score}/10{RST}  |  Topics: {', '.join(analysis.get('matched_interests', []))[:50]}")
        print(f"      > {analysis.get('key_insight', '')[:80]}")

        comment = analysis.get("comment_draft", "")
        if comment:
            print(f"      - {DIM}{comment[:80]}...{RST}" if len(comment) > 80 else f"      - {DIM}{comment}{RST}")

        url = post.get("url", "")
        if url:
            print(f"      > {B}{url[:70]}{RST}")

        print()


def show_errors():
    run_log = Path("session/run_log.json")
    if not run_log.exists():
        print(f"{Y}No run history yet.{RST}")
        return

    runs = json.loads(run_log.read_text())
    recent = runs[-5:] if runs else []

    print(f"\n{W}{'=' * 60}{RST}")
    print(f"{W}  ERROR LOG - LAST 5 RUNS{RST}")
    print(f"{W}{'=' * 60}{RST}\n")

    found_any = False
    for run in reversed(recent):
        errors = run.get("errors", {})
        all_errors = []
        for category, errs in errors.items():
            for e in errs:
                all_errors.append((category, e))

        if all_errors:
            found_any = True
            print(f"  {C}{run.get('started_at', '?')[:16]}{RST}")
            for cat, err in all_errors:
                print(f"    {R}[{cat}]{RST} {err}")
            print()

    if not found_any:
        print(f"  {G}No errors in recent runs [OK]{RST}\n")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--posts" in args:
        show_posts()
    elif "--errors" in args:
        show_errors()
    else:
        show_runs()
        print(f"{DIM}  Commands: python dashboard.py --posts | --errors{RST}\n")
