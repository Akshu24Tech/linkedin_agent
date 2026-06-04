# LinkedIn Feed Intelligence Agent 🤖

A personal AI agent that monitors LinkedIn profiles you care about, extracts their latest posts (from the past 2 weeks only), filters them by your interests using Gemini, and saves the relevant ones to Notion — with summaries, key insights, and ready-to-post comment drafts.

> Built to solve a real problem: missing valuable posts while scrolling LinkedIn, and never having time to engage with the right content at the right time.

---

## How It Works

```
SQLite memory.db (persons, posts, analyses)  <-- Token Efficiency Gates (Skip if checked < 20h OR avg score < 3.0)
      |
      v
Browser opens LinkedIn (local Playwright with saved cookies)
      |
      v
Navigates to each person's activity page
Clicks "see more" -> extracts full post text (no truncation)
Checks post date -> skips anything older than 2 weeks
Gets latest 1-2 posts per profile (checks max 5 containers, stops early)
      |
      v
Gemini 2.5 Flash analyzes each post:
  • Is this relevant to my interests? (score 1-10)
  • What's the key insight?
  • Should I comment? -> drafts a comment in my voice
  • Could this become my own LinkedIn post?
      |
      v
Score >= 7 -> saved to Notion + recorded in SQLite database
Score < 7 -> skipped, recorded in SQLite (dedup) for instant lookup
```

---

## Project Structure

```
linkedin_agent/
│
├── agent.py                  - Main entry point. All commands live here.
│
├── profile_extractor.py      - Core scraper. Opens LinkedIn, navigates to
│                               each profile's activity page, clicks "see more",
│                               filters by 2-week date window,
│                               extracts latest posts per person using
│                               local Playwright with saved cookies.
│
├── memory.py                 - SQLite-backed memory core. Automatically creates and
│                               manages the database (session/memory.db), handles schema,
│                               implements token-efficiency gates, and provides direct CLI.
│
├── profiles.py               - Manage your profile watchlist. Thin wrapper over memory.py
│                               supporting CLI and backward-compatible CRUD.
│
├── analyzer.py               - AI brain. Sends each post to Gemini with your
│                               interest profile. Returns structured analysis:
│                               score, insight, comment draft, content angle.
│
├── schemas.py                - Pydantic model for Gemini's structured output.
│                               Also defines RawPost dataclass and save_raw_posts.
│
├── notion_saver.py           - Saves relevant posts to Notion. Auto-creates
│                               the database on first run. Deduplicates by URL.
│
├── linkedin_login.py         - Handles LinkedIn auth via Playwright.
│                               Saves cookies so browser only opens once.
│   
├── dedup_store.py            - Backward-compatibility seen-posts wrapper over memory.py.
│
├── retry.py                  - Exponential backoff for all API calls.
│                               Gemini rate limits -> 60s auto-wait.
│
├── logger.py                 - Structured logging to console + session/agent.log.
│
├── post_generator.py         - Turns saved content angles into LinkedIn post
│                               drafts using Gemini in your voice.
│
├── scheduler.py              - Runs agent automatically on a schedule.
│                               Daily at set time, or every N hours.
│
├── setup_check.py            - Pre-flight validator. Checks Python, packages, Playwright,
│                               .env keys, Gemini API, Notion, filesystem, and database.
│
├── requirements.txt          - All Python dependencies.
├── env.example               - Template for credentials. Copy -> .env.
│
└── session/                  - Auto-created on first run. Contains SQLite DB,
                                cookies, logs, and debug screenshots.
```

---

## Setup

### 1. Prerequisites

```bash
python --version   # needs 3.11+
node --version     # needs 16+ (for Playwright)
```

### 2. Install dependencies

```bash
cd linkedin_agent
python -m venv venv

# Activate
venv\Scripts\activate      # Windows
source venv/bin/activate   # Mac/Linux

pip install -r requirements.txt
playwright install chromium
```

### 3. Configure credentials

