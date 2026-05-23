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
  python memory.py persons add "Harrison Chase"
  python memory.py persons add "Andrej Karpathy" --username karpathy
  python memory.py persons remove "Harrison Chase"
  python memory.py posts stats
  python memory.py posts clear-seen
  python memory.py migrate          ← import existing profiles.json + seen_posts.json
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
    """
    Create tables if they don't exist. Safe to call on every startup.
    Also handles old DB schemas by adding missing columns via ALTER TABLE.
    """
    with get_db() as conn:
        # ── Create tables (never fails if they already exist) ─────────────────
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS persons (
                username            TEXT PRIMARY KEY,
                display_name        TEXT NOT NULL,
                activity_url        TEXT NOT NULL,
                profile_url         TEXT NOT NULL,
                note                TEXT DEFAULT '',
                added_at            TEXT NOT NULL,
                last_checked        TEXT,
                total_runs          INTEGER DEFAULT 0,
                total_posts_seen    INTEGER DEFAULT 0,
                total_posts_saved   INTEGER DEFAULT 0,
                avg_relevance_score REAL DEFAULT 0.0,
                score_history       TEXT DEFAULT '[]',
                top_topics          TEXT DEFAULT '[]',
                is_active           INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS posts (
                post_id         TEXT PRIMARY KEY,
                post_url        TEXT DEFAULT '',
                author_username TEXT DEFAULT '',
                author_name     TEXT DEFAULT '',
                post_text       TEXT DEFAULT '',
                extracted_at    TEXT NOT NULL,
                was_analyzed    INTEGER DEFAULT 0,
                was_saved       INTEGER DEFAULT 0,
                relevance_score INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS analyses (
                post_id         TEXT PRIMARY KEY,
                analyzed_at     TEXT NOT NULL,
                is_relevant     INTEGER,
                relevance_score INTEGER,
                matched_topics  TEXT,
                post_summary    TEXT,
                key_insight     TEXT,
                content_type    TEXT,
                should_comment  INTEGER,
                comment_draft   TEXT,
                should_save     INTEGER,
                content_angle   TEXT,
                skip_reason     TEXT
            );
        """)

        # ── Rename old columns to new names if they exist ─────────────────────
        _rename_column_if_exists(conn, "persons", "name", "display_name")
        _rename_column_if_exists(conn, "persons", "posts_collected", "total_posts_seen")
        _rename_column_if_exists(conn, "persons", "avg_score", "avg_relevance_score")
        _rename_column_if_exists(conn, "persons", "topics", "top_topics")
        _rename_column_if_exists(conn, "analyses", "matched_interests", "matched_topics")

        # ── Add missing columns to existing tables (schema migration) ─────────
        # This handles old DBs that were created before a column was added.
        _add_column_if_missing(conn, "persons", "total_runs", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "persons", "total_posts_seen", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "persons", "total_posts_saved", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "persons", "avg_relevance_score", "REAL DEFAULT 0.0")
        _add_column_if_missing(conn, "persons", "score_history", "TEXT DEFAULT '[]'")
        _add_column_if_missing(conn, "persons", "top_topics", "TEXT DEFAULT '[]'")
        _add_column_if_missing(conn, "persons", "is_active", "INTEGER DEFAULT 1")

        _add_column_if_missing(conn, "posts", "author_username", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "posts", "author_name",     "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "posts", "post_text",       "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "posts", "was_analyzed",    "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "posts", "was_saved",       "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "posts", "relevance_score", "INTEGER DEFAULT 0")

        # ── Create indexes (each in own try/except — won't fail on old schema) ─
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_posts_url    ON posts(post_url)",
            "CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_username)",
            "CREATE INDEX IF NOT EXISTS idx_posts_saved  ON posts(was_saved)",
            "CREATE INDEX IF NOT EXISTS idx_persons_active ON persons(is_active)",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column may not exist in very old DBs — skip gracefully

    log.info(f"[memory] DB ready at {DB_PATH}")


def _add_column_if_missing(conn, table: str, column: str, col_def: str) -> None:
    """Add a column to a table if it doesn't already exist (safe ALTER TABLE)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        log.info(f"[memory] Schema migration: added {table}.{column}")


def _rename_column_if_exists(conn, table: str, old_column: str, new_column: str) -> None:
    """Rename a column in a table if the old column exists and the new one doesn't."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if old_column in existing and new_column not in existing:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old_column} TO {new_column}")
        log.info(f"[memory] Schema migration: renamed {table}.{old_column} to {new_column}")




# ── URL Helpers ───────────────────────────────────────────────────────────────

def name_to_username(name: str) -> str:
    """'Andrew Ng' → 'andrew-ng'"""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def build_activity_url(username: str) -> str:
    return f"https://www.linkedin.com/in/{username}/recent-activity/all/"


def build_profile_url(username: str) -> str:
    return f"https://www.linkedin.com/in/{username}/"


# ── Persons API ───────────────────────────────────────────────────────────────

def add_person(name: str, username: str = None, note: str = "") -> dict:
    """
    Add a person to track. Username auto-derived from name if not given.
    Returns the person dict.
    """
    init_db()
    if not username:
        username = name_to_username(name)
        log.info(f"[memory] Auto-derived username: '{username}'")
        log.info(f"         If wrong: python memory.py persons add \"{name}\" --username <correct>")

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

    log.info(f"[memory] Added: {name} -> {build_activity_url(username)}")
    return get_person(username)


def remove_person(name: str) -> bool:
    """Soft delete — sets is_active=0 so history is preserved."""
    init_db()
    with get_db() as conn:
        # Try by display name first, then username
        result = conn.execute(
            "UPDATE persons SET is_active = 0 WHERE display_name = ? OR username = ?",
            (name, name_to_username(name))
        )
        if result.rowcount == 0:
            log.warning(f"[memory] '{name}' not found")
            return False

    log.info(f"[memory] Removed: {name} (history preserved)")
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


# ── Migration from old JSON files ─────────────────────────────────────────────

def migrate_from_json() -> None:
    """
    One-time migration from old profiles.json + seen_posts.json → memory.db
    Safe to run multiple times (uses INSERT OR IGNORE).
    """
    init_db()
    migrated_persons = 0
    migrated_posts = 0

    # Migrate profiles.json
    profiles_file = Path("session/profiles.json")
    if profiles_file.exists():
        with open(profiles_file) as f:
            profiles = json.load(f)
        for p in profiles:
            try:
                add_person(
                    name=p["name"],
                    username=p["username"],
                    note=p.get("note", ""),
                )
                migrated_persons += 1
            except Exception as e:
                log.warning(f"[migrate] Skipped person {p.get('name')}: {e}")
        log.info(f"[migrate] Imported {migrated_persons} persons from profiles.json")

    # Migrate seen_posts.json
    seen_file = Path("session/seen_posts.json")
    if seen_file.exists():
        with open(seen_file) as f:
            seen = json.load(f)
        with get_db() as conn:
            for post_id in seen.get("post_ids", []):
                conn.execute(
                    "INSERT OR IGNORE INTO posts (post_id, post_url, extracted_at) VALUES (?,?,?)",
                    (post_id, "", datetime.now().isoformat())
                )
                migrated_posts += 1
            for url in seen.get("post_urls", []):
                conn.execute(
                    "INSERT OR IGNORE INTO posts (post_id, post_url, extracted_at) VALUES (?,?,?)",
                    (f"url_{hash(url)}", url, datetime.now().isoformat())
                )
                migrated_posts += 1
        log.info(f"[migrate] Imported {migrated_posts} seen posts from seen_posts.json")

    print(f"\n[OK] Migration complete:")
    print(f"    Persons imported: {migrated_persons}")
    print(f"    Posts imported:   {migrated_posts}")
    print(f"    Database:         {DB_PATH}")
    print(f"\n    Old files kept (you can delete them manually):")
    print(f"    session/profiles.json")
    print(f"    session/seen_posts.json")


# ── CLI ───────────────────────────────────────────────────────────────────────

def print_persons_table(persons: list[dict]) -> None:
    if not persons:
        print("\n  No profiles tracked yet.")
        print('  Add one: python memory.py persons add "Harrison Chase"')
        return

    print(f"\n  {'-'*62}")
    print(f"  Tracked Profiles ({len(persons)})")
    print(f"  {'-'*62}")

    for p in persons:
        last = (p.get("last_checked") or "never")[:10]
        avg = p.get("avg_relevance_score", 0)
        runs = p.get("total_runs", 0)
        saved = p.get("total_posts_saved", 0)
        topics = json.loads(p.get("top_topics") or "[]")
        score_bar = ("#" * int(avg) + "." * (10 - int(avg))) if avg else "."*10

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


def main():
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
  python memory.py migrate
        """
    )

    sub = parser.add_subparsers(dest="group")

    # ── persons ──────────────────────────────────────────────────────────────
    p_persons = sub.add_parser("persons", help="Manage tracked profiles")
    p_persons_sub = p_persons.add_subparsers(dest="action")

    p_add = p_persons_sub.add_parser("add", help="Add a profile")
    p_add.add_argument("name", help='Display name e.g. "Harrison Chase"')
    p_add.add_argument("--username", help="LinkedIn username if auto-derive is wrong")
    p_add.add_argument("--note", default="", help="Why you're tracking this person")

    p_rm = p_persons_sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("name", help="Display name to remove")

    p_persons_sub.add_parser("list", help="List all tracked profiles")

    # ── posts ─────────────────────────────────────────────────────────────────
    p_posts = sub.add_parser("posts", help="Post dedup management")
    p_posts_sub = p_posts.add_subparsers(dest="action")
    p_posts_sub.add_parser("stats", help="Show post stats")
    p_posts_sub.add_parser("clear-seen", help="Clear all seen posts (re-analyze everything)")

    # ── migrate ───────────────────────────────────────────────────────────────
    sub.add_parser("migrate", help="Import profiles.json + seen_posts.json into memory.db")

    args = parser.parse_args()

    if args.group == "persons":
        if args.action == "add":
            add_person(args.name, username=args.username, note=args.note)
        elif args.action == "remove":
            remove_person(args.name)
        elif args.action == "list":
            print_persons_table(get_all_persons())
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
            print("[OK] Cleared all seen posts and analyses. Everything re-analyzed next run.")
        else:
            p_posts.print_help()

    elif args.group == "migrate":
        migrate_from_json()

    else:
        parser.print_help()
        print("\n  Quick start:")
        print('  python memory.py migrate                          <- import existing data first')
        print('  python memory.py persons add "Harrison Chase"')
        print('  python memory.py persons list')


if __name__ == "__main__":
    main()
