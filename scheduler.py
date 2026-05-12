"""
scheduler.py
────────────
Runs the LinkedIn Feed Agent automatically on a schedule.

Two modes:
  1. Loop mode (recommended): keeps running, triggers at set time daily
  2. Cron/Task Scheduler: just run agent.py directly (see instructions below)

Run:
  python scheduler.py                     # Runs daily at 9:00 AM
  python scheduler.py --time 08:30        # Custom time (HH:MM)
  python scheduler.py --now               # Run immediately, then schedule
  python scheduler.py --interval 6        # Run every 6 hours
  python scheduler.py --print-cron        # Print cron / Task Scheduler command

Keep it running in background:
  # Windows: run in a minimized terminal, or use Task Scheduler (--print-cron)
  # Linux/Mac: nohup python scheduler.py &
  # Or: use tmux / screen
"""

import asyncio
import argparse
import subprocess
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(description="LinkedIn Agent Scheduler")
    parser.add_argument("--time", default="09:00", help="Daily run time HH:MM (default: 09:00)")
    parser.add_argument("--now", action="store_true", help="Run immediately then schedule")
    parser.add_argument("--interval", type=int, default=None, help="Run every N hours instead of daily")
    parser.add_argument("--print-cron", action="store_true", help="Print cron / Task Scheduler setup commands")
    return parser.parse_args()


def get_python_path() -> str:
    return sys.executable


def get_agent_path() -> str:
    return str(Path(__file__).parent / "agent.py")


def print_cron_setup():
    py = get_python_path()
    agent = get_agent_path()

    print("\n" + "═"*60)
    print("  OS-level scheduling (alternative to running scheduler.py)")
    print("═"*60)

    print("\n  ── Linux / Mac (crontab) ─────────────────────────────────")
    print("  Run: crontab -e")
    print("  Add this line for 9 AM daily:")
    print(f'  0 9 * * * cd {Path(agent).parent} && {py} agent.py >> session/cron.log 2>&1')

    print("\n  ── Windows Task Scheduler ───────────────────────────────")
    print("  1. Open Task Scheduler → Create Basic Task")
    print("  2. Trigger: Daily at 9:00 AM")
    print(f"  3. Action: Start a Program")
    print(f"     Program: {py}")
    print(f"     Arguments: {agent}")
    print(f"     Start in: {Path(agent).parent}")

    print("\n  ── Or just use scheduler.py ─────────────────────────────")
    print("  python scheduler.py --time 09:00")
    print("  (keep terminal open / use tmux or nohup)")
    print()


def seconds_until(target_time: str) -> float:
    """Seconds until next occurrence of HH:MM."""
    now = datetime.now()
    h, m = map(int, target_time.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_agent():
    """Trigger a full agent run as a subprocess."""
    py = get_python_path()
    agent = get_agent_path()

    print(f"\n{'─'*55}")
    print(f"  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting agent run...")
    print(f"{'─'*55}")

    result = subprocess.run(
        [py, agent],
        cwd=str(Path(agent).parent),
    )

    if result.returncode == 0:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Run complete ✓")
    else:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Run finished with errors (code {result.returncode})")
        print(f"  Check session/agent.log for details")


async def schedule_loop(run_time: str = "09:00", interval_hours: int = None, run_now: bool = False):
    """Main scheduling loop."""

    if interval_hours:
        interval_secs = interval_hours * 3600
        print(f"\n  Scheduler started — running every {interval_hours}h")
    else:
        print(f"\n  Scheduler started — running daily at {run_time}")

    print(f"  Press Ctrl+C to stop\n")

    if run_now:
        run_agent()

    while True:
        if interval_hours:
            wait = interval_hours * 3600
            next_run = datetime.now() + timedelta(hours=interval_hours)
        else:
            wait = seconds_until(run_time)
            next_run = datetime.now() + timedelta(seconds=wait)

        print(f"  Next run: {next_run.strftime('%Y-%m-%d %H:%M')} "
              f"(in {wait/3600:.1f}h)")

        await asyncio.sleep(wait)
        run_agent()


def main():
    args = parse_args()

    if args.print_cron:
        print_cron_setup()
        return

    print("\n" + "═"*55)
    print("  LinkedIn Feed Agent Scheduler")
    print("═"*55)

    try:
        asyncio.run(schedule_loop(
            run_time=args.time,
            interval_hours=args.interval,
            run_now=args.now,
        ))
    except KeyboardInterrupt:
        print("\n\n  Scheduler stopped.")


if __name__ == "__main__":
    main()
