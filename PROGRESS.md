# LinkedIn Feed Intelligence Agent — Project Progress

## Problem Statement

**The core problem:**

> *"While scrolling LinkedIn, missed many useful posts and chance to be part of conversation or useful information. Got better things on time + post link."*

**In plain words:**
- Scrolling LinkedIn is passive and inefficient — good posts get buried
- No way to filter feed by what actually matters to you
- "See more" truncates posts — you miss context even when you do see them
- You miss the comment window — by the time you see a post, it's 3 days old
- No system to capture insights for future use (content ideas, reference material)

**What we wanted to build:**

```
LinkedIn → post (content + comment)
         → Questions: what is it about? how does it matter? how can it be used?
         ↓
     Extract useful things
         ↓
   Score it with Gemini
         ↓
   If score ≥ 7 → Auto-Save to LinkedIn Saved Posts
         ↓
     Save summary + insight to Notion
```

---

## What We've Built — Full Feature Inventory

### ✅ Core Pipeline

| Feature | File | Status |
|---|---|---|
| Extract posts from specific LinkedIn profiles | `profile_extractor.py` | Done |
| Navigate to `linkedin.com/in/username/recent-activity/all/` | `profile_extractor.py` | Done |
| Click "see more" → full untruncated post text | `profile_extractor.py` | Done |
| Filter posts older than 2 weeks (configurable) | `profile_extractor.py` | Done |
| Extract latest 1-2 posts per profile | `profile_extractor.py` | Done |
| Max 5 DOM containers checked per profile | `profile_extractor.py` | Done |
| Debug screenshots when extraction fails | `profile_extractor.py` | Done |

### ✅ AI Analysis (Gemini)

| Feature | File | Status |
|---|---|---|
| Relevance scoring 1-10 against interest profile | `analyzer.py` | Done |
| Key insight extraction | `analyzer.py` | Done |
| Comment draft in practitioner voice | `analyzer.py` | Done |
| Content angle for own LinkedIn posts | `analyzer.py` | Done |
| Content type classification | `analyzer.py` | Done |
| Structured output via Pydantic + json_schema mode | `schemas.py` | Done |
| Analysis result cached in SQLite (never re-calls Gemini) | `analyzer.py` + `memory.py` | Done |
| Exponential backoff retry for rate limits | `retry.py` | Done |

### ✅ Memory (SQLite)

| Feature | File | Status |
|---|---|---|
| Persistent SQLite DB at `session/memory.db` | `memory.py` | Done |
| `persons` table — who you track + stats | `memory.py` | Done |
| `posts` table — every post seen, indexed dedup | `memory.py` | Done |
| `analyses` table — every Gemini result cached | `memory.py` | Done |
| Cross-run dedup (never re-analyzes seen posts) | `memory.py` | Done |
| Person-level intelligence (avg score, top topics, frequency) | `memory.py` | Done |
| Token gate 1 — skip if checked < 20 hours ago | `memory.py` | Done |
| Token gate 2 — skip if avg score < 3.0 over 5+ runs | `memory.py` | Done |
| Vanity name extraction from any LinkedIn URL format | `memory.py` | Done |
| UTM param stripping from copied LinkedIn URLs | `memory.py` | Done |
| Migration from old `profiles.json` + `seen_posts.json` | `memory.py` | Done |

### ✅ Profile Management

| Feature | File | Status |
|---|---|---|
| Add profiles by URL (recommended — no name collisions) | `profiles.py` | Done |
| Add profiles by name (auto-derives username, warns) | `profiles.py` | Done |
| Remove profiles (soft delete, history preserved) | `profiles.py` | Done |
| List profiles with stats table | `profiles.py` | Done |
| LinkedIn vanity names guaranteed unique — used as IDs | `memory.py` | Done |

### ✅ Notion Integration

| Feature | File | Status |
|---|---|---|
| Auto-create database on first run | `notion_saver.py` | Done |
| Rich page per saved post | `notion_saver.py` | Done |
| Deduplication by post URL | `notion_saver.py` | Done |
| Database properties: score, topics, content type, dates | `notion_saver.py` | Done |
| Page body: summary, insight, original text, comment, angle | `notion_saver.py` | Done |
| Posted At + Post Age (days) stored | `notion_saver.py` | Done |
| Retry on Notion API failures | `notion_saver.py` + `retry.py` | Done |

### ✅ Browser & Auth

| Feature | File | Status |
|---|---|---|
| LinkedIn login via Playwright (one-time) | `linkedin_login.py` | Done |
| Cookie-based session persistence | `linkedin_login.py` | Done |
| Validates `li_at` + `JSESSIONID` present | `linkedin_login.py` | Done |
| Non-headless mode (avoids bot detection) | `profile_extractor.py` | Done |

