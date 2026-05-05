"""
analyzer.py
───────────
The AI brain of the agent.

Takes raw extracted posts -> runs Gemini Flash structured analysis ->
returns PostAnalysis objects with interest scores, insights, comment drafts.

Uses Gemini's native JSON schema mode for guaranteed structured output.
No regex parsing, no prompt hacks - pure Pydantic.

Supports Gemini (primary) and Groq (fallback).
"""

import os
import asyncio
import time
from typing import Optional
from dotenv import load_dotenv

from schemas import PostAnalysis
from feed_extractor import RawPost

load_dotenv()


# ── Interest Profile ──────────────────────────────────────────────────────────
# This is Akshu's interest fingerprint - what the agent filters for.
# Edit this to tune what gets saved vs skipped.

AKSHU_INTEREST_PROFILE = """
You are analyzing LinkedIn posts for Akshu Grewal (Akshu), a final-year B.Tech CSE (AI) 
student and AI engineer building production-grade agentic systems.

His CORE INTEREST AREAS (save posts about these):
- LangGraph: state machines,w r checkpointing, human-in-the-loop, multi-agent graphs
- LangChain: agents, tools, chains, neeleases/features
- Google ADK (Agent Development Kit): tutorials, patterns, production use
- Gemini API: new features, fine-tuning, multimodal use, deployment
- Agentic AI systems: orchestration, planning, memory, tool use, reflection
- Multi-agent architectures: coordination, communication, task delegation
- RAG pipelines: chunking strategies, retrieval optimization, hybrid search
- Vector databases: Pinecone, Weaviate, Chroma, pgvector, Qdrant
- AI observability & tracing: LangSmith, Arize, custom tracing, eval frameworks
- Browser agents / computer use: Playwright automation, vision-based agents
- LLM deployment: inference optimization, quantization, serving (vLLM, Ollama, etc.)
- AI engineering patterns: production systems, not just demos or research
- LinkedIn content strategy: how builders/engineers create authentic technical content

SKIP these (even if AI-adjacent):
- Generic "AI will change everything" hype with no substance
- Job postings or hiring announcements
- Motivational quotes dressed as AI content
- Pure research papers (unless about agentic systems specifically)
- Non-AI topics entirely
- Celebrity AI takes (Elon Musk tweets about AI, etc.)
- Marketing content for AI SaaS tools (no technical depth)
- "Top 10 AI tools" listicles with no real analysis

SAVE THRESHOLD: Only posts scoring 7+ out of 10 get saved.
A 7 means: clearly relevant topic + has at least one concrete insight or technique.
A 10 means: exactly what Aksh is building, with production-level depth.
"""

ANALYSIS_PROMPT_TEMPLATE = """
{interest_profile}

─────────────────────────────────────────
POST TO ANALYZE:
─────────────────────────────────────────
Author: {author_name}
Headline: {author_headline}

Post Content:
{post_text}

Engagement: {likes} likes, {comments} comments
─────────────────────────────────────────

Analyze this post and fill in the structured output schema.
Be strict on relevance - it's better to skip a borderline post than save noise.
For comment drafts, write like a practitioner who has actually built these systems,
not like someone trying to network. Avoid platitudes.
"""


