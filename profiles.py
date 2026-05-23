"""
profiles.py
───────────
Manages the list of LinkedIn profiles to track.
Now fully delegated to the new SQLite database (memory.py) as a wrapper.
"""

import argparse
from datetime import datetime
import memory

# Public helpers re-exported from memory.py or kept here
def name_to_username(name: str) -> str:
    return memory.name_to_username(name)

def build_activity_url(username: str) -> str:
    return memory.build_activity_url(username)

def build_profile_url(username: str) -> str:
    return memory.build_profile_url(username)

# Compatibility wrappers delegating to memory.py

def load_profiles() -> list[dict]:
    """Load profile list from SQLite. Returns list of profile dicts with backward-compatibility keys."""
    persons = memory.get_all_persons(active_only=True)
    compat_list = []
    for p in persons:
        compat_p = dict(p)
        compat_p["name"] = p.get("display_name", "")
        compat_p["posts_collected"] = p.get("total_posts_seen", 0)
        compat_list.append(compat_p)
    return compat_list

def save_profiles(profiles: list[dict]) -> None:
    """No-op as memory.py handles DB persistence directly."""
    pass

def get_profile_by_name(name: str) -> dict | None:
    """Find a profile by display name or username (case-insensitive)."""
    # Check all active persons
    profiles = load_profiles()
    name_lower = name.lower().strip()
    for p in profiles:
        if p["name"].lower().strip() == name_lower or p["username"].lower().strip() == name_lower:
            return p
    return None

def add_profile(name: str, username: str = None, note: str = "") -> dict:
    """Add a profile to the tracking list via memory.py."""
    p = memory.add_person(name, username, note)
    compat_p = dict(p)
    compat_p["name"] = p.get("display_name", "")
    compat_p["posts_collected"] = p.get("total_posts_seen", 0)
    return compat_p

def remove_profile(name: str) -> bool:
    """Remove a profile from the tracking list via memory.py."""
    return memory.remove_person(name)

def update_last_checked(username: str, posts_collected: int) -> None:
    """Update metadata (kept for backward compatibility)."""
    with memory.get_db() as conn:
        conn.execute("""
            UPDATE persons
            SET last_checked = ?, total_posts_seen = total_posts_seen + ?
            WHERE username = ?
        """, (datetime.now().isoformat(), posts_collected, username))

def list_profiles() -> list[dict]:
    """Return all active tracked profiles."""
    return load_profiles()

def print_profiles_table(profiles: list[dict]) -> None:
    if not profiles:
        print("\n  No profiles tracked yet.")
        print('  Add one: python profiles.py add "Andrew Ng"')
        return

    print(f"\n  --------------------------------------------------------------")
    print(f"  Tracked Profiles ({len(profiles)})")
    print(f"  --------------------------------------------------------------")
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

    p_add = sub.add_parser("add", help="Add a profile to track")
    p_add.add_argument("name", help='Display name, e.g. "Andrew Ng"')
    p_add.add_argument("--username", help="LinkedIn username if auto-derive is wrong")
    p_add.add_argument("--note", default="", help="Why you're tracking this person")

    p_rm = sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("name", help="Display name to remove")

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

if __name__ == "__main__":
    main()
