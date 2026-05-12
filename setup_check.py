"""
setup_check.py
──────────────
Validates your entire environment before running the agent.
Run this ONCE after cloning / setting up .env.

Checks:
  ✓ Python version
  ✓ All required packages installed
  ✓ .env file exists with all keys filled
  ✓ Gemini API key works (test call)
  ✓ Notion token + page ID work
  ✓ Playwright + Chromium installed
  ✓ Session folder writable

Run:
  python setup_check.py
"""

import sys
import os
import subprocess
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"
INFO = "\033[90m·\033[0m"

errors = []
warnings = []


def check(label: str, ok: bool, detail: str = "", fix: str = ""):
    if ok:
        print(f"  {PASS}  {label}")
        if detail:
            print(f"     {INFO} {detail}")
    else:
        print(f"  {FAIL}  {label}")
        if detail:
            print(f"     \033[31m{detail}\033[0m")
        if fix:
            print(f"     \033[33mFix: {fix}\033[0m")
        errors.append(label)


def warn(label: str, detail: str = "", fix: str = ""):
    print(f"  {WARN}  {label}")
    if detail:
        print(f"     \033[33m{detail}\033[0m")
    if fix:
        print(f"     Fix: {fix}\033[0m")
    warnings.append(label)


def section(title: str):
    print(f"\n  {'─'*45}")
    print(f"  {title}")
    print(f"  {'─'*45}")


# ── Checks ────────────────────────────────────────────────────────────────────

def check_python():
    section("Python")
    v = sys.version_info
    check(
        f"Python {v.major}.{v.minor}.{v.micro}",
        v >= (3, 11),
        fix="Install Python 3.11+ from python.org"
    )


def check_packages():
    section("Packages")
    required = {
        "playwright": "playwright",
        "dotenv": "python-dotenv",
        "pydantic": "pydantic",
        "requests": "requests",
        "langchain_core": "langchain-core",
        "langchain_google_genai": "langchain-google-genai",
        "google.generativeai": "google-generativeai",
    }
    optional = {
        "langchain_groq": "langchain-groq (optional — Groq fallback)",
    }

    for module, pkg in required.items():
        try:
            __import__(module)
            check(pkg, True)
        except ImportError:
            check(pkg, False, fix=f"pip install {pkg.split(' ')[0]}")

    for module, label in optional.items():
        try:
            __import__(module)
            check(label, True)
        except ImportError:
            warn(label, "Not installed — only needed if using Groq instead of Gemini")


def check_playwright():
    section("Playwright / Chromium")
    try:
        result = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True
        )
        # If chromium is installed, dry-run output mentions it
        installed = "chromium" in result.stdout.lower() or result.returncode == 0
        check("Playwright CLI available", True)
    except FileNotFoundError:
        check("Playwright CLI available", False, fix="pip install playwright")
        return

    # Check chromium binary
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        check("Chromium browser", True)
    except Exception as e:
        check("Chromium browser", False,
              detail=str(e)[:80],
              fix="playwright install chromium")


def check_env():
    section(".env File")
    from dotenv import load_dotenv
    load_dotenv()

    env_path = Path(".env")
    check(".env file exists", env_path.exists(),
          fix="cp .env.example .env  then fill in your values")

    if not env_path.exists():
        return

    # Check all required keys
    keys = {
        "GEMINI_API_KEY": ("Gemini API key", "aistudio.google.com/apikey"),
        "LINKEDIN_EMAIL": ("LinkedIn email", "your LinkedIn login email"),
        "LINKEDIN_PASSWORD": ("LinkedIn password", "your LinkedIn login password"),
        "NOTION_TOKEN": ("Notion token", "notion.so/my-integrations"),
        "NOTION_PARENT_PAGE_ID": ("Notion page ID", "copy from Notion page URL"),
    }

    placeholder_values = {
        "your_gemini_api_key_here", "your_notion_token_here",
        "your_notion_page_id_here", "your_linkedin_email@gmail.com",
        "your_linkedin_password_here", ""
    }

    for key, (label, source) in keys.items():
        val = os.getenv(key, "")
        if not val or val in placeholder_values:
            check(f"{label} ({key})", False,
                  detail="Not set or still placeholder",
                  fix=f"Get from: {source}")
        else:
            check(f"{label} ({key})", True,
                  detail=f"{val[:4]}{'*' * (len(val)-4) if len(val) > 4 else '***'}")

    # Optional
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key and groq_key not in placeholder_values:
        check("Groq API key (optional)", True)
    else:
        print(f"  {INFO}  Groq API key — not set (optional, Gemini is primary)")


