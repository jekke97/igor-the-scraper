"""MTGO Pauper schedule scraper — source: mtgoupdate.com"""

import re
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from scrapers import CalendarEvent

_SITE = "https://mtgoupdate.com"
_PT   = ZoneInfo("America/Los_Angeles")


# ── fetch ─────────────────────────────────────────────────────────────────────

def _fetch_js(name: str) -> str:
    r = requests.get(f"{_SITE}/{name}", timeout=10)
    r.raise_for_status()
    return r.text


# ── parse base schedule ───────────────────────────────────────────────────────

def _expand_vars(js: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r'^\s*const (\w+) = "([^"]+)";', js, re.M)}


def _parse_array(text: str, vars: dict) -> list:
    items = []
    for token in re.findall(r'`([^`]*)`|null', text):
        if token:
            for k, v in vars.items():
                token = token.replace(f'${{{k}}}', v)
            items.append(token)
        else:
            items.append(None)
    return items


def _parse_base_schedule(js: str) -> list:
    """168-element array indexed as [day_of_week * 24 + hour]. 0=Sunday. Times are PT."""
    vars = _expand_vars(js)
    base = []
    for day in ("sun", "mon", "tues", "wed", "thur", "fri", "sat"):
        m = re.search(rf"const {day} = \[(.*?)\];", js, re.DOTALL)
        base.extend(_parse_array(m.group(1), vars) if m else [None] * 24)
    return base


# ── parse special events ──────────────────────────────────────────────────────

def _infer_year(today: date, month_1: int) -> int:
    if month_1 - today.month > 5:
        return today.year - 1
    if today.month - month_1 > 5:
        return today.year + 1
    return today.year


def _parse_special(js: str, func: str) -> dict:
    """Parse getRCQData() or getShowcaseData() → {date: {hour_int: event_str}}."""
    m = re.search(
        rf"function {func}\(\).*?return \[(.*?)\]\.map\(supplyYearAndDecrementMonth\)",
        js, re.DOTALL,
    )
    if not m:
        return {}
    today = date.today()
    result: dict[date, dict[int, str]] = {}
    for row in re.finditer(r"\[\s*(\d+),\s*(\d+),\s*\{([^}]+)\}\s*\]", m.group(1)):
        month_1, day_n = int(row.group(1)), int(row.group(2))
        d = date(_infer_year(today, month_1), month_1, day_n)
        result[d] = {
            int(e.group(1)): e.group(2)
            for e in re.finditer(r"(\d+):\s*\"([^\"]+)\"", row.group(3))
        }
    return result


def _dst_events(today: date) -> dict:
    """Return {date: {2: "DST"}} for US spring/fall DST Sundays (replicates addCurrentYearDSTDates)."""
    year   = today.year
    march1 = date(year, 3, 1)
    nov1   = date(year, 11, 1)
    return {
        date(year, 3,  1 + (6 - march1.weekday()) % 7 + 7): {2: "DST"},
        date(year, 11, 1 + (6 - nov1.weekday()) % 7):        {2: "DST"},
    }


# ── monster schedule assembly ─────────────────────────────────────────────────

def _insert_event(normal: str, special: str, replace: bool) -> str:
    delim = "plus&"
    if delim not in normal and delim not in special:
        return special if replace else normal + "&" + special
    if (delim in normal) != (delim in special):
        return (special + "&" + normal) if (delim in normal) else (normal + "&" + special)
    if replace:
        return normal.split(delim)[0] + "&" + special
    return normal.split("&minus")[0] + "&" + special.split(delim)[1]


def _insert_into_monster(monster: list, today: date, events: dict, is_replacement: bool, span: int) -> None:
    for i in range(span):
        d = today + timedelta(days=i - 1)
        day_events = events.get(d)
        if not day_events:
            continue
        for hour in range(24):
            if hour not in day_events:
                continue
            event = day_events[hour]
            idx   = 24 * i + hour
            if idx >= len(monster):
                continue
            current = monster[idx]
            additive = (not is_replacement) or (event is not None and "Pauper Showcase" in event)
            if not current:
                monster[idx] = event
            else:
                monster[idx] = _insert_event(current, event or "", not additive)


