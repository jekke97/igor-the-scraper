"""Igor the Scraper — generic orchestrator."""

import os
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from gcalendar import get_service, sync
from telegram import send as _send
from scrapers import CalendarEvent


def load_env(path: str | None = None) -> None:
    """Load a .env file into os.environ without overriding existing vars."""
    env_file = Path(path) if path else Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def run(
    events: list[CalendarEvent],
    calendar_name: str,
    window_start: datetime,
    window_end: datetime,
    message: str | Callable[[int, int], str] | None = None,
    notify: bool = True,
    min_events: int = 0,
) -> tuple[int, int]:
    """
    Sync events to Google Calendar, then optionally send a Telegram notification.

    message:    plain string, a callable (added, removed) -> str, or None for a default summary.
    notify:     set to False to skip Telegram entirely.
    min_events: abort (and raise) if fewer than this many events were scraped.

    Returns (added, removed).
    """
    service = get_service()
    added, removed = sync(events, calendar_name, service, window_start, window_end,
                          min_events=min_events)

    if notify:
        token   = os.environ.get("TELEGRAM_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if callable(message):
            text = message(added, removed)
        elif message is not None:
            text = message
        else:
            text = f"*{calendar_name}* — sync complete: {added} added, {removed} removed."
        _send(token, chat_id, text)

    return added, removed


@contextmanager
def notify_on_error(label: str = "Igor"):
    """
    Context manager: if an exception escapes, sends a Telegram error report and re-raises.

    Usage:
        with igor.notify_on_error("my-scraper"):
            main()
    """
    try:
        yield
    except Exception:
        tb       = traceback.format_exc()[-1500:]
        token    = os.environ.get("TELEGRAM_TOKEN", "")
        chat_id  = os.environ.get("TELEGRAM_CHAT_ID", "")
        run_time = datetime.now().strftime("%Y-%m-%d, %H:%M")
        _send(token, chat_id, f"*{label}* failed at {run_time}:\n\n```\n{tb}\n```")
        raise
