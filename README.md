# LinkedIn Feed Intelligence Agent 🤖

A personal AI agent that monitors LinkedIn profiles you care about, extracts their latest posts (from the past 2 weeks only), filters them by your interests using Gemini, and saves the relevant ones to Notion — with summaries, key insights, and ready-to-post comment drafts.

> Built to solve a real problem: missing valuable posts while scrolling LinkedIn, and never having time to engage with the right content at the right time.

---

## How It Works

```
profiles.json  ←  you manage this (add/remove people by name)
      ↓
Browser opens LinkedIn (local Playwright OR Browserbase cloud — your choice)
      ↓
Navigates to each person's activity page
Clicks "see more" → extracts full post text (no truncation)
Checks post date → skips anything older than 2 weeks
Gets latest 1-2 posts per profile (checks max 5 containers, stops early)
      ↓
Gemini 2.5 Flash analyzes each post:
  • Is this relevant to my interests? (score 1–10)
  • What's the key insight?
  • Should I comment? → drafts a comment in my voice
  • Could this become my own LinkedIn post?
      ↓
Score ≥ 7 → saved to Notion with full context
Score < 7 → skipped, logged, never seen again (dedup)
```

---

## Project Structure

```
linkedin_agent/
│
├── agent.py                  ← Main entry point. All commands live here.
│
├── profile_extractor.py      ← Core scraper. Opens LinkedIn, navigates to
│                               each profile's activity page, clicks "see more",
│                               filters by 2-week date window,
│                               extracts latest posts per person.
│                               Routes to local Playwright OR Browserbase
│                               based on USE_BROWSERBASE in .env.
│
├── browserbase_provider.py   ← Browserbase cloud browser (Path B).
│                               Stealth mode, CAPTCHA solving, persistent
│                               LinkedIn session via Contexts API.
│                               Only used when USE_BROWSERBASE=true.
│
├── profiles.py               ← Manage your profile watchlist.
│                               Add/remove people by name — URL auto-built.
│
├── analyzer.py               ← AI brain. Sends each post to Gemini with your
│                               interest profile. Returns structured analysis:
│                               score, insight, comment draft, content angle.
│
├── schemas.py                ← Pydantic model for Gemini's structured output.
│                               Also defines RawPost dataclass and save_raw_posts.
│
├── notion_saver.py           ← Saves relevant posts to Notion. Auto-creates
│                               the database on first run. Deduplicates by URL.
│                               Rich page: summary, insight, comment, angle.
│
├── linkedin_login.py         ← Handles LinkedIn auth via Playwright.
│                               Saves cookies so browser only opens once.
│                               Validates li_at + JSESSIONID are present.
│
├── dedup_store.py            ← Cross-run memory. Tracks every post ID + URL
│                               seen. Skips already-analyzed posts instantly.
│
├── retry.py                  ← Exponential backoff for all API calls.
│                               Gemini rate limits → 60s auto-wait.
│
├── logger.py                 ← Structured logging to console + session/agent.log.
│
├── post_generator.py         ← Turns saved content angles into LinkedIn post
│                               drafts using Gemini in your voice.
│
├── scheduler.py              ← Runs agent automatically on a schedule.
│                               Daily at set time, or every N hours.
│
├── setup_check.py            ← Pre-flight validator. Checks Python version,
│                               all packages, Playwright, .env keys, Gemini API,
│                               Notion access, filesystem. Run this first.
│
├── requirements.txt          ← All Python dependencies.
├── env.example               ← Template for credentials. Copy → .env.
│
└── session/                  ← Auto-created on first run.
    ├── linkedin_cookies.json     LinkedIn session (Playwright saves this)
    ├── bb_context_id.txt         Browserbase context ID (cloud mode only)
    ├── profiles.json             Your profile watchlist
    ├── seen_posts.json           Dedup store (cross-run memory)
    ├── raw_posts.json            Last extraction output (debug)
    ├── analyzed_posts.json       Last analysis results (full detail)
    ├── notion_db_id.txt          Cached Notion database ID
    └── agent.log                 Full run log with timestamps
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

### 4. Choose your browser mode

The agent supports two browser backends. Switch by editing one line in `.env`:

| Setting | What it does |
|---|---|
| `USE_BROWSERBASE=false` | **Local Playwright** (default) — free, uses saved cookies, runs locally |
| `USE_BROWSERBASE=true` | **Browserbase cloud** — stealth mode, CAPTCHA solving, verified browser |

**For Browserbase**, also add to `.env`:
```env
BROWSERBASE_API_KEY=bb_live_xxxxx
BROWSERBASE_PROJECT_ID=proj_xxxxx
```
Get both at [browserbase.com/settings](https://browserbase.com/settings). Free tier = 1 hr total. Developer plan ($20/mo) = 100 hrs (~3 months of daily use).

### 5. Validate everything

```bash
python setup_check.py
```

### 6. Login to LinkedIn (one-time, local mode only)

```bash
python linkedin_login.py
```

Browser opens, logs in, saves cookies to `session/linkedin_cookies.json`. In Browserbase mode, login is handled automatically on first run using your `.env` credentials.

---

## Usage

### Add profiles to track

```bash
# Just provide the name — URL is auto-built
python profiles.py add "Harrison Chase"
python profiles.py add "Andrej Karpathy" --username karpathy
python profiles.py add "Shreya Shankar" --note "RAG researcher"