def _build_monster(base: list, rcq: dict, showcase: dict, today: date, days: int) -> list:
    """Build schedule array covering yesterday + `days` days ahead."""
    span     = days + 2                  # yesterday + days + 1 buffer
    repeated = base * (span // 7 + 3)   # enough weeks to cover the window
    js_day   = today.isoweekday() % 7   # 0=Sun … 6=Sat
    start    = 24 * (6 + js_day)
    monster  = repeated[start: start + span * 24 + 1]

    all_rcq: dict[date, dict] = {d: dict(ev) for d, ev in rcq.items()}
    for d, ev in _dst_events(today).items():
        if d in all_rcq:
            all_rcq[d][2] = ("DST&" + all_rcq[d][2]) if 2 in all_rcq[d] else "DST"
        else:
            all_rcq[d] = dict(ev)

    _insert_into_monster(monster, today, showcase, is_replacement=True,  span=span)
    _insert_into_monster(monster, today, all_rcq,  is_replacement=False, span=span)
    return monster


# ── public API ────────────────────────────────────────────────────────────────

def _simplify_name(part: str) -> str:
    name = re.sub(r"^Pauper ", "", part)
    name = re.sub(r" (\d+) Players$", r" (\1 players)", name)
    name = re.sub(r"\((\d+)-player\)", r"(\1 players)", name)
    return name


def scrape(days: int = 14, tz: str = "Europe/Rome") -> list[CalendarEvent]:
    """Fetch Pauper events for the next `days` days and return as CalendarEvent list."""
    js      = _fetch_js("scheduleData.js")
    base    = _parse_base_schedule(js)
    rcq     = _parse_special(js, "getRCQData")
    showcase = _parse_special(js, "getShowcaseData")

    today   = date.today()
    monster = _build_monster(base, rcq, showcase, today, days)

    local_tz    = ZoneInfo(tz)
    yesterday   = today - timedelta(days=1)
    ts          = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, tzinfo=_PT).timestamp()
    week_start  = datetime(today.year, today.month, today.day, tzinfo=local_tz)
    week_end    = week_start + timedelta(days=days)

    seen: set = set()
    events: list[CalendarEvent] = []

    for k, slot in enumerate(monster):
        if k > 0:
            ts += 3600
        if not slot:
            continue
        for part in slot.split("&"):
            if part == "DST":
                ts += -3600 if today.month <= 6 else 3600
            elif part == "minus":
                ts -= 1800
            elif part == "plus":
                ts += 1800
            elif "Pauper" in part and "Prelim" not in part:
                dt  = datetime.fromtimestamp(ts, tz=local_tz)
                key = (round(ts), part)
                if week_start <= dt < week_end and key not in seen:
                    seen.add(key)
                    uid = f"mtgo:{round(ts)}:{part}"
                    events.append(CalendarEvent(
                        title       = _simplify_name(part),
                        start       = dt,
                        end         = dt + timedelta(hours=6),
                        location    = "MTGO",
                        description = '<a href="https://mtgoupdate.com">mtgoupdate.com</a>',
                        uid         = uid,
                        timezone    = tz,
                    ))

    events.sort(key=lambda e: e.start)
    return events


def format_week(events: list[CalendarEvent], tz_label: str = "IT") -> str:
    """Format a list of MTGO CalendarEvents as a Telegram Markdown recap."""
    if not events:
        return "No Pauper events this week."
    lines = ["*MTGO Pauper — This Week*"]
    current_day = None
    for ev in events:
        day = ev.start.strftime("%A %d %B")
        if day != current_day:
            lines.append(f"\n*{day}*")
            current_day = day
        name = ev.title
        lines.append(f"  {ev.start.strftime('%H:%M')} {tz_label} — {name}")
    return "\n".join(lines)