def check_gemini():
    section("Gemini API")
    from dotenv import load_dotenv
    load_dotenv()

    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key == "your_gemini_api_key_here":
        check("Gemini test call", False, detail="API key not set")
        return

    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content("Reply with just the word: OK")
        result = response.text.strip()
        check("Gemini test call", "ok" in result.lower(),
              detail=f"Response: {result[:30]}")
    except Exception as e:
        err = str(e)
        if "quota" in err.lower() or "429" in err.lower():
            warn("Gemini test call", "Rate limited but key is valid")
        elif "invalid" in err.lower() or "api_key" in err.lower():
            check("Gemini test call", False,
                  detail="Invalid API key",
                  fix="Check your GEMINI_API_KEY at aistudio.google.com")
        else:
            check("Gemini test call", False, detail=err[:80])


def check_notion():
    section("Notion API")
    from dotenv import load_dotenv
    load_dotenv()
    import requests

    token = os.getenv("NOTION_TOKEN", "")
    page_id = os.getenv("NOTION_PARENT_PAGE_ID", "")
    placeholders = {"your_notion_token_here", "your_notion_page_id_here", ""}

    if token in placeholders:
        check("Notion auth", False, fix="Set NOTION_TOKEN in .env")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }

    # Test auth
    try:
        res = requests.get("https://api.notion.com/v1/users/me", headers=headers, timeout=10)
        if res.status_code == 200:
            name = res.json().get("name", "Unknown")
            check("Notion auth", True, detail=f"Integration: {name}")
        elif res.status_code == 401:
            check("Notion auth", False,
                  detail="Invalid token",
                  fix="Re-copy token from notion.so/my-integrations")
            return
        else:
            check("Notion auth", False, detail=f"Status {res.status_code}")
            return
    except Exception as e:
        check("Notion auth", False, detail=str(e)[:60])
        return

    # Test page access
    if page_id in placeholders:
        check("Notion page access", False, fix="Set NOTION_PARENT_PAGE_ID in .env")
        return

    clean_id = page_id.replace("-", "")
    try:
        res = requests.get(
            f"https://api.notion.com/v1/pages/{clean_id}",
            headers=headers, timeout=10
        )
        if res.status_code == 200:
            title = "Unknown"
            props = res.json().get("properties", {})
            for v in props.values():
                if v.get("type") == "title":
                    parts = v.get("title", [])
                    if parts:
                        title = parts[0].get("plain_text", "Unknown")
            check("Notion page access", True, detail=f"Page: {title[:40]}")
        elif res.status_code == 404:
            check("Notion page access", False,
                  detail="Page not found or integration not connected",
                  fix="On that Notion page: ··· → Connections → Add your integration")
        else:
            check("Notion page access", False, detail=f"Status {res.status_code}: {res.text[:80]}")
    except Exception as e:
        check("Notion page access", False, detail=str(e)[:60])


def check_filesystem():
    section("Filesystem")
    Path("session").mkdir(exist_ok=True)
    test_file = Path("session/.write_test")
    try:
        test_file.write_text("ok")
        test_file.unlink()
        check("session/ folder writable", True)
    except Exception as e:
        check("session/ folder writable", False, detail=str(e))

    check(".env.example exists", Path(".env.example").exists())
    check("requirements.txt exists", Path("requirements.txt").exists())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*50)
    print("  LinkedIn Feed Agent — Setup Check")
    print("═"*50)

    check_python()
    check_packages()
    check_playwright()
    check_env()
    check_gemini()
    check_notion()
    check_filesystem()

    # ── Result ────────────────────────────────────────────────────────────────
    print("\n" + "═"*50)

    if not errors:
        print(f"  \033[32m✓ All checks passed!\033[0m")
        print(f"\n  You're good to go. Run:")
        print(f"    python agent.py --dry-run    ← test run (no Notion write)")
        print(f"    python agent.py              ← full run")
    else:
        print(f"  \033[31m✗ {len(errors)} check(s) failed:\033[0m")
        for e in errors:
            print(f"    · {e}")
        print(f"\n  Fix the above and re-run: python setup_check.py")

    if warnings:
        print(f"\n  \033[33m{len(warnings)} warning(s):\033[0m")
        for w in warnings:
            print(f"    · {w}")

    print()


if __name__ == "__main__":
    main()
