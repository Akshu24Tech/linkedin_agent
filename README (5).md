# LinkedIn Feed Intelligence Agent

Personal AI agent that opens LinkedIn, reads your feed, filters posts by your interests, and saves the useful ones to Notion — so you never miss relevant content while scrolling.

---

## Project Structure

```
linkedin_agent/
├── .env.example          ← copy this to .env, fill in credentials
├── requirements.txt      ← Python dependencies
├── agent.py              ← main entry point (run this)
├── linkedin_login.py     ← handles login + cookie session
├── feed_extractor.py     ← scrolls feed, extracts post data
├── session/              ← auto-created, stores cookies + debug output
│   ├── linkedin_cookies.json
│   ├── raw_posts.json
│   └── feed_screenshot.png
└── README.md
```

---

## Day 1 Setup — Exact Commands

### 1. Prerequisites

Make sure you have Python 3.11+ installed:
```bash
python --version
# Should show Python 3.11.x or higher
```

### 2. Create virtual environment

```bash
# Navigate to project folder
cd linkedin_agent

# Create venv
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Playwright browsers

```bash
playwright install chromium
```
This downloads a clean Chromium browser (~150MB). One-time setup.

### 5. Set up your credentials

```bash
# Copy the template
cp .env.example .env

# Edit .env and fill in:
# - GEMINI_API_KEY  (get free at https://aistudio.google.com/apikey)
# - LINKEDIN_EMAIL
# - LINKEDIN_PASSWORD
```

### 6. Test login first

```bash
python linkedin_login.py
```

**What happens:**
- Browser opens (you'll see it, not headless)
- Logs into LinkedIn with your credentials
- Saves cookies to `session/linkedin_cookies.json`
- Takes a screenshot to `session/login_test.png`

**If LinkedIn shows CAPTCHA:** Just solve it manually in the browser window. The script waits 30 seconds.

### 7. Run the full Day 1 agent

```bash
python agent.py
```

**What happens:**
- Loads saved cookies (no re-login needed)
- Opens LinkedIn feed
- Scrolls down to load posts
- Extracts post text, author, URL, engagement
- Saves everything to `session/raw_posts.json`
- Prints a preview of first 3 posts

---

## Troubleshooting

**"No posts extracted" / empty raw_posts.json**

LinkedIn changed their DOM. Run the screenshot fallback test:
```bash
python feed_extractor.py
```
Check `session/feed_screenshot.png` to see what the agent sees.

**"Login failed"**

Check your `.env` credentials. If 2FA is on for your LinkedIn, disable it temporarily or use app password.

**Cookies expired**

Delete `session/linkedin_cookies.json` and run again - it will re-login.

**ModuleNotFoundError: browser_use**

```bash
pip install browser-use
```

---

## LLM Options (Day 2+)

You'll need one of these for AI analysis (free tiers available):

| Provider | Free Tier | Speed | Notes |
|----------|-----------|-------|-------|
| **Gemini Flash** | ✓ 1M tokens/day | Fast | Recommended |
| **Groq** | ✓ Rate limited | Very fast | Llama/Mixtral |
| **Ollama** | ✓ Unlimited | Slow (local) | No internet needed |

Get Gemini API key (free): https://aistudio.google.com/apikey

---

## What's Coming

- **Day 2:** Gemini interest-matching — does this post relate to LangGraph / RAG / agentic AI?
- **Day 3:** Relevance scoring + comment draft generation
- **Day 4:** Notion save integration
- **Day 5:** End-to-end pipeline
