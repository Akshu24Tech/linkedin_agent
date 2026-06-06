"""
memory.py
─────────
SQLite-backed memory for the LinkedIn Feed Intelligence Agent.

Replaces:
  - dedup_store.py     (seen_posts.json → posts table, indexed)
  - profiles.json      (flat file → persons table, with stats)

Three tables:
  persons   — who you track + intelligence built over time
              (avg score, top topics, frequency, skip logic)
  posts     — every post ever seen, indexed by post_id + url
              (dedup at any scale, sub-millisecond lookup)
  analyses  — every Gemini result stored locally
              (never re-analyze the same post)

Token efficiency logic:
  Before opening browser for a person:
    → check avg_score history → if consistently irrelevant, skip
  Before calling Gemini:
    → check posts table → if seen, skip entirely
  Result: 100 profiles → maybe 15-20 actual Gemini calls/day

Database: session/memory.db (single file, zero server needed)

CLI:
  python memory.py persons list

  # Best — paste full LinkedIn URL (guarantees exact person)
  python memory.py persons add "https://www.linkedin.com/in/karpathy/"
  python memory.py persons add "https://www.linkedin.com/in/yann-lecun/" --name "Yann LeCun"

  # Also works — just the username slug from the URL
  python memory.py persons add "karpathy"
  python memory.py persons add "yann-lecun" --name "Yann LeCun"

  # Risky — name-only (only safe for unique names)
  python memory.py persons add --name "Harrison Chase" --username "harrison-chase"

  python memory.py persons remove "karpathy"
  python memory.py posts stats
  python memory.py posts clear-seen
"""

import sqlite3
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
from logger import setup_logger

log = setup_logger(__name__)

DB_PATH = Path("session/memory.db")


# ── DB Connection ─────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """Context manager for SQLite connection with WAL mode for reliability."""
    Path("session").mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as conn:
        conn.executescript("""
            -- People you track
            CREATE TABLE IF NOT EXISTS persons (
                username            TEXT PRIMARY KEY,
                display_name        TEXT NOT NULL,
                activity_url        TEXT NOT NULL,
                profile_url         TEXT NOT NULL,
                note                TEXT DEFAULT '',
                added_at            TEXT NOT NULL,
                last_checked        TEXT,

                -- Intelligence built over time
                total_runs          INTEGER DEFAULT 0,
                total_posts_seen    INTEGER DEFAULT 0,
                total_posts_saved   INTEGER DEFAULT 0,
                avg_relevance_score REAL DEFAULT 0.0,
                score_history       TEXT DEFAULT '[]',  -- JSON list of last 10 scores
                top_topics          TEXT DEFAULT '[]',  -- JSON list of frequent topics
                is_active           INTEGER DEFAULT 1   -- 0 = paused/removed
            );

            -- Every post ever seen (dedup + history)
            CREATE TABLE IF NOT EXISTS posts (
                post_id         TEXT PRIMARY KEY,
                post_url        TEXT,
                author_username TEXT,
                author_name     TEXT,
                post_text       TEXT,
                extracted_at    TEXT NOT NULL,
                was_analyzed    INTEGER DEFAULT 0,  -- 1 if Gemini was called
                was_saved       INTEGER DEFAULT 0,  -- 1 if saved to Notion
                linkedin_saved  INTEGER DEFAULT 0,  -- 1 if saved to LinkedIn Saved Posts
                relevance_score INTEGER DEFAULT 0,
                FOREIGN KEY (author_username) REFERENCES persons(username)
            );

            -- Every Gemini analysis result (never re-analyze)
            CREATE TABLE IF NOT EXISTS analyses (
                post_id         TEXT PRIMARY KEY,
                analyzed_at     TEXT NOT NULL,
                is_relevant     INTEGER,
                relevance_score INTEGER,
                matched_topics  TEXT,   -- JSON list
                post_summary    TEXT,
                key_insight     TEXT,
                content_type    TEXT,
                should_comment  INTEGER,
                comment_draft   TEXT,
                should_save     INTEGER,
                content_angle   TEXT,
                skip_reason     TEXT,
                FOREIGN KEY (post_id) REFERENCES posts(post_id)
            );

            -- Indexes for fast lookups
            CREATE INDEX IF NOT EXISTS idx_posts_url
                ON posts(post_url);
            CREATE INDEX IF NOT EXISTS idx_posts_author
                ON posts(author_username);
            CREATE INDEX IF NOT EXISTS idx_posts_saved
                ON posts(was_saved);
            CREATE INDEX IF NOT EXISTS idx_persons_active
                ON persons(is_active);
        """)

        # ── Migration: add linkedin_saved column to existing DBs ────────────
        # ALTER TABLE ignores the ADD COLUMN if it already exists in SQLite 3.37+
        # For older SQLite we catch the error gracefully.
        try:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN linkedin_saved INTEGER DEFAULT 0"
            )
            log.info("[memory] Migrated: added linkedin_saved column to posts")
        except Exception:
            pass  # column already exists — no action needed

    log.info(f"[memory] DB ready at {DB_PATH}")


