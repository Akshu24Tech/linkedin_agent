"""
profiles.py
───────────
Manages the list of LinkedIn profiles to track.

Key idea: you only provide the person's name or their LinkedIn username.
The code builds the URL automatically.

LinkedIn profile URL pattern:
  linkedin.com/in/<username>/recent-activity/all/

Username is usually: firstname-lastname (e.g. "andrew-ng" for Andrew Ng)
If someone has a custom URL it might differ — the lookup handles both.

Usage:
  python profiles.py add "Andrew Ng"
  python profiles.py add "Andrej Karpathy" --username karpathy
  python profiles.py list
  python profiles.py remove "Andrew Ng"
  python profiles.py test "Andrew Ng"     ← verify URL is reachable

Data stored in: session/profiles.json
"""

import json
import re
import argparse
from pathlib import Path
from datetime import datetime

PROFILES_FILE = Path("session/profiles.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def name_to_username(name: str) -> str:
    """
    Convert a display name to a likely LinkedIn username slug.
    'Andrew Ng'       → 'andrew-ng'
    'Andrej Karpathy' → 'andrej-karpathy'
    'Yann LeCun'      → 'yann-lecun'
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)   # remove special chars
    slug = re.sub(r"\s+", "-", slug)        # spaces → hyphens
    slug = re.sub(r"-+", "-", slug)         # collapse multiple hyphens
    return slug.strip("-")


def build_activity_url(username: str) -> str:
    """Build the LinkedIn recent-activity URL for a profile username."""
    return f"https://www.linkedin.com/in/{username}/recent-activity/all/"


def build_profile_url(username: str) -> str:
    """Build the base LinkedIn profile URL."""
    return f"https://www.linkedin.com/in/{username}/"


# ── Storage ───────────────────────────────────────────────────────────────────

def load_profiles() -> list[dict]:
    """Load profile list from JSON. Returns list of profile dicts."""
    Path("session").mkdir(exist_ok=True)
    if not PROFILES_FILE.exists():
        return []
    with open(PROFILES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_profiles(profiles: list[dict]) -> None:
    Path("session").mkdir(exist_ok=True)
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)


def get_profile_by_name(name: str) -> dict | None:
    """Find a profile by display name (case-insensitive)."""
    profiles = load_profiles()
    name_lower = name.lower().strip()
    for p in profiles:
        if p["name"].lower().strip() == name_lower:
            return p
    return None


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_profile(name: str, username: str = None, note: str = "") -> dict:
    """
    Add a profile to the tracking list.

    Args:
        name:     Display name, e.g. "Andrew Ng"
        username: LinkedIn username if known, e.g. "andrewyng"
                  If not provided, auto-derived from name.
        note:     Why you're tracking this person.
    """
    profiles = load_profiles()

    # Check duplicate
    if get_profile_by_name(name):
        print(f"[!] '{name}' already in your list.")
        return get_profile_by_name(name)

    # Derive username if not given
    if not username:
        username = name_to_username(name)
        print(f"[i] Auto-derived username: '{username}'")
        print(f"    If wrong, re-add with: python profiles.py add \"{name}\" --username <correct-username>")

    profile = {
        "name": name,
        "username": username,
        "activity_url": build_activity_url(username),
        "profile_url": build_profile_url(username),
        "note": note,
        "added_at": datetime.now().isoformat(),
        "last_checked": None,
        "posts_collected": 0,
    }

    profiles.append(profile)
    save_profiles(profiles)
    print(f"[✓] Added: {name} → {profile['activity_url']}")
    return profile


def remove_profile(name: str) -> bool:
    """Remove a profile from the tracking list by display name."""
    profiles = load_profiles()
    original_count = len(profiles)
    profiles = [p for p in profiles if p["name"].lower().strip() != name.lower().strip()]

    if len(profiles) == original_count:
        print(f"[!] '{name}' not found in your list.")
        return False

    save_profiles(profiles)
    print(f"[✓] Removed: {name}")
    return True


def update_last_checked(username: str, posts_collected: int) -> None:
    """Update metadata after a successful scrape."""
    profiles = load_profiles()
    for p in profiles:
        if p["username"] == username:
            p["last_checked"] = datetime.now().isoformat()
            p["posts_collected"] = p.get("posts_collected", 0) + posts_collected
            break
    save_profiles(profiles)


def list_profiles() -> list[dict]:
    """Return all tracked profiles."""
    return load_profiles()


# ── CLI ───────────────────────────────────────────────────────────────────────

def print_profiles_table(profiles: list[dict]) -> None:
    if not profiles:
        print("\n  No profiles tracked yet.")
        print("  Add one: python profiles.py add \"Andrew Ng\"")
        return

    print(f"\n  {'─'*60}")
    print(f"  Tracked Profiles ({len(profiles)})")
    print(f"  {'─'*60}")
    for i, p in enumerate(profiles, 1):
        last = p.get("last_checked", "never")
        if last and last != "never":
            last = last[:10]  # just the date
        print(f"\n  [{i}] {p['name']}")
        print(f"       Username:     {p['username']}")
        print(f"       Activity URL: {p['activity_url']}")
        if p.get("note"):
            print(f"       Note:         {p['note']}")
        print(f"       Last checked: {last or 'never'}")
        print(f"       Posts found:  {p.get('posts_collected', 0)}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Manage LinkedIn profiles to track",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python profiles.py add "Andrew Ng"
  python profiles.py add "Andrej Karpathy" --username karpathy
  python profiles.py add "Harrison Chase" --note "LangChain founder"
  python profiles.py remove "Andrew Ng"
  python profiles.py list
        """
    )
    sub = parser.add_subparsers(dest="cmd")

    # add
    p_add = sub.add_parser("add", help="Add a profile to track")
    p_add.add_argument("name", help='Display name, e.g. "Andrew Ng"')
    p_add.add_argument("--username", help="LinkedIn username if auto-derive is wrong")
    p_add.add_argument("--note", default="", help="Why you're tracking this person")

    # remove
    p_rm = sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("name", help="Display name to remove")

    # list
    sub.add_parser("list", help="List all tracked profiles")

    args = parser.parse_args()

    if args.cmd == "add":
        add_profile(args.name, username=args.username, note=args.note)

    elif args.cmd == "remove":
        remove_profile(args.name)

    elif args.cmd == "list":
        print_profiles_table(load_profiles())

    else:
        parser.print_help()
        print("\n  Quick start:")
        print('  python profiles.py add "Harrison Chase" --note "LangChain founder"')
        print('  python profiles.py add "Andrej Karpathy"')
        print('  python profiles.py list')


if __name__ == "__main__":
    main()
