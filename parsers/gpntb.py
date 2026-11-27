"""Афиша ГПНТБ СО РАН (afisha.gpntbsib.ru) — через API самого сайта.

Сайт — приложение на React: в HTML только пустой каркас, события приходят
из `/api/events/list?startDate=…&endDate=…&isShow=true&limit=0`. Поле
`startDate` — время в миллисекундах, `location` — этаж, а не адрес.

Адрес площадки один: ул. Восход, 15. Цену сайт не отдаёт.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import bs4

from common import http, text

BASE = "https://afisha.gpntbsib.ru"
LIST = f"{BASE}/api/events/list"
WINDOW_DAYS = 45

VENUE = "ГПНТБ СО РАН"
ADDRESS = "ул. Восход, 15"
NSK = timezone(timedelta(hours=7))

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def _moment(ms: int | float | None) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=NSK)
    except (ValueError, OSError, OverflowError):
        return None


def _plain(html_text: str) -> str:
    if not html_text:
        return ""
    soup = bs4.BeautifulSoup(html_text, "html.parser")
    return text.clean(soup.get_text(" ", strip=True))


def _date_raw(start: datetime, end: datetime | None) -> str:
    """«5 декабря 2026, 18:00» — в том виде, который разбирает common.dates."""
    raw = f"{start.day} {MONTHS[start.month - 1]} {start.year}, {start:%H:%M}"
    if end and end.date() != start.date():
        raw += f" — до {end.day} {MONTHS[end.month - 1]}"
    return raw


def fetch() -> list[dict]:
    today = date.today()
    params = {
        "startDate": today.isoformat(),
        "endDate": (today + timedelta(days=WINDOW_DAYS)).isoformat(),
        "isShow": "true",
        "limit": "0",
    }
    url = f"{LIST}?{urlencode(params)}"
    payload = http.get_json(url, ttl=1800)
    if not payload:
        print("  [!] API афиши не ответил или отдал пустой список")
        return []
    items = payload if isinstance(payload, list) else payload.get("data") or []
    print(f"  событий в API: {len(items)}")

    out: list[dict] = []
    for item in items:
        title = text.clean_title(str(item.get("title") or ""))
        if not title:
            continue
        start = _moment(item.get("startDate"))
        if start is None:
            continue
        end = _moment(item.get("endDate"))

        ident = str(item.get("_id") or "").strip()
        link = str(item.get("link") or "").strip()
        source_url = link or (f"{BASE}/events/{ident}" if ident else BASE)

        image = str(item.get("image") or "").strip()
        if image and not image.startswith("http"):
            image = BASE + image

        description = _plain(str(item.get("description") or ""))
        if len(description) < 40:
            description = _plain(str(item.get("shortDescription") or ""))
        location = text.clean(str(item.get("location") or ""))
        if location and location.lower() not in description.lower():
            description = f"{location}. {description}".strip(". ")

        out.append({
            "source": "ГПНТБ",
            "title": title,
            "date_raw": _date_raw(start, end),
            "source_url": source_url,
            "image_url": image,
            "time_text": f"{start:%H:%M}",
            "venue": VENUE,
            "address": ADDRESS,
            "district": text.guess_district(ADDRESS, strict=True),
            "text_raw": description or title,
            "age_text": str(item.get("ageLimit") or ""),
            "price_text": "",
        })

    unique: list[dict] = []
    seen: set[str] = set()
    for ev in unique_targets(out):
        if ev["source_url"] in seen:
            continue
        seen.add(ev["source_url"])
        unique.append(ev)
    print(f"  к выходным: {len(unique)}")
    return unique


def unique_targets(events: list[dict]) -> list[dict]:
    """Оставляет только то, что пересекается с ближайшими выходными."""
    from common import dates

    keep = []
    for ev in events:
        if dates.is_weekend_relevant(ev["date_raw"]):
            keep.append(ev)
    return keep