# ── URL Helpers ───────────────────────────────────────────────────────────────

def name_to_username(name: str) -> str:
    """'Andrew Ng' → 'andrew-ng'  (best-effort only, not guaranteed unique)"""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def username_from_url(url: str) -> str | None:
    """
    Extract LinkedIn vanity name from any profile URL format.

    Handles all real-world copy-paste formats:
      https://www.linkedin.com/in/yatinbhalla42?utm_source=share&utm_content=profile&utm_medium=member_android
      https://linkedin.com/in/karpathy/
      linkedin.com/in/andrew-ng
      https://www.linkedin.com/in/yann-lecun/recent-activity/all/

    LinkedIn vanity names are globally unique — no two people share one.
    Everything after ? is just UTM tracking, safely stripped.

    Returns None if URL doesn't look like a LinkedIn profile URL.
    """
    if not url:
        return None

    url = url.strip()

    # Strip UTM params and everything after ?
    url = url.split("?")[0]

    # Strip trailing slashes + any sub-paths (like /recent-activity/all/)
    # We only want the vanity name segment right after /in/
    match = re.search(r"linkedin[.]com/in/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1).lower()

    return None


def build_activity_url(username: str) -> str:
    return f"https://www.linkedin.com/in/{username}/recent-activity/all/"


def build_profile_url(username: str) -> str:
    return f"https://www.linkedin.com/in/{username}/"


# ── Persons API ───────────────────────────────────────────────────────────────

def add_person(name: str, username: str = None, url: str = None, note: str = "") -> dict:
    """
    Add a person to track.

    Priority for resolving username:
      1. --url  "https://linkedin.com/in/karpathy/"  → extracts 'karpathy' (most reliable)
      2. --username karpathy                          → use exactly as given
      3. name only "Andrej Karpathy"                 → auto-derive (risky for common names)

    LinkedIn allows duplicate display names — username is the only unique identifier.
    Always prefer --url or --username for anyone with a common name.
    """
    init_db()

    # Priority 1: extract from URL (most reliable — copy from their LinkedIn profile)
    if url:
        extracted = username_from_url(url)
        if extracted:
            username = extracted
            log.info(f"[memory] Username from URL: '{username}'")
        else:
            log.warning(f"[memory] Could not parse username from URL: {url}")
            log.warning(f"         Expected format: linkedin.com/in/<username>")

    # Priority 2: explicit username provided
    if not username and url is None:
        # Auto-derive — warn user to verify
        username = name_to_username(name)
        log.info(f"[memory] Auto-derived username: '{username}'")
        log.warning(
            f"[memory] ⚠ LinkedIn has duplicate names — verify this is the right person:"
        )
        log.warning(f"         Open: https://www.linkedin.com/in/{username}/")
        log.warning(
            f"         If wrong, re-add with URL: "
            f"python profiles.py add \"{name}\" --url \"https://linkedin.com/in/<correct-username>/\""
        )
    elif not username:
        # URL was given but parsing failed — fall back to name
        username = name_to_username(name)
        log.warning(f"[memory] URL parse failed, falling back to: '{username}'"
        )

    with get_db() as conn:
        # Check duplicate
        existing = conn.execute(
            "SELECT username FROM persons WHERE username = ?", (username,)
        ).fetchone()

        if existing:
            log.info(f"[memory] '{name}' already tracked (username: {username})")
            return get_person(username)

        conn.execute("""
            INSERT INTO persons
                (username, display_name, activity_url, profile_url, note, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            username,
            name,
            build_activity_url(username),
            build_profile_url(username),
            note,
            datetime.now().isoformat(),
        ))

    log.info(f"[memory] Added: {name} → {build_activity_url(username)}")
    return get_person(username)


def remove_person(identifier: str) -> bool:
    """
    Soft delete — sets is_active=0 so history is preserved.
    Accepts: username slug, display name, or full LinkedIn URL.
    """
    init_db()

    # Extract username if URL given
    if "linkedin.com/in/" in identifier:
        parsed = username_from_url(identifier)
        if parsed:
            identifier = parsed

    with get_db() as conn:
        # Try exact username match first, then display name
        result = conn.execute(
            "UPDATE persons SET is_active = 0 WHERE username = ? OR display_name = ?",
            (identifier, identifier)
        )
        if result.rowcount == 0:
            log.warning(f"[memory] '{identifier}' not found in tracked profiles")
            return False

    log.info(f"[memory] Removed: {identifier} (history preserved in DB)")
    return True


def get_person(username: str) -> dict | None:
    """Get a single person by username."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM persons WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_all_persons(active_only: bool = True) -> list[dict]:
    """Get all tracked persons."""
    init_db()
    with get_db() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM persons WHERE is_active = 1 ORDER BY display_name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM persons ORDER BY display_name"
            ).fetchall()
    return [dict(r) for r in rows]


def update_person_after_run(
    username: str,
    posts_seen: int,
    posts_saved: int,
    scores: list[int],
    topics: list[str],
) -> None:
    """
    Update person stats after a run.
    Called by agent.py after each profile is processed.
    Builds intelligence over time — avg score, top topics, frequency.
    """
    init_db()
    person = get_person(username)
    if not person:
        return

    # Update score history (keep last 10)
    history = json.loads(person["score_history"] or "[]")
    history.extend(scores)
    history = history[-10:]

    # Recalculate avg
    avg = sum(history) / len(history) if history else 0.0

    # Merge topics (keep top 10 most frequent)
    existing_topics = json.loads(person["top_topics"] or "[]")
    all_topics = existing_topics + topics
    # Simple frequency count
    topic_freq: dict[str, int] = {}
    for t in all_topics:
        topic_freq[t] = topic_freq.get(t, 0) + 1
    top_topics = sorted(topic_freq, key=topic_freq.get, reverse=True)[:10]

    with get_db() as conn:
        conn.execute("""
            UPDATE persons SET
                last_checked        = ?,
                total_runs          = total_runs + 1,
                total_posts_seen    = total_posts_seen + ?,
                total_posts_saved   = total_posts_saved + ?,
                avg_relevance_score = ?,
                score_history       = ?,
                top_topics          = ?
            WHERE username = ?
        """, (
            datetime.now().isoformat(),
            posts_seen,
            posts_saved,
            round(avg, 2),
            json.dumps(history),
            json.dumps(top_topics),
            username,
        ))


def reset_verification(username: str) -> None:
    """
    Reset checked stats and intelligence history for a person so they
    are immediately checked on the next run and their history is rebuilt.
    """
    init_db()
    with get_db() as conn:
        conn.execute("""
            UPDATE persons SET
                last_checked        = NULL,
                total_runs          = 0,
                total_posts_seen    = 0,
                total_posts_saved   = 0,
                avg_relevance_score = 0.0,
                score_history       = '[]',
                top_topics          = '[]'
            WHERE username = ?
        """, (username,))
    log.info(f"[memory] Reset verification status and stats for: {username}")


def should_skip_person(username: str, min_avg_score: float = 3.0) -> tuple[bool, str]:
    """
    Token efficiency gate — should we even check this person today?

    Skips if:
    - They've been checked in the last 20 hours (avoid duplicate daily runs)
    - They have 5+ runs AND avg score is consistently below min_avg_score
      (consistently irrelevant to your interests — not worth browser time)

    Returns (should_skip: bool, reason: str)
    """
    init_db()
    person = get_person(username)
    if not person:
        return False, ""

    # Check if checked too recently (within 20 hours)
    last = person.get("last_checked")
    if last:
        last_dt = datetime.fromisoformat(last)
        if datetime.now() - last_dt < timedelta(hours=20):
            hours_ago = (datetime.now() - last_dt).seconds // 3600
            return True, f"checked {hours_ago}h ago (min gap: 20h)"

    # Check if consistently irrelevant (need at least 5 runs of data)
    runs = person.get("total_runs", 0)
    avg = person.get("avg_relevance_score", 0.0)
    if runs >= 5 and avg < min_avg_score:
        return True, f"avg score {avg:.1f}/10 over {runs} runs (below threshold {min_avg_score})"

    return False, ""


# ── Posts API ─────────────────────────────────────────────────────────────────

def is_post_seen(post_id: str, post_url: str = "") -> bool:
    """
    Fast indexed lookup — have we seen this post before?
    Sub-millisecond at any scale.
    """
    init_db()
    with get_db() as conn:
        if post_id:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE post_id = ?", (post_id,)
            ).fetchone()
            if row:
                return True
        if post_url:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE post_url = ?", (post_url,)
            ).fetchone()
            if row:
                return True
    return False