```bash
cp env.example .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free |
| `LINKEDIN_EMAIL` | Your LinkedIn login email |
| `LINKEDIN_PASSWORD` | Your LinkedIn password |
| `NOTION_TOKEN` | [notion.so/my-integrations](https://www.notion.so/my-integrations) → New Integration → copy secret |
| `NOTION_PARENT_PAGE_ID` | Create an empty Notion page → copy ID from URL (last 32 chars) |

> **Notion page setup:** After setting the page ID, go to that page → `···` menu → Connections → Add your integration. Required or you'll get a 403.

### 4. Validate everything

```bash
python setup_check.py
```

### 5. Login to LinkedIn (one-time setup)

```bash
python linkedin_login.py
```

Browser opens, logs in, and saves cookies to `session/linkedin_cookies.json`. After this, the agent uses saved cookies — no re-login needed.

---

## Usage

### Add profiles to track (`profiles.py`)

Using a full profile URL is strongly recommended to guarantee the correct user and avoid vanity name collisions on LinkedIn.

```bash
# RECOMMENDED: Add via profile URL
python profiles.py add --url "https://linkedin.com/in/karpathy/" --note "AI pioneer"
python profiles.py add "Harrison Chase" --url "https://www.linkedin.com/in/harrison-chase-961287118/"

# Add via name only (auto-derives vanity name; warns to verify)
python profiles.py add "Harrison Chase"
python profiles.py add "Andrej Karpathy" --username karpathy

# Manage list
python profiles.py list
python profiles.py remove "Yann LeCun"
```

### Run the agent (`agent.py`)

```bash
# Full pipeline run (extract → analyze → save to Notion)
python agent.py

# Test without writing to Notion
python agent.py --dry-run

# Just extract posts (no AI, fast)
python agent.py --extract-only

# Re-analyze last extraction without opening LinkedIn
python agent.py --analyze-only

# Read saved posts + copy comment drafts
python agent.py --view-saved

# Generate LinkedIn post drafts from saved content angles
python agent.py --generate-posts

# Check stats + profile list
python agent.py --stats

# Validate environment setup
python agent.py --setup-check

# Reset dedup — reprocess all posts
python agent.py --clear-seen

# Lower save threshold (default 7)
python agent.py --threshold 6

# Force re-verification for a person (bypasses 20h skip gates)
python agent.py --reverify "Harrison Chase"

# Add a profile directly via the agent
python agent.py --add-profile "https://www.linkedin.com/in/karpathy/" --profile-note "AI pioneer"
```

### Direct SQLite Memory Management (`memory.py`)

The SQLite database backend (`session/memory.db`) can be administered directly:

```bash
# List tracked profiles with stats directly from database
python memory.py persons list

# Re-verify a person to rebuild their history
python memory.py persons reverify "Andrej Karpathy"

# Show database seen and saved stats
python memory.py posts stats

# Clear seen posts from database (re-extract and re-analyze everything)
python memory.py posts clear-seen

# One-time migration: Import profiles.json + seen_posts.json into memory.db
python memory.py migrate
```

### Automate (run daily)

```bash
# Run every morning at 9 AM
python scheduler.py --time 09:00

# Run immediately, then daily
python scheduler.py --now --time 09:00

