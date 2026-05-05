"""
schemas.py
----------
Pydantic models for structured AI output.

Every post that passes interest filtering gets analyzed into this schema.
Gemini is forced to return exactly this structure - no hallucinated fields,
no missing keys.
"""

from pydantic import BaseModel, Field
from typing import Literal


class PostAnalysis(BaseModel):
    """
    Full AI analysis of a single LinkedIn post.
    Gemini fills every field via structured output (JSON schema mode).
    """

    # -- Interest matching -----------------------------------------------------
    is_relevant: bool = Field(
        description=(
            "True if this post is meaningfully related to at least one of Aksh's "
            "interest areas: LangGraph, LangChain, agentic AI, Google ADK, Gemini, "
            "multi-agent systems, RAG pipelines, vector DBs, AI observability/tracing, "
            "browser agents, LLM deployment, AI engineering (production-grade), "
            "LinkedIn content strategy for builders/devs. "
            "False for generic AI hype, job posts, motivational content, "
            "non-AI topics, or surface-level takes with no depth."
        )
    )

    relevance_score: int = Field(
        description=(
            "Relevance score from 1-10. "
            "1-3: Not relevant or pure hype. "
            "4-6: Loosely related but low signal. "
            "7-8: Clearly relevant, has real insight. "
            "9-10: Directly in core interest area with actionable depth. "
            "Only posts scoring 7+ are saved."
        ),
        ge=1,
        le=10,
    )

    matched_interests: list[str] = Field(
        description=(
            "List of specific interest areas this post matches. "
            "E.g. ['LangGraph', 'agentic AI'] or ['RAG pipelines', 'vector DBs']. "
            "Empty list if not relevant."
        )
    )

    # -- Content extraction ----------------------------------------------------
    post_summary: str = Field(
        description=(
            "2-3 sentence summary of what this post is actually about. "
            "Be specific - not 'talks about AI' but 'explains how to implement "
            "LangGraph checkpointing with Redis for long-running agents'."
        )
    )

    key_insight: str = Field(
        description=(
            "The single most useful/actionable thing from this post. "
            "1-2 sentences max. This is the 'extract useful things' from Aksh's notes. "
            "If there's no real insight, write: 'No concrete insight - surface level.'"
        )
    )

    content_type: Literal[
        "tutorial",
        "tool_announcement",
        "opinion_take",
        "case_study",
        "resource_list",
        "job_post",
        "personal_update",
        "hype_post",
        "other",
    ] = Field(
        description="What kind of post is this."
    )

    # -- Action recommendations ------------------------------------------------
    should_comment: bool = Field(
        description=(
            "True if commenting would be valuable - post has traction, "
            "Akshu has something real to add, or engaging helps with visibility. "
            "False for job posts, low-engagement posts, or posts where commenting adds nothing."
        )
    )

    comment_draft: str = Field(
        description=(
            "If should_comment is True: draft a short, genuine comment (2-4 sentences) "
            "in Akshu's voice - practitioner tone, direct, no cringe phrases like "
            "'Great post!' or 'Absolutely agree!'. Should add value or share a real experience. "
            "If should_comment is False: empty string."
        )
    )

    should_save: bool = Field(
        description=(
            "True if this post has lasting reference value - a technique, tool, "
            "pattern, or insight Aksh might want to revisit. "
            "False for time-sensitive news, opinions without substance, or anything "
            "that'll be irrelevant in a week."
        )
    )

    content_angle: str = Field(
        description=(
            "If this post could inspire a LinkedIn post from Aksh's perspective, "
            "describe the angle in 1 sentence (e.g. 'Write about your experience "
            "using LangGraph checkpointing in production'). "
            "Empty string if no content angle."
        )
    )

    # -- Skip reason (for non-relevant posts) ---------------------------------
    skip_reason: str = Field(
        description=(
            "If is_relevant is False or score < 7: one sentence explaining why this "
            "post was skipped. E.g. 'Generic motivational content, no technical depth.' "
            "If relevant and saved, write: 'Saved.'"
        )
    )
