import os
import re
import time
import requests
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from scrapers import CalendarEvent


def _get_new_access_token() -> str | None:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type":    "refresh_token",
        "client_id":     os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
        "refresh_token": os.environ["REFRESH_TOKEN"],
    })
    return r.json().get("access_token") if r.ok else None


def get_service():
    token = _get_new_access_token()
    if not token:
        raise RuntimeError("Could not obtain Google OAuth access token.")
    return build("calendar", "v3", credentials=Credentials(token))


def _uid_from_gcal_event(event: dict) -> str | None:
    desc = event.get("description") or ""
    m = re.search(r"<!-- uid:(.+?) -->", desc)
    if m:
        return m.group(1)
    # Legacy lab111 format (backward compat for events created before refactor)
    m = re.search(r"<a href=([^\s>]+)>Ticket</a>", desc)
    return m.group(1) if m else None


def _to_gcal_body(event: CalendarEvent) -> dict:
    desc = (event.description or "") + f"\n<!-- uid:{event.uid} -->"
    return {
        "summary":     event.title,
        "location":    event.location,
        "description": desc.strip(),
        "start": {"dateTime": event.start.isoformat(), "timeZone": event.timezone},
        "end":   {"dateTime": event.end.isoformat(),   "timeZone": event.timezone},
    }


def _get_or_create_calendar(service, name: str) -> str:
    page_token = None
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for cal in resp["items"]:
            if cal["summary"] == name:
                return cal["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    created = service.calendars().insert(body={"summary": name}).execute()
    print(f"Created new calendar: {name}")
    return created["id"]


def sync(
    events: list[CalendarEvent],
    calendar_name: str,
    service,
    window_start: datetime,
    window_end: datetime,
    min_events: int = 0,
) -> tuple[int, int]:
    """
    Sync events to Google Calendar within [window_start, window_end].
    Adds new events, removes events no longer in the scraped list.
    Returns (added, removed).

    min_events: if the scraped list is smaller than this, abort rather than
    delete existing events (guards against a silent scraper failure wiping
    the calendar clean).
    """
    cal_id = _get_or_create_calendar(service, calendar_name)

    existing: dict[str, str] = {}  # uid -> event_id
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=cal_id,
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            pageToken=page_token,
        ).execute()
        for ev in resp.get("items", []):
            uid = _uid_from_gcal_event(ev)
            if uid:
                existing[uid] = ev["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"Found {len(existing)} existing events in '{calendar_name}' calendar.")

    if min_events and len(events) < min_events:
        raise RuntimeError(
            f"Scraped only {len(events)} events for '{calendar_name}' "
            f"(minimum {min_events}); aborting to protect existing calendar."
        )

    scraped_uids = {ev.uid for ev in events}

    removed = 0
    for uid in set(existing) - scraped_uids:
        service.events().delete(calendarId=cal_id, eventId=existing[uid]).execute()
        time.sleep(0.3)
        removed += 1

    added = 0
    for event in events:
        if event.uid not in existing:
            service.events().insert(calendarId=cal_id, body=_to_gcal_body(event)).execute()
            time.sleep(0.3)
            added += 1

    return added, removed
