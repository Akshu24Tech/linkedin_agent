"""
post_generator.py
─────────────────
Turns content angles saved in Notion/JSON into full LinkedIn post drafts.

This closes the loop from your notebook:
  "Save for Future Work" → actual posts you can publish

Reads content angles from session/analyzed_posts.json,
lets you pick one, then uses Gemini to draft a post in your voice.

Run:
  python post_generator.py                  # Interactive mode
  python post_generator.py --all            # Draft all angles from last run
  python post_generator.py --save-notion    # Save drafts back to Notion
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Aksh's LinkedIn voice — this is the critical prompt
AKSH_VOICE_PROMPT = """
You are writing a LinkedIn post for Akshu Grewal (Aksh), an AI engineer and final-year
B.Tech CSE (AI) student who builds production-grade agentic systems.

His LinkedIn voice:
- PRACTITIONER tone — writes from actual hands-on building experience, not theory
- DIRECT and specific — no vague "AI is amazing" statements
- Casual but technical — uses "bro" energy but backs claims with real details
- SHORT hook — first line must stop the scroll, asks a question or makes a bold claim
- HONEST — admits when things don't work, shares real tradeoffs
- NO cringe phrases: "Game-changer", "Revolutionizing", "In today's fast-paced world",
  "I'm excited to share", "Thrilled to announce", "This blew my mind"
- NO excessive emojis — 2-3 max, only where they genuinely add clarity
- Structure: Hook → Real experience/insight → Concrete takeaway → CTA (question or call to action)
- Length: 150-250 words max. Short posts get more reach on LinkedIn.
- Hashtags: 3-4 max, specific and relevant (not #AI #Tech #Innovation)

His tech stack context (weave in naturally when relevant):
LangGraph, Google ADK, RAG pipelines, Gemini, multi-agent systems,
ChromaDB, Playwright, AI observability, agentic AI.

Write in first person. Make it sound like a builder sharing a real lesson, not a marketer.
"""


def load_content_angles() -> list[dict]:
    """Load posts with content angles from last analysis run."""
    path = Path("session/analyzed_posts.json")
    if not path.exists():
        print("[!] No analyzed_posts.json found. Run the agent first.")
        return []

    with open(path) as f:
        data = json.load(f)

    angles = []
    for item in data:
        a = item["analysis"]
        p = item["post"]
        if a.get("content_angle") and a.get("should_save"):
            angles.append({
                "author": p["author_name"],
                "original_insight": a["key_insight"],
                "content_angle": a["content_angle"],
                "topics": a["matched_interests"],
                "post_summary": a["post_summary"],
            })

    return angles


def generate_post_draft(angle: dict) -> str:
    """Use Gemini to generate a LinkedIn post from a content angle."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY not set in .env")

    import google.generativeai as genai
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""
{AKSH_VOICE_PROMPT}

─────────────────────────────────────────
CONTENT ANGLE TO WRITE ABOUT:
{angle['content_angle']}

CONTEXT (use this as background, don't copy it directly):
- Original insight that inspired this: {angle['original_insight']}
- Topics: {', '.join(angle['topics'])}
- Summary of what triggered this idea: {angle['post_summary']}
─────────────────────────────────────────

Write the LinkedIn post now. Return ONLY the post text, nothing else.
No title, no "Here's the post:", no extra commentary.
"""

    from retry import with_retry
    response = with_retry(
        lambda: model.generate_content(prompt),
        retries=3,
        label="Gemini post generation"
    )
    return response.text.strip()


def save_draft_to_file(drafts: list[dict], path: str = "session/post_drafts.json") -> None:
    """Save generated drafts to JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False)
    print(f"\n[✓] Drafts saved to {path}")


def save_draft_to_notion(draft: dict) -> None:
    """Save a post draft as a page in a Notion drafts database."""
    from notion_saver import get_headers, notion_post, get_or_create_database
    import requests

    # We'll save to same DB as a special "content draft" entry type
    # by creating a standalone page under the parent page
    parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID", "").replace("-", "")
    if not parent_page_id:
        print("[!] NOTION_PARENT_PAGE_ID not set — skipping Notion save")
        return

    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": "✍️"},
        "properties": {
            "title": {
                "title": [{"text": {"content": f"Draft: {draft['angle'][:80]}"}}]
            }
        },
        "children": [
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "💡"},
                    "rich_text": [{"type": "text", "text": {"content": f"Angle: {draft['angle']}"}}]
                }
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "LinkedIn Post Draft"}}]
                }
            },
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": "plain text",
                    "rich_text": [{"type": "text", "text": {"content": draft["draft"]}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {
                        "content": f"Word count: {len(draft['draft'].split())} words"
                    }}]
                }
            }
        ]
    }

    result = notion_post("/pages", payload)
    print(f"[✓] Draft saved to Notion")


