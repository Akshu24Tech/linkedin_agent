"""
vision_fallback.py
──────────────────
When LinkedIn's DOM changes and text extraction fails,
this module uses Gemini's vision to read the feed screenshot
and extract posts directly from the image.

Flow:
  feed_extractor.py → DOM fails → screenshot saved
  → vision_fallback.py reads screenshot → Gemini extracts posts
  → returns same RawPost list format as normal extraction
"""

import os
import base64
import json
from pathlib import Path
from datetime import datetime

from logger import setup_logger
from retry import with_retry

log = setup_logger(__name__)

VISION_PROMPT = """
You are analyzing a screenshot of a LinkedIn feed page.

Extract ALL visible content from people in this screenshot - including:
- Regular feed posts
- Posts in the "Recommended for you" section
- Any other posts showing a person's name, headline, and text content

For each person/post you find, extract:
1. author_name: The person's full name
2. author_headline: Their job title/company shown below their name
3. post_text: Any post text visible, OR if it's a "follow" suggestion, use their headline as the text
4. likes_approx: Number of likes/reactions shown (or "" if none)
5. comments_approx: Number of comments shown (or "" if none)

Return ONLY a valid JSON array like this:
[
  {
    "author_name": "John Smith",
    "author_headline": "AI Engineer at Google",
    "post_text": "The full post text here...",
    "likes_approx": "1,234",
    "comments_approx": "56"
  }
]

Rules:
- Extract ALL visible people, even from "Recommended for you" or suggestion sections
- If a field isn't visible, use empty string ""
- Return ONLY the JSON array, no other text
- Skip obvious banner ads with no person name
- If there are zero posts visible, return an empty array []
"""


def encode_image(image_path: str) -> str:
    """Encode image to base64 for Gemini vision."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_posts_from_screenshot(screenshot_path: str) -> list[dict]:
    """
    Use Gemini vision to extract posts from a LinkedIn feed screenshot.
    Returns list of dicts with post data.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        log.error("[vision] GEMINI_API_KEY not set. Cannot use vision fallback.")
        return []

    if not Path(screenshot_path).exists():
        log.error(f"[vision] Screenshot not found: {screenshot_path}")
        return []

    log.info(f"[vision] Running Gemini vision on {screenshot_path}...")

    import google.generativeai as genai
    genai.configure(api_key=gemini_key)

    model = genai.GenerativeModel("gemini-2.5-flash")

    image_data = encode_image(screenshot_path)

    def call_vision():
        response = model.generate_content([
            {
                "parts": [
                    {"text": VISION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_data
                        }
                    }
                ]
            }
        ])
        return response.text

    raw = with_retry(call_vision, retries=2, label="Gemini vision")

    # Parse JSON response
    try:
        # Strip any markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        posts_data = json.loads(cleaned)
        log.info(f"[vision] Extracted {len(posts_data)} posts from screenshot")
        return posts_data

    except json.JSONDecodeError as e:
        log.error(f"[vision] Failed to parse Gemini vision response: {e}")
        log.error(f"[vision] Raw response: {raw[:300]}")
        return []


def screenshot_posts_to_raw(posts_data: list[dict], screenshot_path: str) -> list:
    """
    Convert vision-extracted post dicts to RawPost objects.
    """
    from feed_extractor import RawPost

    raw_posts = []
    for i, p in enumerate(posts_data):
        post = RawPost(
            post_id=f"vision_{i}_{hash(p.get('post_text', '')[:30])}",
            author_name=p.get("author_name", "Unknown"),
            author_headline=p.get("author_headline", ""),
            post_text=p.get("post_text", ""),
            post_url="",  # Vision can't reliably extract URLs
            has_image=False,
            has_video=False,
            likes_approx=p.get("likes_approx", ""),
            comments_approx=p.get("comments_approx", ""),
            extracted_at=datetime.now().isoformat(),
            screenshot_path=screenshot_path,
        )
        raw_posts.append(post)

    return raw_posts


def run_vision_fallback(screenshot_path: str = "session/feed_screenshot.png") -> list:
    """
    Full vision fallback pipeline.
    Returns list of RawPost objects extracted via Gemini vision.
    """
    posts_data = extract_posts_from_screenshot(screenshot_path)
    if not posts_data:
        return []
    return screenshot_posts_to_raw(posts_data, screenshot_path)
