"""
dedup_store.py
──────────────
Tracks which posts have already been processed across runs.
Prevents re-analyzing the same post and wasting Gemini API calls.
Fully delegated to the new SQLite database (memory.py) as a wrapper.
"""

from logger import setup_logger
import memory

log = setup_logger(__name__)

def is_seen(post_id: str, post_url: str = "") -> bool:
    """Returns True if this post was already processed in a previous run."""
    return memory.is_post_seen(post_id, post_url)

def mark_seen(post_id: str, post_url: str = "") -> None:
    """Mark a post as processed so it's skipped next run."""
    memory.mark_post_seen(post_id, post_url)

def filter_new_posts(posts) -> tuple[list, int]:
    """
    Filter out already-seen posts from a list of RawPost objects.
    Returns (new_posts, skipped_count).
    """
    new = []
    skipped = 0
    for post in posts:
        if is_seen(post.post_id, post.post_url):
            skipped += 1
        else:
            new.append(post)

    if skipped:
        log.info(f"[dedup] Skipped {skipped} already-seen posts, {len(new)} new to analyze")
    return new, skipped

def mark_posts_seen(posts) -> None:
    """Mark a list of RawPost objects as seen."""
    for post in posts:
        mark_seen(post.post_id, post.post_url)

def stats() -> dict:
    """Return seen posts stats from memory.py."""
    db_stats = memory.stats()
    return {
        "total_seen": db_stats.get("total_seen", 0),
        "last_updated": db_stats.get("last_updated", "never"),
    }