# Print cron / Windows Task Scheduler commands
python scheduler.py --print-cron
```

---

## Token Efficiency Gates 🧠

To keep your runs fast and save LLM token costs, the agent implements smart token gates in the SQLite memory core (`memory.py`):

1. **Daily Check Gate**: The agent remembers when each profile was last checked. If a profile was checked less than **20 hours ago**, it will be skipped entirely on subsequent runs that day.
2. **Consistently Irrelevant Gate**: If a tracked profile is checked **5 or more times** and its `avg_relevance_score` is below **3.0**, the agent will temporarily skip it from daily runs. This ensures you're not opening a browser or wasting Gemini tokens on profiles that consistently post unrelated content.

> **Force Re-verification**: If you want to bypass these gates and force-run a profile immediately, you can reset their verification status using:
> `python agent.py --reverify "Person Name"`  or  `python memory.py persons reverify "Person Name"`

---

## What Gets Saved to Notion

Each relevant post (score ≥ 7/10) becomes a rich Notion page:

**Database properties:**

| Field | Example |
|---|---|
| Title | `Harrison Chase: LangGraph 0.3 ships Redis checkpointing` |
| Score | `9` |
| Topics | `LangGraph`, `agentic AI` |
| Content Type | `tool_announcement` |
| Comment Drafted | ✅ |
| Post URL | Direct link to LinkedIn post |
| Saved At | `2026-05-27` (when we ran the agent) |
| Posted At | `2026-05-26T14:30:00` (when they posted it) |
| Post Age (days) | `3.0` |

**Inside each page:**
- 📋 **Summary** — 2-3 sentence description of what the post is actually about
- 💡 **Key Insight** — the one concrete takeaway (callout block)
- 📝 **Original Post** — full text + author + link
- 💬 **Comment Draft** — ready-to-copy comment in your voice (code block)
- ✍️ **Content Angle** — how this could become your own LinkedIn post
- 📊 **Analysis Details** — score, type, engagement, timestamp

---

## Interest Profile

The agent filters posts using a hardcoded interest profile in `analyzer.py` (`AKSHU_INTEREST_PROFILE`).

**Currently configured for:**
- LangGraph, LangChain, agentic AI systems
- Google ADK, Gemini, multi-agent architectures
- RAG pipelines, vector databases, retrieval optimization
- AI observability and tracing (Arize, LangSmith, RAGAS)
- Browser agents, computer use
- LLM deployment and inference optimization
- AI engineering (production-grade, not demos)
- LinkedIn content strategy for builders

**Skips:** generic AI hype, job posts, motivational quotes, surface-level takes, non-AI topics.

To tune it, edit `AKSHU_INTEREST_PROFILE` in `analyzer.py`.

---

## Post Date Filtering

The extractor only captures posts from the last N days (default: **14 days**, configured via `MAX_POST_AGE_DAYS` in `.env`). If a profile's most recent posts are older than that, they are skipped and the agent moves on. It checks at most **5 post containers** per profile to avoid infinite scrolling, stopping as soon as 2 valid recent posts are found. If a post's age is unknown due to layout changes, it is allowed through to avoid false negatives.

---

## Troubleshooting

**Browser opens but extracts 0 posts**
- Check `session/debug_PersonName.png` — screenshot of what the browser saw
- That person's activity may be set to private
- Try a different profile to confirm extraction works

**"Session expired" error (local mode)**
- Delete `session/linkedin_cookies.json` and re-run `python linkedin_login.py`


**Gemini rate limit**
- Free tier: 15 req/min. `retry.py` auto-waits 60s and retries.
- Reduce profiles or run at off-peak times

**Notion 403 error**
- You forgot to share the Notion page with your integration
- Page → `···` → Connections → Add your integration

**Wrong LinkedIn username auto-derived**
```bash
python profiles.py remove "Person Name"
python profiles.py add "Person Name" --username correct-username
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Browser automation | Playwright (Python) |
| LLM | Gemini 2.0 Flash (via `langchain-google-genai`) |
| Structured output | Pydantic + `with_structured_output(method="json_schema")` |
| Storage | Notion API (via `requests`) |
| Retry logic | Custom exponential backoff (`retry.py`) |
| Deduplication | SQLite database core (`memory.py`) |
| Logging | Python `logging` -> file + console |
| Scheduling | Python `asyncio` loop / cron |

---

## LLM Alternatives

Set in `.env` - Gemini is primary, Groq is fallback:

| Provider | Free Tier | Key Variable | Model Used |
|---|---|---|---|
| **Gemini Flash** | 1M tokens/day | `GEMINI_API_KEY` | `gemini-2.0-flash` |
| **Groq** | Rate limited | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

To switch to Groq, comment out the Gemini block and uncomment Groq in `analyzer.py -> get_llm()`.

---

*Built by Akshu Grewal | [Portfolio](https://akshu-grewal-portfolio.vercel.app) | [GitHub](https://github.com/Akshu24Tech)*
