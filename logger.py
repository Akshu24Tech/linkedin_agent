"""
logger.py
─────────
Structured logging for the LinkedIn Feed Agent.
Logs to both console (colored) and session/agent.log (file).

Every module imports this instead of using print().
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


# ── Setup ─────────────────────────────────────────────────────────────────────

LOG_FILE = Path("session/agent.log")


class ColorFormatter(logging.Formatter):
    """Colored console output."""
    COLORS = {
        logging.DEBUG:    "\033[90m",   # dark gray
        logging.INFO:     "\033[0m",    # default
        logging.WARNING:  "\033[33m",   # yellow
        logging.ERROR:    "\033[31m",   # red
        logging.CRITICAL: "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        msg = super().format(record)
        return f"{color}{msg}{self.RESET}"


def setup_logger(name: str = "linkedin_agent", level: int = logging.INFO) -> logging.Logger:
    """
    Returns a configured logger.
    Call once per module: logger = setup_logger(__name__)
    """
    Path("session").mkdir(exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(level)

    # Console handler (colored)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ColorFormatter(
        fmt="%(message)s"
    ))
    logger.addHandler(console)

    # File handler (plain, timestamped)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(file_handler)

    return logger


# Root logger for the project
log = setup_logger("agent")