def mark_post_seen(post_id: str, post_url: str = "", author_username: str = "",
                   author_name: str = "", post_text: str = "") -> None:
    """Insert a post record. Ignores if already exists."""
    init_db()
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO posts
                (post_id, post_url, author_username, author_name, post_text, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            post_id,
            post_url or "",
            author_username or "",
            author_name or "",
            (post_text or "")[:500],  # store preview only
            datetime.now().isoformat(),
        ))


def mark_post_analyzed(post_id: str, score: int, saved: bool) -> None:
    """Update post record after Gemini analysis."""
    init_db()
    with get_db() as conn:
        conn.execute("""
            UPDATE posts SET
                was_analyzed    = 1,
                was_saved       = ?,
                relevance_score = ?
            WHERE post_id = ?
        """, (1 if saved else 0, score, post_id))


def mark_post_linkedin_saved(post_id: str, post_url: str = "") -> None:
    """
    Mark a post as saved to LinkedIn Saved Posts.
    Called by post_saver.py after a successful Save click.
    Prevents re-saving the same post on the next run.
    """
    init_db()
    with get_db() as conn:
        if post_id:
            conn.execute(
                "UPDATE posts SET linkedin_saved = 1 WHERE post_id = ?",
                (post_id,)
            )
        elif post_url:
            conn.execute(
                "UPDATE posts SET linkedin_saved = 1 WHERE post_url = ?",
                (post_url,)
            )