def get_llm():
    """
    Initialize the LLM based on available API keys.
    Priority: Gemini Flash > Groq > error
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if gemini_key and gemini_key != "your_gemini_api_key_here":
        from langchain_google_genai import ChatGoogleGenerativeAI
        print("[i] Using Gemini 2.5 Flash for analysis")
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=gemini_key,
            temperature=0.1,  # Low temp = consistent, structured responses
        )

    elif groq_key and groq_key != "your_groq_api_key_here":
        from langchain_groq import ChatGroq
        print("[i] Using Groq (Llama 3.3 70B) for analysis")
        return ChatGroq(
            model="llama-3.3-70b-versatile",
            groq_api_key=groq_key,
            temperature=0.1,
        )

    else:
        raise ValueError(
            "No LLM API key found!\n"
            "Set GEMINI_API_KEY or GROQ_API_KEY in your .env file.\n"
            "Get Gemini free key: https://aistudio.google.com/apikey\n"
            "Get Groq free key: https://console.groq.com/keys"
        )


def build_structured_analyzer(llm):
    """
    Wrap the LLM with structured output using PostAnalysis schema.
    Uses json_schema mode - Gemini guarantees valid Pydantic output.
    """
    return llm.with_structured_output(
        schema=PostAnalysis,
        method="json_schema",
    )


def build_prompt(post: RawPost) -> str:
    """Build the analysis prompt for a single post."""
    return ANALYSIS_PROMPT_TEMPLATE.format(
        interest_profile=AKSHU_INTEREST_PROFILE,
        author_name=post.author_name,
        author_headline=post.author_headline or "N/A",
        post_text=post.post_text[:2000],  # Cap at 2000 chars to save tokens
        likes=post.likes_approx or "unknown",
        comments=post.comments_approx or "unknown",
    )


def analyze_post(analyzer, post: RawPost) -> Optional[PostAnalysis]:
    """
    Analyze a single post with Gemini.
    Returns PostAnalysis or None if analysis fails.
    """
    if not post.post_text or len(post.post_text) < 30:
        return None

    # Skip screenshot placeholder
    if post.post_id == "screenshot_mode":
        return None

    prompt = build_prompt(post)

    try:
        result = analyzer.invoke(prompt)
        return result
    except Exception as e:
        print(f"    [!] Analysis failed for post by {post.author_name}: {e}")
        return None


def analyze_posts_batch(
    posts: list[RawPost],
    delay_between: float = 1.0,  # seconds between API calls (rate limit safety)
) -> list[tuple[RawPost, PostAnalysis]]:
    """
    Analyze a batch of posts.
    Returns list of (post, analysis) tuples - only for analyzed posts.

    Args:
        posts: Raw extracted posts from feed_extractor
        delay_between: Seconds to wait between API calls (avoid rate limits)
    """
    llm = get_llm()
    analyzer = build_structured_analyzer(llm)

    results = []
    saved_count = 0
    skipped_count = 0

    print(f"\n[->] Analyzing {len(posts)} posts with AI...")
    print(f"    Threshold: score >= 7 gets saved\n")

    for i, post in enumerate(posts, 1):
        print(f"  [{i}/{len(posts)}] {post.author_name[:35]!r}")

        analysis = analyze_post(analyzer, post)

        if analysis is None:
            print(f"         Skipped (no content / extraction failed)")
            skipped_count += 1
            continue

        # Log the decision
        score_bar = "█" * analysis.relevance_score + "░" * (10 - analysis.relevance_score)
        status = "SAVE" if analysis.should_save else "skip"

        print(f"         Score: [{score_bar}] {analysis.relevance_score}/10  {status}")

        if analysis.is_relevant and analysis.relevance_score >= 7:
            print(f"         Topics: {', '.join(analysis.matched_interests)}")
            print(f"         Insight: {analysis.key_insight[:80]}...")
            if analysis.should_comment:
                print(f"         Comment drafted")
            saved_count += 1
        else:
            print(f"         Reason: {analysis.skip_reason}")
            skipped_count += 1

        results.append((post, analysis))

        # Rate limit safety - Gemini free tier = 15 req/min
        if i < len(posts):
            time.sleep(delay_between)

    print(f"\n Analysis complete: {saved_count} saved, {skipped_count} skipped out of {len(posts)} posts")
    return results


def filter_saved_posts(
    results: list[tuple[RawPost, PostAnalysis]]
) -> list[tuple[RawPost, PostAnalysis]]:
    """Filter to only posts that should be saved (score >= 7)."""
    return [
        (post, analysis)
        for post, analysis in results
        if analysis.is_relevant and analysis.relevance_score >= 7 and analysis.should_save
    ]


# ── Standalone test ───────────────────────────────────────────────────────────
def main():
    """Test analyzer with mock posts (no LinkedIn needed)."""
    import json

    print("=" * 55)
    print("  Analyzer Test - No LinkedIn Needed")
    print("=" * 55)

    # Mock posts for testing
    mock_posts = [
        RawPost(
            post_id="test_1",
            author_name="Harrison Chase",
            author_headline="Co-founder & CEO at LangChain",
            post_text="""
            Just shipped LangGraph 0.3 with major checkpointing improvements.
            
            Key changes:
            - Redis-backed checkpointer is now production-ready
            - Interrupt/resume now works across async boundaries
            - New SubgraphState for composable multi-agent systems
            
            The big one: you can now serialize arbitrary Python objects in state,
            not just JSON-serializable types. This unblocks a ton of production use cases.
            
            Migration guide in the thread 👇
            """,
            post_url="https://linkedin.com/posts/test1",
            has_image=False,
            has_video=False,
            likes_approx="2,341",
            comments_approx="187",
            extracted_at="2026-01-04T09:00:00",
            screenshot_path="",
        ),
        RawPost(
            post_id="test_2",
            author_name="Some Influencer",
            author_headline="LinkedIn Top Voice | Entrepreneur | Speaker",
            post_text="""
            🚀 AI is changing EVERYTHING!!!
            
            The companies that don't adopt AI will be left behind.
            Are you ready for the AI revolution?
            
            Like and share if you agree! 💪
            
            #AI #FutureOfWork #Innovation #Leadership
            """,
            post_url="https://linkedin.com/posts/test2",
            has_image=False,
            has_video=False,
            likes_approx="4,521",
            comments_approx="312",
            extracted_at="2026-01-04T09:01:00",
            screenshot_path="",
        ),
        RawPost(
            post_id="test_3",
            author_name="Shreya Shankar",
            author_headline="PhD student @ UC Berkeley | AI Data Quality",
            post_text="""
            Been running evals on RAG pipelines for 6 months. Here's what actually moves the needle:
            
            1. Chunk overlap matters less than chunk SIZE relative to your query length
            2. Hybrid search (BM25 + dense) consistently beats pure semantic by ~12% on recall
            3. MMR reranking > cross-encoder reranking for speed/quality tradeoff at scale
            4. Metadata filtering before vector search = 3x throughput, negligible quality loss
            
            Most teams skip #4 and then complain their RAG is slow. The retrieval stack 
            is usually the bottleneck, not the LLM.
            
            Full benchmark notebook in comments.
            """,
            post_url="https://linkedin.com/posts/test3",
            has_image=False,
            has_video=False,
            likes_approx="891",
            comments_approx="64",
            extracted_at="2026-01-04T09:02:00",
            screenshot_path="",
        ),
    ]

    results = analyze_posts_batch(mock_posts, delay_between=2.0)
    saved = filter_saved_posts(results)

    print(f"\n{'='*55}")
    print(f"  RESULTS: {len(saved)} posts would be saved")
    print(f"{'='*55}")

    for post, analysis in saved:
        print(f"\n  {post.author_name}")
        print(f"     Score:    {analysis.relevance_score}/10")
        print(f"     Topics:   {', '.join(analysis.matched_interests)}")
        print(f"     Summary:  {analysis.post_summary}")
        print(f"     Insight:  {analysis.key_insight}")
        if analysis.comment_draft:
            print(f"     Comment:  {analysis.comment_draft[:120]}...")
        if analysis.content_angle:
            print(f"     Angle:    {analysis.content_angle}")

    # Save test results to JSON
    import json
    from dataclasses import asdict

    output = []
    for post, analysis in results:
        output.append({
            "post": asdict(post),
            "analysis": analysis.model_dump(),
        })

    with open("session/analyzed_posts.json", "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[v] Full results saved to session/analyzed_posts.json")


if __name__ == "__main__":
    import pathlib
    pathlib.Path("session").mkdir(exist_ok=True)
    main()
