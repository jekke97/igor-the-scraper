"""Lab111 cinema programme scraper."""

import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

from scrapers import CalendarEvent

_URL          = "https://www.lab111.nl/programma/listview/"
_TZ           = "Europe/Amsterdam"
_AMS          = ZoneInfo(_TZ)
_DEFAULT_DUR  = timedelta(hours=2)
_HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; igor-scraper/1.0)"}


def _fetch_duration(session: requests.Session, movie_url: str) -> timedelta:
    """Fetch runtime from the movie page; return 2-hour default on any failure."""
    for attempt in range(2):
        try:
            page = BeautifulSoup(
                session.get(movie_url, timeout=15, headers=_HEADERS).content, "lxml"
            )
            els = page.find_all("ul", class_="speelduur")
            if not els:
                return _DEFAULT_DUR
            nums = re.findall(r"\d+", els[0].text)
            if len(nums) >= 2:
                return timedelta(hours=int(nums[0]), minutes=int(nums[1]))
            if len(nums) == 1:
                return timedelta(minutes=int(nums[0]))
            return _DEFAULT_DUR
        except Exception:
            if attempt == 0:
                continue
            return _DEFAULT_DUR
    return _DEFAULT_DUR


def _make_session(use_cloudscraper: bool) -> requests.Session:
    if use_cloudscraper:
        import cloudscraper
        return cloudscraper.create_scraper()
    return requests.Session()


def scrape(forecast_days: int = 14, use_cloudscraper: bool = False) -> list[CalendarEvent]:
    today   = datetime.now()
    session = _make_session(use_cloudscraper)
    soup    = BeautifulSoup(
        session.get(_URL, timeout=15, headers=_HEADERS).content, "lxml"
    )
    events: list[CalendarEvent] = []
    skipped = 0

    for day in range(forecast_days):
        for row in soup.find_all("tr", class_=f"day{day}")[1:]:
            try:
                urls = re.findall(r'https?://[^\s]+"', str(row))
                if len(urls) < 2:
                    raise ValueError(f"expected 2 URLs, found {len(urls)}")

                movie_url  = urls[1][:-1]
                ticket_url = str(row.find("a", class_="button tic")).split('"')[3]
                name       = row.find_all("a")[1].text
                s_time     = row.find_all("a")[0].text
                lab        = row.find("span").text
                info_url   = row.find_all("a")[1]["href"]

                duration = _fetch_duration(session, movie_url)
                sh, sm   = [int(x) for x in s_time.split(":")]
                start    = (datetime(today.year, today.month, today.day, sh, sm)
                            + timedelta(days=day)).replace(tzinfo=_AMS)
                end      = start + duration

                events.append(CalendarEvent(
                    title       = name,
                    start       = start,
                    end         = end,
                    location    = lab,
                    description = f'<a href="{ticket_url}">Ticket</a>\n<a href="{info_url}">Description</a>',
                    uid         = ticket_url,
                    timezone    = _TZ,
                ))
            except Exception as e:
                print(f"  Skipping entry on day {day}: {e}")
                skipped += 1

        print(f"Day {day + 1} scraped.")

    print(f"Scraped {len(events)} events ({skipped} skipped).")
    return events
