# LinkedIn Feed Intelligence Agent

Personal AI agent that opens LinkedIn autonomously → reads your feed → filters posts by your interests → saves the good ones to Notion with summaries, key insights, and comment drafts.

---

## Project Structure

```
linkedin_agent/
├── agent.py              ← main entry point (all commands)
├── linkedin_login.py     ← cookie-based session management
├── feed_extractor.py     ← Playwright DOM scraper
├── vision_fallback.py    ← Gemini vision when DOM breaks
├── analyzer.py           ← Gemini interest matching + extraction
├── schemas.py            ← Pydantic structured output model
├── notion_saver.py       ← Notion database + rich page creation
├── dedup_store.py        ← cross-run deduplication
├── retry.py              ← exponential backoff for API calls
├── logger.py             ← structured logging to file + console
├── .env.example          ← copy → .env, fill credentials
├── requirements.txt
└── session/              ← auto-created
    ├── linkedin_cookies.json
    ├── seen_posts.json       ← dedup store
    ├── raw_posts.json        ← last extraction
    ├── analyzed_posts.json   ← last analysis results
    ├── feed_screenshot.png   ← vision fallback screenshot
    └── agent.log             ← full run log
```

---

## Setup (one-time)

```bash
# 1. Create venv + install deps
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac/Linux
pip install -r requirements.txt
playwright install chromium

# 2. Configure credentials
cp .env.example .env
# Fill in: GEMINI_API_KEY, LINKEDIN_EMAIL, LINKEDIN_PASSWORD
# Fill in: NOTION_TOKEN, NOTION_PARENT_PAGE_ID

# 3. Test each piece independently
python linkedin_login.py       # Test LinkedIn auth
python analyzer.py             # Test Gemini (uses mock posts, no LinkedIn)
python notion_saver.py         # Test Notion connection

# 4. Test Notion via CLI
python agent.py --test-notion
```

---

## Daily Usage

```bash
# Full run (most common)
python agent.py

# Dry run — analyze but don't write to Notion yet
python agent.py --dry-run

# Just pull posts (no AI, fast)
python agent.py --extract-only

# Re-analyze last extraction without opening LinkedIn
python agent.py --analyze-only

# See what was saved last run (with comment drafts)
python agent.py --view-saved

# Check stats
python agent.py --stats

# Reset dedup — reprocess posts already seen
python agent.py --clear-seen

# Pull more posts this run
python agent.py --posts 25

# Lower save threshold (default 7)
python agent.py --threshold 6
```

---

## What Gets Saved to Notion

Each page in your `LinkedIn Feed Intelligence` database contains:

| Field | Example |
|-------|---------|
| Title | `Shreya Shankar: Benchmark results comparing RAG retrieval...` |
| Score | `9` |
| Topics | `RAG pipelines`, `AI engineering` |
| Comment Drafted | ✅ |
| Post URL | Direct link |

**Inside each page:**
- 📋 Summary (2-3 sentences)
- 💡 Key insight (callout block)
- 📝 Original post text + author
- 💬 Comment draft (code block — easy to copy)
- ✍️ Content angle for your own posts
- 📊 Meta: score, type, engagement, timestamp

---

## Notion Setup

```
1. notion.so/my-integrations → New Integration → "LinkedIn Agent" → Submit
2. Copy "Internal Integration Secret" → NOTION_TOKEN in .env
3. Create an empty Notion page (anywhere)
4. URL: notion.so/workspace/My-Page-abc123def456...
   Copy the last 32 chars → NOTION_PARENT_PAGE_ID in .env
5. On that page → ··· menu → Connections → Add "LinkedIn Agent"
```

---

## LLM Setup

| Provider | Free Tier | Get Key |
|----------|-----------|---------|
| Gemini Flash | 1M tokens/day | aistudio.google.com/apikey |
| Groq | Rate limited | console.groq.com/keys |

Set `GEMINI_API_KEY` or `GROQ_API_KEY` in `.env`.

---

## Troubleshooting

**"No posts extracted"**
- Check `session/feed_screenshot.png` — see what the browser sees
- LinkedIn may have updated their DOM selectors
- Run `python agent.py --extract-only` to test just extraction

**"Gemini rate limit"**
- Free tier: 15 req/min. Retry handles this automatically with 60s wait.
- Reduce `POSTS_TO_COLLECT` or add `GEMINI_API_KEY` from paid account

**"Notion 403 error"**
- Check you've shared the page with your integration (step 5 of Notion setup)
- Delete `session/notion_db_id.txt` and re-run to recreate the database

**Cookies expired**
- Delete `session/linkedin_cookies.json` — agent will re-login automatically

---

## Interest Profile

Edit `AKSH_INTEREST_PROFILE` in `analyzer.py` to tune what gets saved.
Current interests: LangGraph, Google ADK, RAG pipelines, agentic AI,
vector DBs, AI observability, browser agents, LLM deployment, AI engineering.
