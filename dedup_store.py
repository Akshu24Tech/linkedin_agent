"""
dedup_store.py
──────────────
Tracks which posts have already been processed across runs.
Prevents re-analyzing the same post and wasting Gemini API calls.

Storage: session/seen_posts.json (simple set of post IDs + URLs)
"""

import json
from pathlib import Path
from datetime import datetime
from logger import setup_logger

log = setup_logger(__name__)
STORE_FILE = Path("session/seen_posts.json")


def _load() -> dict:
    if not STORE_FILE.exists():
        return {"post_ids": [], "post_urls": [], "last_updated": ""}
    with open(STORE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(store: dict) -> None:
    Path("session").mkdir(exist_ok=True)
    store["last_updated"] = datetime.now().isoformat()
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def is_seen(post_id: str, post_url: str = "") -> bool:
    """Returns True if this post was already processed in a previous run."""
    store = _load()
    if post_id and post_id in store["post_ids"]:
        return True
    if post_url and post_url in store["post_urls"]:
        return True
    return False


def mark_seen(post_id: str, post_url: str = "") -> None:
    """Mark a post as processed so it's skipped next run."""
    store = _load()
    if post_id and post_id not in store["post_ids"]:
        store["post_ids"].append(post_id)
    if post_url and post_url not in store["post_urls"]:
        store["post_urls"].append(post_url)
    # Keep store size manageable — max 500 entries
    store["post_ids"] = store["post_ids"][-500:]
    store["post_urls"] = store["post_urls"][-500:]
    _save(store)


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
    store = _load()
    return {
        "total_seen": len(store["post_ids"]),
        "last_updated": store.get("last_updated", "never"),
    }
