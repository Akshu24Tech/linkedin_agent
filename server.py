"""
server.py
─────────
Local FastAPI server that bridges the Chrome extension → existing Python pipeline.

The Chrome extension sends extracted LinkedIn posts to this server.
This server runs the existing Gemini analysis + Notion save pipeline.

Run with:
  python server.py
  # or with auto-reload during dev:
  uvicorn server:app --host 127.0.0.1 --port 8765 --reload

Endpoints:
  GET  /health   — check if server is running, return memory stats
  POST /analyze  — receive posts from extension, analyze + save
  GET  /results  — return last run's results (for debugging)
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import asdict

# ── Environment ───────────────────────────────────────────────────────────────

from dotenv import load_dotenv
load_dotenv()

# ── FastAPI ───────────────────────────────────────────────────────────────────

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("[!] FastAPI/uvicorn not installed. Run:")
    print("    pip install fastapi uvicorn")
    sys.exit(1)

# ── Project imports ───────────────────────────────────────────────────────────

from logger import setup_logger
log = setup_logger("server")

from schemas import RawPost
from memory import (
    init_db, filter_new_posts, mark_posts_seen,
    mark_post_analyzed, stats as mem_stats,
)
from analyzer import analyze_posts_batch
from notion_saver import save_posts_to_notion

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LinkedIn Feed Intelligence — Local Server",
    description="Bridges Chrome extension → Gemini analysis → Notion",
    version="1.0.0",
)

# Allow requests from Chrome extension (chrome-extension:// origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # extension origin varies; localhost is safe
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for last run results (for /results endpoint)
_last_results: list = []

# ── Request / Response models ─────────────────────────────────────────────────

class ExtractedPost(BaseModel):
    """Post data sent by the Chrome extension content_script.js"""
    post_id: str
    author_name: str
    author_headline: str = ""
    post_text: str
    post_url: str = ""
    has_image: bool = False
    has_video: bool = False
    likes_approx: str = ""
    comments_approx: str = ""
    extracted_at: str = ""
    screenshot_path: str = ""
    posted_at: str = ""
    post_age_days: float = -1
    source_page: str = ""


class AnalyzeRequest(BaseModel):
    posts: list[ExtractedPost]
    threshold: int = 7
    source_url: str = ""


class PostResult(BaseModel):
    post: dict
    analysis: Optional[dict] = None
    saved_to_notion: bool = False
    already_seen: bool = False
    skip_reason: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Extension calls this to check if server is alive."""
    init_db()
    s = mem_stats()
    return {
        "status": "ok",
        "posts_in_memory": s["total_seen"],
        "posts_saved": s["total_saved"],
        "persons_tracked": s["total_persons"],
        "last_updated": s["last_updated"],
        "server": "LinkedIn Feed Intelligence v1.0",
    }


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> list[PostResult]:
    """
    Main endpoint called by the extension after extracting posts.

    1. Convert ExtractedPost → RawPost
    2. Filter already-seen posts (dedup via memory.db)
    3. Run Gemini analysis on new posts
    4. Save qualifying posts to Notion
    5. Return results to extension
    """
    global _last_results

    if not req.posts:
        raise HTTPException(status_code=400, detail="No posts provided")

    log.info(f"[server] Received {len(req.posts)} posts from extension (source: {req.source_url})")

    # ── Convert to RawPost ────────────────────────────────────────────────────
    raw_posts: list[RawPost] = []
    now = datetime.now().isoformat()

    for ep in req.posts:
        raw_posts.append(RawPost(
            post_id=ep.post_id or f"ext_{hash(ep.post_text[:80])}",
            author_name=ep.author_name,
            author_headline=ep.author_headline,
            post_text=ep.post_text,
            post_url=ep.post_url,
            has_image=ep.has_image,
            has_video=ep.has_video,
            likes_approx=ep.likes_approx,
            comments_approx=ep.comments_approx,
            extracted_at=ep.extracted_at or now,
            screenshot_path="",
            posted_at=ep.posted_at,
            post_age_days=ep.post_age_days,
        ))

    # ── Dedup: filter already-seen ────────────────────────────────────────────
    new_posts, skipped_count = filter_new_posts(raw_posts)

    results: list[PostResult] = []

    # Mark already-seen posts in results
    seen_ids = {p.post_id for p in new_posts}
    for rp in raw_posts:
        if rp.post_id not in seen_ids:
            results.append(PostResult(
                post=asdict(rp),
                already_seen=True,
                skip_reason="Already in memory",
            ))

    if not new_posts:
        log.info("[server] All posts already seen — zero Gemini calls used.")
        _last_results = [r.model_dump() for r in results]
        return results

    log.info(f"[server] {len(new_posts)} new posts → sending to Gemini")

    # ── Analyze ───────────────────────────────────────────────────────────────
    all_analysis = analyze_posts_batch(new_posts, delay_between=1.5)
    mark_posts_seen(new_posts)

    # ── Save qualifying posts to Notion ───────────────────────────────────────
    qualifying = [
        (p, a) for p, a in all_analysis
        if a.is_relevant and a.relevance_score >= req.threshold and a.should_save
    ]

    notion_saved_ids: set[str] = set()
    if qualifying:
        log.info(f"[server] {len(qualifying)} posts qualify (score ≥ {req.threshold}) → saving to Notion")
        try:
            save_posts_to_notion(qualifying)
            notion_saved_ids = {p.post_id for p, _ in qualifying}
        except Exception as e:
            log.error(f"[server] Notion save failed: {e}")

    # ── Update memory + build result list ─────────────────────────────────────
    for post, analysis in all_analysis:
        is_saved = post.post_id in notion_saved_ids
        mark_post_analyzed(post.post_id, analysis.relevance_score, is_saved)

        skip = ""
        if not analysis.is_relevant:
            skip = analysis.skip_reason or "Not relevant to your interests"
        elif analysis.relevance_score < req.threshold:
            skip = analysis.skip_reason or f"Score {analysis.relevance_score} below threshold {req.threshold}"
        elif not analysis.should_save:
            skip = "Not flagged for saving (time-sensitive / low lasting value)"

        results.append(PostResult(
            post=asdict(post),
            analysis=analysis.model_dump(),
            saved_to_notion=is_saved,
            already_seen=False,
            skip_reason=skip,
        ))

    # ── Persist locally too ───────────────────────────────────────────────────
    Path("session").mkdir(exist_ok=True)
    output = [r.model_dump() for r in results]
    with open("session/extension_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    _last_results = output
    log.info(f"[server] Done. {len(notion_saved_ids)} saved to Notion.")
    return results


@app.get("/results")
async def get_results():
    """Return results from the last analyze call (for debugging)."""
    return _last_results


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    init_db()
    s = mem_stats()

    print()
    print("═" * 55)
    print("  LinkedIn Feed Intelligence — Local Server")
    print("═" * 55)
    print(f"  URL:            http://localhost:8765")
    print(f"  Posts in memory: {s['total_seen']}")
    print(f"  Posts saved:     {s['total_saved']}")
    print(f"  Persons tracked: {s['total_persons']}")
    print()
    print("  Install extension in Chrome:")
    print("  1. Go to chrome://extensions")
    print("  2. Enable Developer Mode (top right)")
    print("  3. Click 'Load unpacked'")
    print("  4. Select the 'extension/' folder")
    print()
    print("  Then navigate to any LinkedIn activity page")
    print("  and click 'Scan This Page' in the extension popup.")
    print()
    print("  Press Ctrl+C to stop.")
    print("═" * 55)
    print()

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8765,
        log_level="warning",   # suppress uvicorn noise, our logger handles it
        reload=False,
    )