### ✅ CLI & Automation

| Feature | File | Status |
|---|---|---|
| Full pipeline run | `agent.py` | Done |
| `--dry-run` (no Notion write) | `agent.py` | Done |
| `--extract-only` | `agent.py` | Done |
| `--analyze-only` (re-analyze from JSON) | `agent.py` | Done |
| `--view-saved` (read posts + comment drafts) | `agent.py` | Done |
| `--generate-posts` (LinkedIn post drafts) | `agent.py` | Done |
| `--stats` (DB stats + profile list) | `agent.py` | Done |
| `--clear-seen` (reset dedup) | `agent.py` | Done |
| `--threshold N` (custom save threshold) | `agent.py` | Done |
| `--reverify` (bypass skip gates for one person) | `agent.py` | Done |
| `--add-profile` (add by URL directly) | `agent.py` | Done |
| Daily scheduler with custom time | `scheduler.py` | Done |
| Cron / Task Scheduler command printer | `scheduler.py` | Done |
| Pre-flight environment validator | `setup_check.py` | Done |
| Structured logging to file + console | `logger.py` | Done |

### ✅ Content Generation

| Feature | File | Status |
|---|---|---|
| Generate LinkedIn post drafts from saved content angles | `post_generator.py` | Done |
| Interactive mode (pick angle, copy to clipboard) | `post_generator.py` | Done |
| Batch mode (generate all angles) | `post_generator.py` | Done |
| Writes in practitioner voice (your style) | `post_generator.py` | Done |

### ✅ Auto-Save to LinkedIn Saved Posts

| Feature | File | Status |
|---|---|---|
| Opens each qualifying post (score ≥ 7) in browser | `post_saver.py` | Done |
| Clicks three-dot menu → Save on LinkedIn | `post_saver.py` | Done |
| Handles "already saved" state gracefully (idempotent) | `post_saver.py` | Done |
| Memory dedup — never re-saves in future runs | `post_saver.py` + `memory.py` | Done |
| Human-paced delay between saves (anti-detection) | `post_saver.py` | Done |
| `--save-posts` standalone command | `agent.py` | Done |
| Auto-runs in full pipeline (Step 3, before Notion) | `agent.py` | Done |
| `linkedin_saved` column in posts table | `memory.py` | Done |
| Skipped automatically in `--dry-run` mode | `agent.py` | Done |

### ✅ Profile Discovery (LangGraph)

| Feature | File | Status |
|---|---|---|
| Discover relevant profiles via web search | `profile_discovery_agent.py` | Done |
| Gemini scores candidate profiles | `profile_discovery_agent.py` | Done |
| Interactive approve/reject flow (HITL) | `profile_discovery_agent.py` | Done |
| `--auto-add` mode (adds if score ≥ threshold) | `profile_discovery_agent.py` | Done |
| `--dry-run` mode (search without saving) | `profile_discovery_agent.py` | Done |

---

## What Is NOT Done Yet

| Gap | Priority | Why It Matters |
|---|---|---|
| Tests (unit + integration) | Medium | Required before CI/CD makes sense |
| CI/CD GitHub Actions pipeline | Low | Needs tests first |
| Arize Phoenix observability | Low | Nice-to-have for tracing Gemini calls |

---

## Architecture — How It All Connects