def is_post_linkedin_saved(post_id: str, post_url: str = "") -> bool:
    """
    Check if a post was already saved to LinkedIn Saved Posts in a previous run.
    Prevents duplicate Save clicks across runs.
    """
    init_db()
    with get_db() as conn:
        if post_id:
            row = conn.execute(
                "SELECT linkedin_saved FROM posts WHERE post_id = ?", (post_id,)
            ).fetchone()
            if row and row[0]:
                return True
        if post_url:
            row = conn.execute(
                "SELECT linkedin_saved FROM posts WHERE post_url = ?", (post_url,)
            ).fetchone()
            if row and row[0]:
                return True
    return False


def save_analysis(post_id: str, analysis) -> None:
    """
    Store full Gemini analysis result.
    'analysis' is a PostAnalysis Pydantic object.
    Means we never call Gemini twice for the same post.
    """
    init_db()
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO analyses (
                post_id, analyzed_at, is_relevant, relevance_score,
                matched_topics, post_summary, key_insight, content_type,
                should_comment, comment_draft, should_save, content_angle, skip_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_id,
            datetime.now().isoformat(),
            1 if analysis.is_relevant else 0,
            analysis.relevance_score,
            json.dumps(analysis.matched_interests),
            analysis.post_summary,
            analysis.key_insight,
            analysis.content_type,
            1 if analysis.should_comment else 0,
            analysis.comment_draft,
            1 if analysis.should_save else 0,
            analysis.content_angle,
            analysis.skip_reason,
        ))