def interactive_mode(angles: list[dict]) -> list[dict]:
    """Let user pick which angles to draft."""
    print(f"\n{'═'*58}")
    print(f"  Content Angles from Last Run ({len(angles)} available)")
    print(f"{'═'*58}")

    for i, a in enumerate(angles, 1):
        print(f"\n  [{i}] {a['content_angle'][:80]}...")
        print(f"       Topics: {', '.join(a['topics'][:3])}")

    print(f"\n  [A] Generate ALL")
    print(f"  [Q] Quit")

    drafts = []

    while True:
        choice = input("\n  Pick a number (or A/Q): ").strip().upper()

        if choice == "Q":
            break
        elif choice == "A":
            selected = angles
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(angles):
                    selected = [angles[idx]]
                else:
                    print("  Invalid number.")
                    continue
            except ValueError:
                print("  Enter a number, A, or Q.")
                continue

        for angle in selected:
            print(f"\n  Generating draft for: {angle['content_angle'][:60]}...")
            print(f"  {'─'*50}")

            try:
                draft_text = generate_post_draft(angle)

                print(f"\n{'─'*58}")
                print(f"  DRAFT ({len(draft_text.split())} words)")
                print(f"{'─'*58}")
                print(f"\n{draft_text}\n")
                print(f"{'─'*58}")

                word_count = len(draft_text.split())
                if word_count > 250:
                    print(f"  ⚠️  {word_count} words — consider trimming (target: 150-250)")
                elif word_count < 80:
                    print(f"  ⚠️  {word_count} words — might be too short")
                else:
                    print(f"  ✓ {word_count} words — good length")

                drafts.append({
                    "angle": angle["content_angle"],
                    "topics": angle["topics"],
                    "draft": draft_text,
                    "word_count": word_count,
                })

                # Ask what to do with it
                action = input("\n  [C]opy to clipboard  [N]ext  [Q]uit: ").strip().upper()
                if action == "C":
                    try:
                        import subprocess
                        subprocess.run(
                            ["clip"] if os.name == "nt" else ["pbcopy"],
                            input=draft_text.encode(),
                            check=True
                        )
                        print("  ✓ Copied to clipboard!")
                    except Exception:
                        print("  [!] Clipboard copy failed — manually copy from above")
                elif action == "Q":
                    break

            except Exception as e:
                print(f"  [✗] Generation failed: {e}")

        if choice != "A":
            another = input("\n  Generate another? [Y/N]: ").strip().upper()
            if another != "Y":
                break

    return drafts


def batch_mode(angles: list[dict]) -> list[dict]:
    """Generate drafts for all angles without interaction."""
    drafts = []
    print(f"\n[→] Generating {len(angles)} post drafts...")

    for i, angle in enumerate(angles, 1):
        print(f"\n  [{i}/{len(angles)}] {angle['content_angle'][:60]}...")
        try:
            draft_text = generate_post_draft(angle)
            word_count = len(draft_text.split())
            drafts.append({
                "angle": angle["content_angle"],
                "topics": angle["topics"],
                "draft": draft_text,
                "word_count": word_count,
            })
            print(f"  ✓ {word_count} words")
        except Exception as e:
            print(f"  ✗ Failed: {e}")

    return drafts


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate LinkedIn posts from saved content angles")
    parser.add_argument("--all", action="store_true", help="Generate all angles non-interactively")
    parser.add_argument("--save-notion", action="store_true", help="Save drafts to Notion")
    args = parser.parse_args()

    print("\n" + "═"*58)
    print("  LinkedIn Post Draft Generator")
    print("═"*58)

    angles = load_content_angles()
    if not angles:
        print("\n[!] No content angles found in session/analyzed_posts.json")
        print("    Run the agent first: python agent.py")
        return

    if args.all:
        drafts = batch_mode(angles)
    else:
        drafts = interactive_mode(angles)

    if drafts:
        save_draft_to_file(drafts)

        if args.save_notion:
            print(f"\n[→] Saving {len(drafts)} drafts to Notion...")
            for draft in drafts:
                try:
                    save_draft_to_notion(draft)
                except Exception as e:
                    print(f"  [✗] Failed: {e}")

        print(f"\n[✓] Done. {len(drafts)} draft(s) ready.")
        print(f"    Full drafts: session/post_drafts.json")
        print(f"    Run with --view-saved to see comment drafts too")


if __name__ == "__main__":
    main()
