"""Tests for fragile points in scrapers/lab111.py."""

import re
import requests
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from scrapers.lab111 import scrape


# ── minimal HTML fixtures ─────────────────────────────────────────────────────

_PROGRAMME_HTML = """
<html><body><table>
  <tr class="day0"><th>Header row — skipped by scraper</th></tr>
  <tr class="day0">
    <td><a href="https://www.lab111.nl/screening/20250112-200/">20:00</a></td>
    <td><a href="https://www.lab111.nl/films/test-film/">Test Film</a></td>
    <td><span>Lab 1</span></td>
    <td><a class="button tic" href="https://tickets.lab111.nl/event/999/">Tickets</a></td>
  </tr>
</table></body></html>
"""

_MOVIE_DETAIL_HTML = """
<html><body>
  <ul class="speelduur">1 uur 45 min</ul>
</body></html>
"""


def _resp(html: str) -> MagicMock:
    r = MagicMock()
    r.content = html.encode()
    r.text = html
    r.status_code = 200
    return r


def _mock_session(*responses):
    """Return a mock Session whose .get() yields responses in order (or raises if Exception)."""
    session = MagicMock()
    effects = []
    for r in responses:
        effects.append(r)
    session.get.side_effect = effects
    return session


# ── tests ─────────────────────────────────────────────────────────────────────

def test_events_are_timezone_aware():
    """All returned start/end datetimes must carry tzinfo (fragile: datetime.now() base is naive)."""
    with patch("scrapers.lab111.requests.Session",
               return_value=_mock_session(_resp(_PROGRAMME_HTML), _resp(_MOVIE_DETAIL_HTML))):
        events = scrape(forecast_days=1)

    assert events, "expected at least one event from valid fixture HTML"
    for ev in events:
        assert ev.start.tzinfo is not None, f"start of '{ev.title}' has no tzinfo"
        assert ev.end.tzinfo is not None,   f"end of '{ev.title}' has no tzinfo"


def test_description_hrefs_are_quoted():
    """href attributes in the description must be quoted (bug: was href=url instead of href=\"url\")."""
    with patch("scrapers.lab111.requests.Session",
               return_value=_mock_session(_resp(_PROGRAMME_HTML), _resp(_MOVIE_DETAIL_HTML))):
        events = scrape(forecast_days=1)

    assert events
    for ev in events:
        bare = re.findall(r'href=[^"\s>]', ev.description)
        assert not bare, f"unquoted href in description: {ev.description!r}"


def test_row_with_no_urls_is_skipped():
    """A row containing no URLs raises ValueError internally and is skipped; scrape() must not crash."""
    no_url_html = """
    <html><body><table>
      <tr class="day0"><th>Header</th></tr>
      <tr class="day0"><td>20:00</td><td>Some Film</td><td><span>Lab 1</span></td></tr>
    </table></body></html>
    """
    mock_session = MagicMock()
    mock_session.get.return_value = _resp(no_url_html)
    with patch("scrapers.lab111.requests.Session", return_value=mock_session):
        events = scrape(forecast_days=1)

    assert events == [], "row with no URLs should be skipped, not raise"


def test_row_with_broken_dom_is_skipped():
    """A row whose DOM structure doesn't match expectations is skipped; scrape() must not crash.

    Covers: positional find_all("a")[n] and str(row.find(...)).split('"')[3] failures.
    """
    broken_html = """
    <html><body><table>
      <tr class="day0"><th>Header</th></tr>
      <tr class="day0">
        <td data-a="https://www.lab111.nl/screening/1/"
            data-b="https://www.lab111.nl/films/x/">no real anchor elements here</td>
      </tr>
    </table></body></html>
    """
    mock_session = MagicMock()
    mock_session.get.return_value = _resp(broken_html)
    with patch("scrapers.lab111.requests.Session", return_value=mock_session):
        events = scrape(forecast_days=1)

    assert events == [], "row with broken DOM should be skipped, not raise"


def test_blocked_first_attempt_retries_and_recovers():
    """If the first fetch comes back without day0 rows (e.g. a Cloudflare block),
    scrape() must retry with a fresh session instead of giving up immediately."""
    blocked = _resp("<html><body>Just a moment...</body></html>")
    with patch("scrapers.lab111.requests.Session",
               side_effect=[
                   _mock_session(blocked),
                   _mock_session(_resp(_PROGRAMME_HTML), _resp(_MOVIE_DETAIL_HTML)),
               ]):
        events = scrape(forecast_days=1)

    assert len(events) == 1, "should recover once a retry returns real content"


def test_movie_page_fetch_failure_uses_default_duration():
    """A network error on the per-movie detail fetch must NOT drop the event.

    The scraper falls back to a 2-hour default duration so a flaky movie page
    never wipes the calendar clean.
    """
    conn_err = requests.exceptions.ConnectionError("timeout")
    with patch("scrapers.lab111.requests.Session",
               return_value=_mock_session(_resp(_PROGRAMME_HTML), conn_err, conn_err)):
        events = scrape(forecast_days=1)

    assert len(events) == 1, "event should be kept even when detail page is unreachable"
    assert events[0].end - events[0].start == timedelta(hours=2), \
        "expected 2-hour fallback duration"