def get_cached_analysis(post_id: str) -> dict | None:
    """Return stored analysis if it exists — skip Gemini call entirely."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE post_id = ?", (post_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Filter helpers (drop-in for dedup_store) ─────────────────────────────────

def filter_new_posts(posts: list) -> tuple[list, int]:
    """
    Drop-in replacement for dedup_store.filter_new_posts().
    Returns (new_posts, skipped_count).
    """
    new = []
    skipped = 0
    for post in posts:
        if is_post_seen(post.post_id, post.post_url):
            skipped += 1
        else:
            new.append(post)

    if skipped:
        log.info(f"[memory] Dedup: {skipped} already seen, {len(new)} new")
    return new, skipped


def mark_posts_seen(posts: list) -> None:
    """Drop-in replacement for dedup_store.mark_posts_seen()."""
    for post in posts:
        mark_post_seen(
            post_id=post.post_id,
            post_url=post.post_url,
            author_username=name_to_username(post.author_name),
            author_name=post.author_name,
            post_text=post.post_text,
        )


def stats() -> dict:
    """Drop-in replacement for dedup_store.stats()."""
    init_db()
    with get_db() as conn:
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_saved = conn.execute("SELECT COUNT(*) FROM posts WHERE was_saved=1").fetchone()[0]
        total_persons = conn.execute("SELECT COUNT(*) FROM persons WHERE is_active=1").fetchone()[0]
        last_post = conn.execute(
            "SELECT extracted_at FROM posts ORDER BY extracted_at DESC LIMIT 1"
        ).fetchone()

    return {
        "total_seen": total_posts,
        "total_saved": total_saved,
        "total_persons": total_persons,
        "last_updated": last_post[0] if last_post else "never",
    }




# ── CLI ───────────────────────────────────────────────────────────────────────

def print_persons_table(persons: list[dict]) -> None:
    if not persons:
        print("\n  No profiles tracked yet.")
        print('  Add one: python memory.py persons add "Harrison Chase"')
        return

    print(f"\n  {'─'*62}")
    print(f"  Tracked Profiles ({len(persons)})")
    print(f"  {'─'*62}")

    for p in persons:
        last = (p.get("last_checked") or "never")[:10]
        avg = p.get("avg_relevance_score", 0)
        runs = p.get("total_runs", 0)
        saved = p.get("total_posts_saved", 0)
        topics = json.loads(p.get("top_topics") or "[]")
        score_bar = ("█" * int(avg) + "░" * (10 - int(avg))) if avg else "░"*10

        print(f"\n  {p['display_name']}")
        print(f"    Username:   {p['username']}")
        print(f"    URL:        {p['activity_url']}")
        if p.get("note"):
            print(f"    Note:       {p['note']}")
        print(f"    Runs:       {runs}  |  Posts saved: {saved}  |  Last: {last}")
        if runs > 0:
            print(f"    Avg score:  [{score_bar}] {avg:.1f}/10")
        if topics:
            print(f"    Top topics: {', '.join(topics[:5])}")

    print()


def _cli_add_person(args) -> None:
    """
    Smart add — handles URL, username slug, or name gracefully.
    Called by CLI only.
    """
    identifier = args.identifier.strip()
    name_label = getattr(args, "name", "").strip()
    username_override = getattr(args, "username", None)
    note = getattr(args, "note", "")

    # ── Case 1: Full LinkedIn URL ─────────────────────────────────────────────
    if "linkedin.com/in/" in identifier:
        username = username_from_url(identifier)
        if not username:
            print(f"[✗] Could not parse username from URL: {identifier}")
            print(f"    Expected format: https://www.linkedin.com/in/<username>/")
            return
        display_name = name_label or username  # use --name if given, else slug as label
        add_person(name=display_name, username=username, note=note)
        return

    # ── Case 2: Plain username slug (no spaces, no http) ─────────────────────
    if " " not in identifier and not identifier.startswith("http"):
        username = username_override or identifier
        display_name = name_label or identifier
        add_person(name=display_name, username=username, note=note)
        return

    # ── Case 3: Display name only (warn user) ─────────────────────────────────
    print(f"[!] Got a display name: '{identifier}'")
    print(f"    LinkedIn has many people with the same name.")
    print(f"    Please use the profile URL instead:")
    print(f"    1. Open LinkedIn, find the person")
    print(f"    2. Copy their profile URL from the browser")
    print(f"    3. Run:")
    print(f'       python profiles.py add "https://www.linkedin.com/in/<their-username>/"')
    print()
    print(f"    If you're sure about the username, use --username:")
    print(f'       python profiles.py add "{identifier}" --username <their-exact-username>')


def main():
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    init_db()

    parser = argparse.ArgumentParser(
        description="LinkedIn Agent Memory (SQLite)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python memory.py persons add "Harrison Chase"
  python memory.py persons add "Andrej Karpathy" --username karpathy --note "LLM researcher"
  python memory.py persons remove "Harrison Chase"
  python memory.py persons list
  python memory.py posts stats
  python memory.py posts clear-seen
        """
    )

    sub = parser.add_subparsers(dest="group")

    # ── persons ──────────────────────────────────────────────────────────────
    p_persons = sub.add_parser("persons", help="Manage tracked profiles")
    p_persons_sub = p_persons.add_subparsers(dest="action")

    p_add = p_persons_sub.add_parser(
        "add",
        help="Add a profile to track",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Add a LinkedIn profile to track.

BEST: paste their full LinkedIn URL (guarantees the right person)
  python memory.py persons add "https://www.linkedin.com/in/karpathy/"
  python memory.py persons add "https://www.linkedin.com/in/andrewyng/" --name "Andrew Ng"

ALSO WORKS: just their username slug (from the URL)
  python memory.py persons add "karpathy"
  python memory.py persons add "yann-lecun" --name "Yann LeCun"

RISKY (only for very unique names):
  python memory.py persons add --name "Someone Unique" --username their-slug
        """
    )
    p_add.add_argument(
        "identifier",
        help=(
            "LinkedIn profile URL (recommended) OR username slug. "
            "Examples: \"https://linkedin.com/in/karpathy/\" or \"karpathy\""
        )
    )
    p_add.add_argument("--name",     default="", help="Display name label (optional, cosmetic only)")
    p_add.add_argument("--username", help="Override username (rarely needed)")
    p_add.add_argument("--note",     default="", help="Why you are tracking this person")

    p_rm = p_persons_sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("name", help="Display name to remove")

    p_persons_sub.add_parser("list", help="List all tracked profiles with stats")

    p_rv = p_persons_sub.add_parser("reverify", help="Force re-verification on next run")
    p_rv.add_argument("name", help="Display name or username to re-verify")

    # ── posts ─────────────────────────────────────────────────────────────────
    p_posts = sub.add_parser("posts", help="Post dedup management")
    p_posts_sub = p_posts.add_subparsers(dest="action")
    p_posts_sub.add_parser("stats", help="Show post stats")
    p_posts_sub.add_parser("clear-seen", help="Clear all seen posts (re-analyze everything)")

    args = parser.parse_args()

    if args.group == "persons":
        if args.action == "add":
            _cli_add_person(args)
        elif args.action == "remove":
            remove_person(args.name)
        elif args.action == "list":
            print_persons_table(get_all_persons())
        elif args.action == "reverify":
            reset_verification(name_to_username(args.name))
            print(f"[✓] Will re-verify '{args.name}' on next run.")
        else:
            p_persons.print_help()

    elif args.group == "posts":
        if args.action == "stats":
            s = stats()
            print(f"\n  Posts seen:    {s['total_seen']}")
            print(f"  Posts saved:   {s['total_saved']}")
            print(f"  Persons:       {s['total_persons']}")
            print(f"  Last updated:  {s['last_updated'][:16]}")
            print()
        elif args.action == "clear-seen":
            with get_db() as conn:
                conn.execute("DELETE FROM posts")
                conn.execute("DELETE FROM analyses")
            print("[✓] Cleared all seen posts and analyses. Everything re-analyzed next run.")
        else:
            p_posts.print_help()

    else:
        parser.print_help()
        print("\n  Quick start:")
        print('  python memory.py persons add "Harrison Chase"')
        print('  python memory.py persons list')


if __name__ == "__main__":
    main()