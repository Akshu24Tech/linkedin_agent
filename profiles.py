"""
profiles.py
───────────
Manage LinkedIn profiles to track.
Thin CLI wrapper over memory.py — all data lives in session/memory.db.

BEST PRACTICE: Always add people using their LinkedIn URL.
LinkedIn vanity names are globally unique — URL is the only guaranteed
way to add exactly the right person, no collisions.

Usage:
  python profiles.py add --url "https://www.linkedin.com/in/yatinbhalla42?utm_source=share..."
  python profiles.py add --url "https://linkedin.com/in/karpathy/" --note "LLM researcher"
  python profiles.py list
  python profiles.py remove "Andrej Karpathy"

If you only have a name (no URL):
  python profiles.py add "Harrison Chase"
  → Auto-derives username, warns you to verify manually.
"""

import argparse
from memory import (
    add_person, remove_person, get_all_persons,
    print_persons_table, init_db,
)


def load_profiles() -> list[dict]:
    """Backwards compat — returns active persons from memory.db"""
    persons = get_all_persons(active_only=True)
    for p in persons:
        if "display_name" in p and "name" not in p:
            p["name"] = p["display_name"]
    return persons


def main():
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    init_db()
    parser = argparse.ArgumentParser(
        description="Manage LinkedIn profiles to track",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (recommended — use URL):
  python profiles.py add --url "https://www.linkedin.com/in/yatinbhalla42?utm_source=share_via..."
  python profiles.py add --url "https://linkedin.com/in/karpathy/" --note "LLM researcher"
  python profiles.py add --url "https://www.linkedin.com/in/harrison-chase-961287118/"

Examples (name only — less reliable for common names):
  python profiles.py add "Harrison Chase"
  python profiles.py add "Andrej Karpathy" --username karpathy

Other:
  python profiles.py list
  python profiles.py remove "Harrison Chase"
        """
    )
    sub = parser.add_subparsers(dest="cmd")

    # add — supports both --url (recommended) and name-only
    p_add = sub.add_parser("add", help="Add a profile to track")
    p_add.add_argument("name", nargs="?", default="", help='Display name (optional if --url given)')
    p_add.add_argument("--url",      help="LinkedIn profile URL (recommended — handles UTM params)")
    p_add.add_argument("--username", help="LinkedIn username if auto-derive is wrong")
    p_add.add_argument("--note", default="", help="Why you are tracking this person")

    # remove
    p_rm = sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("name", help="Display name or username to remove")

    # list
    sub.add_parser("list", help="List all tracked profiles with stats")

    args = parser.parse_args()

    if args.cmd == "add":
        # If only URL given, derive display name from vanity name
        name = args.name
        if not name and args.url:
            from memory import username_from_url
            vanity = username_from_url(args.url)
            name = vanity or "Unknown"
            print(f"[i] No name given — using vanity name as display name: '{name}'")
            print(f"    You can re-add with a proper name: profiles.py add \"Real Name\" --url ...")

        if not name:
            print("[!] Provide a name or --url")
            p_add.print_help()
            return

        add_person(name, username=args.username, url=args.url, note=args.note)

    elif args.cmd == "remove":
        remove_person(args.name)

    elif args.cmd == "list":
        print_persons_table(get_all_persons())

    else:
        parser.print_help()
        print("\n  Quick start (recommended):")
        print('  python profiles.py add --url "https://linkedin.com/in/karpathy/"')
        print('  python profiles.py add --url "https://linkedin.com/in/harrison-chase-961287118/" --note "LangChain founder"')
        print("  python profiles.py list")


if __name__ == "__main__":
    main()