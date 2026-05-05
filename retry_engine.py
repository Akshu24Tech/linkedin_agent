"""
retry_engine.py - Day 4
Wraps all fragile operations (DOM scraping, API calls, Notion saves) with:
- Exponential backoff retry
- Screenshot fallback when DOM fails
- Structured error logging
- Per-run stats tracking
"""

import asyncio
import base64
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from playwright.async_api import Page


# ── Retry decorator ──────────────────────────────────────────────────────────

async def with_retry(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    label: str = "operation",
    **kwargs,
) -> Any:
    """
    Retry async function with exponential backoff.
    
    Usage:
        result = await with_retry(extract_posts, page, max_attempts=3, label="feed_extract")
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))  # 2s, 4s, 8s
            print(f"  [retry] {label} failed (attempt {attempt}/{max_attempts}): {type(e).__name__}")
            print(f"  [retry] Retrying in {delay:.0f}s...")
            await asyncio.sleep(delay)

    raise RuntimeError(f"{label} failed after {max_attempts} attempts. Last error: {last_error}")


# ── Screenshot fallback extractor ────────────────────────────────────────────

async def screenshot_extract_posts(page: Page, gemini_model) -> list[dict]:
    """
    Fallback: take screenshot of feed, send to Gemini Vision,
    extract post data from the image when DOM scraping fails.
    """
    print("  [fallback] DOM scraping failed -> switching to screenshot extraction")

    # Capture current viewport
    screenshot_bytes = await page.screenshot(full_page=False)
    img_b64 = base64.b64encode(screenshot_bytes).decode()

    # Save screenshot for debugging
    Path("session/fallback_screenshots").mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    Path(f"session/fallback_screenshots/{ts}.png").write_bytes(screenshot_bytes)

    # Ask Gemini to extract posts from the image
    import google.generativeai as genai
    import os

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = """
    This is a screenshot of a LinkedIn feed.
    Extract every visible post and return as JSON array.
    Each post object: { "author": "name", "role": "job title", "text": "full post content", "source": "screenshot" }
    Return ONLY the JSON array, nothing else.
    If no posts visible, return [].
    """

    import google.generativeai as genai_img
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(screenshot_bytes))
    response = model.generate_content([prompt, img])

    raw = response.text.strip()
    # Strip markdown code blocks if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    posts = json.loads(raw.strip())
    print(f"  [fallback] Screenshot extracted {len(posts)} posts")
    return posts


# ── Run statistics tracker ────────────────────────────────────────────────────

@dataclass
class RunStats:
    """Tracks stats for a single agent run."""
    run_id: str = field(default_factory=lambda: str(int(time.time())))
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    # Extraction
    posts_found: int = 0
    posts_dom_scraped: int = 0
    posts_screenshot_fallback: int = 0
    dom_failures: list[str] = field(default_factory=list)

    # Analysis
    posts_analyzed: int = 0
    posts_relevant: int = 0  # score >= 7
    posts_skipped: int = 0
    analysis_errors: list[str] = field(default_factory=list)
    avg_relevance_score: float = 0.0

    # Notion
    posts_saved_notion: int = 0
    posts_already_existed: int = 0
    notion_errors: list[str] = field(default_factory=list)

    # Retries
    total_retries: int = 0

    def finish(self):
        self.finished_at = time.time()

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def log_error(self, category: str, msg: str):
        """Log an error to the appropriate list."""
        entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
        if category == "dom":
            self.dom_failures.append(entry)
        elif category == "analysis":
            self.analysis_errors.append(entry)
        elif category == "notion":
            self.notion_errors.append(entry)
        print(f"  [error/{category}] {msg}")

    def save(self, path: str = "session/run_log.json"):
        """Append this run's stats to the run log file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        existing = []
        if Path(path).exists():
            try:
                existing = json.loads(Path(path).read_text())
            except Exception:
                existing = []

        existing.append(self.to_dict())
        # Keep last 30 runs only
        existing = existing[-30:]
        Path(path).write_text(json.dumps(existing, indent=2))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)),
            "duration_seconds": self.duration_seconds,
            "posts_found": self.posts_found,
            "posts_dom_scraped": self.posts_dom_scraped,
            "posts_screenshot_fallback": self.posts_screenshot_fallback,
            "posts_analyzed": self.posts_analyzed,
            "posts_relevant": self.posts_relevant,
            "posts_skipped": self.posts_skipped,
            "avg_relevance_score": self.avg_relevance_score,
            "posts_saved_notion": self.posts_saved_notion,
            "posts_already_existed": self.posts_already_existed,
            "total_retries": self.total_retries,
            "errors": {
                "dom": self.dom_failures,
                "analysis": self.analysis_errors,
                "notion": self.notion_errors,
            },
        }

    def print_summary(self):
        """Print a clean terminal summary at end of run."""
        print("\n" + "=" * 55)
        print(f"  RUN COMPLETE  |  {self.duration_seconds}s")
        print("=" * 55)
        print(f"  [+] Posts found       : {self.posts_found}")
        print(f"  [+] DOM scraped       : {self.posts_dom_scraped}")
        print(f"  [+] Screenshot FB     : {self.posts_screenshot_fallback}")
        print(f"  [+] Analyzed          : {self.posts_analyzed}")
        print(f"  [+] Relevant (≥7)     : {self.posts_relevant}")
        print(f"  [-] Skipped           : {self.posts_skipped}")
        if self.posts_analyzed:
            print(f"  [+] Avg score         : {self.avg_relevance_score:.1f}/10")
        print(f"  [+] Saved to Notion   : {self.posts_saved_notion}")
        print(f"  [!] Already existed   : {self.posts_already_existed}")
        if self.total_retries:
            print(f"  [*] Retries used      : {self.total_retries}")
        total_errors = len(self.dom_failures) + len(self.analysis_errors) + len(self.notion_errors)
        if total_errors:
            print(f"  [!] Errors logged     : {total_errors} -> session/run_log.json")
        print("=" * 55 + "\n")
