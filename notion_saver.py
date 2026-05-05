"""
notion_saver.py
───────────────
Saves analyzed LinkedIn posts to a Notion database.

What it does:
  1. Creates the database automatically on first run (inside a page you specify)
  2. Saves each relevant post as a full Notion page with all fields
  3. Deduplicates - won't save the same post twice (checks post_url)
  4. Rich page body: summary, insight, comment draft, content angle

Setup:
  - Get Notion integration token: https://www.notion.so/my-integrations
  - Create an empty Notion page -> copy its ID from the URL
  - Add NOTION_TOKEN and NOTION_PARENT_PAGE_ID to your .env

Run standalone to test:
  python notion_saver.py
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

from schemas import PostAnalysis
from feed_extractor import RawPost

load_dotenv()

NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

# Database name in Notion
DB_TITLE = "LinkedIn Feed Intelligence"
DB_ID_CACHE_FILE = "session/notion_db_id.txt"


# ── HTTP Client ───────────────────────────────────────────────────────────────

def get_headers() -> dict:
    token = os.getenv("NOTION_TOKEN")
    if not token or token == "your_notion_token_here":
        raise ValueError(
            "NOTION_TOKEN not set in .env\n"
            "Get it at: https://www.notion.so/my-integrations\n"
            "Create integration -> copy 'Internal Integration Secret'"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_API_VERSION,
    }


def notion_post(endpoint: str, payload: dict) -> dict:
    """POST to Notion API with error handling."""
    res = requests.post(
        f"{NOTION_API_BASE}{endpoint}",
        headers=get_headers(),
        json=payload,
        timeout=15,
    )
    if res.status_code not in (200, 201):
        raise RuntimeError(
            f"Notion API error {res.status_code}: {res.text[:300]}"
        )
    return res.json()


def notion_get(endpoint: str) -> dict:
    res = requests.get(
        f"{NOTION_API_BASE}{endpoint}",
        headers=get_headers(),
        timeout=15,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Notion API error {res.status_code}: {res.text[:300]}")
    return res.json()


# ── Database Setup ────────────────────────────────────────────────────────────

def create_database(parent_page_id: str) -> str:
    """
    Create the LinkedIn Feed Intelligence database in Notion.
    Returns the new database ID.
    """
    print(f"[->] Creating Notion database '{DB_TITLE}'...")

    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": "🧠"},
        "title": [{"type": "text", "text": {"content": DB_TITLE}}],
        "properties": {
            # Title (required by Notion)
            "Post Title": {"title": {}},

            # Author info
            "Author": {"rich_text": {}},
            "Author Headline": {"rich_text": {}},

            # Relevance
            "Score": {"number": {"format": "number"}},
            "Topics": {"multi_select": {}},
            "Content Type": {
                "select": {
                    "options": [
                        {"name": "tutorial", "color": "blue"},
                        {"name": "tool_announcement", "color": "green"},
                        {"name": "opinion_take", "color": "orange"},
                        {"name": "case_study", "color": "purple"},
                        {"name": "resource_list", "color": "yellow"},
                        {"name": "other", "color": "gray"},
                    ]
                }
            },

            # Actions
            "Commented": {"checkbox": {}},
            "Comment Drafted": {"checkbox": {}},
            "Has Content Angle": {"checkbox": {}},

            # Meta
            "Post URL": {"url": {}},
            "Saved At": {"date": {}},
            "Likes": {"rich_text": {}},
        },
    }

    data = notion_post("/databases", payload)
    db_id = data["id"]
    print(f"[✓] Database created! ID: {db_id}")

    # Cache it so we don't recreate on every run
    import pathlib
    pathlib.Path("session").mkdir(exist_ok=True)
    with open(DB_ID_CACHE_FILE, "w") as f:
        f.write(db_id)

    return db_id


def get_or_create_database() -> str:
    """
    Returns existing database ID if cached, otherwise creates a new one.
    """
    # Check cache first
    try:
        with open(DB_ID_CACHE_FILE) as f:
            db_id = f.read().strip()
        if db_id:
            # Verify it still exists
            try:
                notion_get(f"/databases/{db_id}")
                print(f"[✓] Using existing Notion database: {db_id[:8]}...")
                return db_id
            except Exception:
                print("[i] Cached database not found, creating new one...")
    except FileNotFoundError:
        pass

    # Need to create
    parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID")
    if not parent_page_id or parent_page_id == "your_notion_page_id_here":
        raise ValueError(
            "NOTION_PARENT_PAGE_ID not set in .env\n"
            "1. Create an empty page in Notion\n"
            "2. Open it in browser\n"
            "3. Copy the ID from URL: notion.so/Your-Page-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\n"
            "   The ID is the last 32 characters (with or without dashes)\n"
            "4. Also share the page with your integration:\n"
            "   Page -> ··· menu -> Connections -> Add your integration"
        )

    return create_database(parent_page_id)


# ── Deduplication ─────────────────────────────────────────────────────────────

def post_already_saved(db_id: str, post_url: str) -> bool:
    """Check if a post URL already exists in the database."""
    if not post_url:
        return False

    payload = {
        "filter": {
            "property": "Post URL",
            "url": {"equals": post_url}
        }
    }

    try:
        data = notion_post(f"/databases/{db_id}/query", payload)
        return len(data.get("results", [])) > 0
    except Exception:
        return False  # If check fails, allow saving


# ── Page Creation ─────────────────────────────────────────────────────────────

def build_page_title(post: RawPost, analysis: PostAnalysis) -> str:
    """Generate a meaningful page title."""
    # Use first sentence of summary as title
    summary = analysis.post_summary
    first_sentence = summary.split(".")[0][:80]
    return f"{post.author_name}: {first_sentence}"


def build_page_properties(post: RawPost, analysis: PostAnalysis) -> dict:
    """Build Notion page properties from post + analysis data."""
    title = build_page_title(post, analysis)

    props = {
        "Post Title": {
            "title": [{"text": {"content": title[:200]}}]
        },
        "Author": {
            "rich_text": [{"text": {"content": post.author_name[:200]}}]
        },
        "Author Headline": {
            "rich_text": [{"text": {"content": (post.author_headline or "")[:200]}}]
        },
        "Score": {
            "number": analysis.relevance_score
        },
        "Topics": {
            "multi_select": [
                {"name": topic[:100]} for topic in analysis.matched_interests[:5]
            ]
        },
        "Content Type": {
            "select": {"name": analysis.content_type}
        },
        "Commented": {
            "checkbox": False  # Default: not commented yet
        },
        "Comment Drafted": {
            "checkbox": bool(analysis.comment_draft)
        },
        "Has Content Angle": {
            "checkbox": bool(analysis.content_angle)
        },
        "Saved At": {
            "date": {"start": datetime.now().isoformat()}
        },
        "Likes": {
            "rich_text": [{"text": {"content": post.likes_approx or "?"}}]
        },
    }

    # URL is optional (might be empty if extraction failed)
    if post.post_url:
        props["Post URL"] = {"url": post.post_url}

    return props


def build_page_body(post: RawPost, analysis: PostAnalysis) -> list:
    """
    Build rich page content (blocks) for the Notion page.
    This is the full detail view - everything useful in one place.
    """
    blocks = []

    def heading2(text: str) -> dict:
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            }
        }

    def paragraph(text: str, bold: bool = False) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": text[:2000]},
                    "annotations": {"bold": bold}
                }]
            }
        }

    def callout(text: str, emoji: str = "💡") -> dict:
        return {
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": emoji},
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }
        }

    def divider() -> dict:
        return {"object": "block", "type": "divider", "divider": {}}

    def bulleted(text: str) -> dict:
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }
        }

    def code_block(text: str) -> dict:
        return {
            "object": "block",
            "type": "code",
            "code": {
                "language": "plain text",
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }
        }

    # ── Summary section ───────────────────────────────────────────────────────
    blocks.append(heading2("Summary"))
    blocks.append(paragraph(analysis.post_summary))

    # ── Key Insight ───────────────────────────────────────────────────────────
    blocks.append(divider())
    blocks.append(callout(analysis.key_insight, emoji="💡"))

    # ── Original Post ─────────────────────────────────────────────────────────
    blocks.append(divider())
    blocks.append(heading2("Original Post"))
    blocks.append(paragraph(f"By: {post.author_name} - {post.author_headline or 'N/A'}", bold=True))
    if post.post_url:
        blocks.append(paragraph(f"{post.post_url}"))
    blocks.append(paragraph(post.post_text[:2000] if post.post_text else "(No text extracted)"))

    # ── Comment Draft ─────────────────────────────────────────────────────────
    if analysis.comment_draft:
        blocks.append(divider())
        blocks.append(heading2("Comment Draft"))
        blocks.append(callout(
            "Copy -> edit if needed -> post manually on LinkedIn",
            emoji="⚠️"
        ))
        blocks.append(code_block(analysis.comment_draft))

    # ── Content Angle ─────────────────────────────────────────────────────────
    if analysis.content_angle:
        blocks.append(divider())
        blocks.append(heading2("Content Angle for Your Posts"))
        blocks.append(callout(analysis.content_angle, emoji="🎯"))

    # ── Meta ──────────────────────────────────────────────────────────────────
    blocks.append(divider())
    blocks.append(heading2("Analysis Details"))
    blocks.append(bulleted(f"Relevance Score: {analysis.relevance_score}/10"))
    blocks.append(bulleted(f"Content Type: {analysis.content_type}"))
    blocks.append(bulleted(f"Topics: {', '.join(analysis.matched_interests)}"))
    blocks.append(bulleted(f"Likes: {post.likes_approx or '?'} | Comments: {post.comments_approx or '?'}"))
    blocks.append(bulleted(f"Extracted: {post.extracted_at}"))

    return blocks


def save_post_to_notion(
    db_id: str,
    post: RawPost,
    analysis: PostAnalysis,
) -> dict | None:
    """
    Save a single post to Notion.
    Returns the created page data, or None if skipped (duplicate).
    """
    # Deduplication check
    if post_already_saved(db_id, post.post_url):
        print(f"    [=] Already saved: {post.author_name[:30]}")
        return None

    properties = build_page_properties(post, analysis)
    body_blocks = build_page_body(post, analysis)

    payload = {
        "parent": {"database_id": db_id},
        "icon": {"type": "emoji", "emoji": "📌"},
        "properties": properties,
        "children": body_blocks,
    }

    data = notion_post("/pages", payload)
    return data


# ── Main Save Pipeline ────────────────────────────────────────────────────────

def save_posts_to_notion(
    results: list[tuple[RawPost, PostAnalysis]],
) -> int:
    """
    Save all relevant posts (score >= 7) to Notion.
    Returns count of newly saved posts.
    """
    # Filter to only save-worthy posts
    to_save = [
        (post, analysis) for post, analysis in results
        if analysis.is_relevant and analysis.relevance_score >= 7 and analysis.should_save
    ]

    if not to_save:
        print("[i] No posts meet save threshold - nothing to send to Notion.")
        return 0

    print(f"\n[->] Saving {len(to_save)} posts to Notion...")

    db_id = get_or_create_database()
    saved_count = 0

    for post, analysis in to_save:
        try:
            result = save_post_to_notion(db_id, post, analysis)
            if result:
                print(f"    [✓] Saved: {post.author_name[:30]} ({analysis.relevance_score}/10)")
                saved_count += 1
        except Exception as e:
            print(f"    [✗] Failed to save {post.author_name[:30]}: {e}")

    print(f"\n[✓] Notion save complete: {saved_count}/{len(to_save)} posts saved.")
    return saved_count


# ── Standalone test ───────────────────────────────────────────────────────────
def main():
    """
    Test Notion saving with mock data - no LinkedIn or Gemini needed.
    Make sure NOTION_TOKEN and NOTION_PARENT_PAGE_ID are set in .env
    """
    from feed_extractor import RawPost
    from schemas import PostAnalysis

    print("=" * 55)
    print("  Notion Save Test")
    print("=" * 55)

    mock_post = RawPost(
        post_id="test_notion_1",
        author_name="Shreya Shankar",
        author_headline="PhD student @ UC Berkeley | AI Data Quality",
        post_text=(
            "Been running evals on RAG pipelines for 6 months. "
            "Hybrid search (BM25 + dense) beats pure semantic by ~12% on recall. "
            "Metadata filtering before vector search = 3x throughput. "
            "Most teams skip this and then complain RAG is slow."
        ),
        post_url="https://www.linkedin.com/posts/test-notion-1",
        has_image=False,
        has_video=False,
        likes_approx="891",
        comments_approx="64",
        extracted_at=datetime.now().isoformat(),
        screenshot_path="",
    )

    mock_analysis = PostAnalysis(
        is_relevant=True,
        relevance_score=9,
        matched_interests=["RAG pipelines", "AI engineering"],
        post_summary=(
            "Benchmark results comparing RAG retrieval strategies over 6 months. "
            "Hybrid search outperforms pure semantic by 12% recall. "
            "Metadata pre-filtering dramatically improves throughput."
        ),
        key_insight=(
            "Metadata filtering before vector search gives 3x throughput with negligible "
            "quality loss - most teams skip this and blame the LLM."
        ),
        content_type="case_study",
        should_comment=True,
        comment_draft=(
            "The metadata filtering point is underrated - I've seen the same pattern "
            "in production RAG systems. Most teams over-index on embedding models and "
            "ignore pre-retrieval optimizations. The 3x throughput gain is real."
        ),
        should_save=True,
        content_angle=(
            "Write about production RAG optimizations you've found: "
            "metadata filtering, hybrid search, and why the retrieval stack "
            "is usually the actual bottleneck."
        ),
        skip_reason="Saved.",
    )

    results = [(mock_post, mock_analysis)]
    count = save_posts_to_notion(results)
    print(f"\n[✓] Test complete. Saved {count} post to Notion.")
    print("    Check your Notion page for the new database!")


if __name__ == "__main__":
    main()