# Manage list
python profiles.py list
python profiles.py remove "Yann LeCun"
```

> **Username flag:** LinkedIn usernames are usually `firstname-lastname`. If someone uses a custom URL (e.g. `karpathy` instead of `andrej-karpathy`), pass `--username` manually.

### Run the agent

```bash
# Full run (extract → analyze → save to Notion)
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

# Validate environment
python agent.py --setup-check

# Reset dedup — reprocess all posts
python agent.py --clear-seen

# Lower save threshold (default 7)
python agent.py --threshold 6
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
| Saved At | `2026-01-04` |

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

The extractor only captures posts from the **last 2 weeks**. If a profile's most recent posts are older than that, they are skipped and the agent moves on. It checks at most **5 post containers** per profile to avoid infinite scrolling, stopping as soon as 2 valid recent posts are found.

---

## Troubleshooting

**Browser opens but extracts 0 posts**
- Check `session/debug_PersonName.png` — screenshot of what the browser saw
- That person's activity may be set to private
- Try a different profile to confirm extraction works

**"Session expired" error (local mode)**
- Delete `session/linkedin_cookies.json` and re-run `python linkedin_login.py`

**Browserbase context expired**
- Delete `session/bb_context_id.txt` — a fresh context with new login will be created on next run

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
| Browser automation (local) | Playwright (Python) |
| Browser automation (cloud) | Browserbase (stealth, CAPTCHA solving) |
| LLM | Gemini 2.5 Flash (via `langchain-google-genai`) |
| Structured output | Pydantic + `with_structured_output(method="json_schema")` |
| Storage | Notion API (via `requests`) |
| Retry logic | Custom exponential backoff (`retry.py`) |
| Deduplication | Local JSON store (`dedup_store.py`) |
| Logging | Python `logging` → file + console |
| Scheduling | Python `asyncio` loop / cron |

---

## LLM Alternatives

Set in `.env` — Gemini is primary, Groq is fallback:

| Provider | Free Tier | Key Variable | Model Used |
|---|---|---|---|
| **Gemini Flash** | 1M tokens/day | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| **Groq** | Rate limited | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

To switch to Groq, comment out the Gemini block and uncomment Groq in `analyzer.py → get_llm()`.

---

*Built by Akshu Grewal | [Portfolio](https://akshu-grewal-portfolio.vercel.app) | [GitHub](https://github.com/Akshu24Tech)*