```
┌─────────────────────────────────────────────────────────────────┐
│                         agent.py (CLI)                          │
│  Full run / dry-run / extract-only / analyze-only /             │
│  view-saved / save-posts                                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
│ → 2 posts max   │  │          │  │ DB on first  │
└────────┬────────┘  └────┬─────┘  │ run          │
         │               │         └──────────────┘
         │               │
         ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      memory.py (SQLite)                         │
│                    session/memory.db                            │
│                                                                 │
│  ┌──────────────┐  ┌──────────────────────────────┐  ┌──────────────────────┐  │
│  │   persons    │  │            posts             │  │      analyses        │  │
│  │              │  │                              │  │                      │  │
│  │ username     │  │ post_id (PK)  was_analyzed   │  │ post_id (PK)         │  │
│  │ display_name │  │ post_url      was_saved       │  │ relevance_score      │  │
│  │ activity_url │  │ author        linkedin_saved  │  │ matched_topics       │  │
│  │ avg_score    │  │ post_text     relevance_score │  │ post_summary         │  │
│  │ score_history│  │ extracted_at                  │  │ key_insight          │  │
│  │ top_topics   │  └──────────────────────────────┘  │ comment_draft        │  │
│  │ total_runs   │                                     │ content_angle        │  │
│  │ last_checked │                                     │ analyzed_at          │  │
│  └──────────────┘                                     └──────────────────────┘  │
│                                                                 │
│  Token Gate 1: skip if last_checked < 20h ago                  │
│  Token Gate 2: skip if avg_score < 3.0 AND runs >= 5           │
│  Dedup:        O(1) indexed lookup by post_id OR post_url       │
│  Save dedup:   linkedin_saved=1 → skip re-saving next run      │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│              profile_discovery_agent.py (LangGraph)             │
│                                                                 │
│  StateGraph:                                                    │
│                                                                 │
│  [search_web] → finds candidate LinkedIn profiles               │
│       ↓                                                         │
│  [score_candidates] → Gemini evaluates post quality             │
│       ↓                                                         │
│  [human_gate] → you approve / reject each candidate             │
│       ↓                                                         │
│  [add_to_memory] → saves approved profiles to memory.db         │
└─────────────────────────────────────────────────────────────────┘

Supporting modules:
  linkedin_login.py      → one-time browser auth, saves cookies
  retry.py               → exponential backoff (Gemini + Notion APIs)
  logger.py              → structured logs → session/agent.log
  scheduler.py           → daily automation at set time
  post_generator.py      → turns content angles into LinkedIn post drafts
  post_saver.py          → opens post URLs and clicks LinkedIn Save button
  setup_check.py         → validates entire environment before first run
  profiles.py            → thin CLI wrapper over memory.py
```

---

## Technology Decisions — Why Each Tool

| Tool | Why Chosen | Alternative Considered |
|---|---|---|
| **Playwright** | Non-headless, cookie auth, real Chrome fingerprint | Selenium (more verbose), requests (can't handle JS) |
| **Gemini 2.0 Flash** | Free tier (1M tokens/day), fast, Pydantic structured output | GPT-4o (paid), Groq (rate limited free tier) |
| **SQLite** | Zero infrastructure, single file, indexed lookups, persists forever | Neo4j (overkill for CRUD), JSON files (breaks at scale) |
| **Notion API** | Already used as second brain, rich page format, filterable DB | Obsidian (no API), plain markdown (no structure) |
| **LangGraph** | Profile discovery needs genuine agentic decision-making | Sequential pipeline (no branching, no replanning) |
| **Pydantic structured output** | Guarantees schema compliance, no regex parsing | Raw JSON prompting (hallucinated fields, missing keys) |

---

## Approaches That Failed (And Why)

| Approach | What Happened | Why It Failed |
|---|---|---|
| DOM scrape main feed | Tried first | Sidebar + ads mixed in, truncated posts, breaks weekly |
| Voyager REST API (`updatesV2`) | 400 error | Endpoint deprecated |
| Voyager GraphQL API | 400 error | `queryId` rotates with every LinkedIn frontend deploy |
| Screenshot + Gemini vision | Tried as fallback | Captures UI chrome, sidebar, ads — not clean post data |
| `seen_posts.json` flat file dedup | Used until SQLite | Reads entire file every check, breaks at scale |
| HITL verification at run time | Built, then removed | Unnecessary — LinkedIn vanity names are globally unique |

---

## Run Order (First Time Setup)

```bash
# 1. Install
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure
cp env.example .env
# Fill in: GEMINI_API_KEY, LINKEDIN_EMAIL, LINKEDIN_PASSWORD
# Fill in: NOTION_TOKEN, NOTION_PARENT_PAGE_ID

# 3. Validate
python setup_check.py

# 4. Login to LinkedIn (one-time)
python linkedin_login.py

# 5. Add profiles to track
python profiles.py add --url "https://linkedin.com/in/karpathy/"
python profiles.py add --url "https://www.linkedin.com/in/harrison-chase-961287118/"

# 6. Test run (no Notion write)
python agent.py --dry-run

# 7. Full run
python agent.py

# 8. Automate
python scheduler.py --time 09:00
```

---

## Key Numbers

| Metric | Value |
|---|---|
| Profiles tested live | 4 (Karpathy, LeCun, self, Raahul Seshadri) |
| Posts extracted in test run | 6 across 4 profiles |
| Avg post length (Voyager DOM) | ~800 chars (full text, no truncation) |
| Token gates save | ~80% of potential Gemini calls on repeated runs |
| LinkedIn vanity name | Globally unique — solves all name collision problems |

---

*This document tracks the real history of the project — what worked, what failed, what's next.*
*Update the "What Is NOT Done Yet" table as gaps get closed.*
