"""Lab111 cinema → Google Calendar sync."""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import igor
igor.load_env()

from scrapers.lab111 import scrape

DEFAULT_FORECAST   = 14
USE_CLOUDSCRAPER   = True   # set False to revert to plain requests.Session


def main() -> None:
    if sys.stdin.isatty():
        raw      = input(f"Days to forecast [default {DEFAULT_FORECAST}]: ").strip()
        forecast = int(raw) if raw.isdigit() else DEFAULT_FORECAST
    else:
        forecast = DEFAULT_FORECAST
    print(f"Forecasting {forecast} days (cloudscraper={'on' if USE_CLOUDSCRAPER else 'off'}).")

    events    = scrape(forecast, use_cloudscraper=USE_CLOUDSCRAPER)
    ams       = ZoneInfo("Europe/Amsterdam")
    win_start = datetime.now(ams).replace(hour=0, minute=0, second=0, microsecond=0)
    win_end   = win_start + timedelta(days=forecast)

    igor.run(
        events, "lab111", win_start, win_end,
        message=lambda a, r: f"*Lab111* — {a} added, {r} removed. What hump?\n— Eye-gor",
        min_events=10,
    )


if __name__ == "__main__":
    with igor.notify_on_error("lab111"):
        main()
