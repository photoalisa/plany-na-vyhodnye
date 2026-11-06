"""Дом учёных СО РАН (dusoran.ru) — только выставки.

Концерты с этого источника не нужны, поэтому берём не общую ленту /events/,
а готовую выборку «Выставки»: https://dusoran.ru/events/event-2/

Особенность разметки: у выставки указана только дата окончания («по 8 Ноября»),
поэтому считаем, что она идёт с сегодняшнего дня до этой даты.
"""

from __future__ import annotations

import re
from datetime import date

import bs4

from common import dates, http, text

BASE = "https://dusoran.ru"
LISTING = f"{BASE}/events/event-2/"

# На всякий случай: если в выборке появится концерт — не берём.
_EXCLUDE = re.compile(r"(концерт|филармони|оркестр|симфони)", re.IGNORECASE)
_RE_UNTIL = re.compile(r"\bпо\s+(\d{1,2}\s+\S+)", re.IGNORECASE)


def _detail_text(url: str) -> tuple[str, str]:
    """(описание, возрастная метка) со страницы выставки.

    Возраст лежит отдельным блоком в шапке: «<div class="srh-jumbotron-age-rating">0+</div>».
    """
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    age = ""
    age_node = soup.select_one(".srh-jumbotron-age-rating")
    if age_node:
        value = text.clean(age_node.get_text(" ", strip=True))
        if re.fullmatch(r"\d{1,2}\+", value):
            age = value

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()
    main = soup.select_one("main") or soup.body or soup
    for p in main.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) >= 60 and not text.is_boilerplate(value):
            return value, age
    return "", age


def fetch() -> list[dict]:
    html = http.get_text(LISTING, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select(".srh-card-event")
    print(f"  карточек: {len(cards)}")

    today = date.today()
    events: list[dict] = []
    seen: set[str] = set()

    for card in cards:
        date_el = card.select_one(".srh-card-event-date")
        link = card.select_one(".srh-card-event-footer a[href]") or card.find("a", href=True)
        if not date_el or not link:
            continue

        title = text.clean(link.get_text(" ", strip=True))
        if not title or _EXCLUDE.search(title):
            continue

        raw_date = text.clean(date_el.get_text(" ", strip=True))
        # на карточке уже может стоять «по 8 Ноября» — не дублируем предлог
        shown_date = raw_date if re.match(r"^по\b", raw_date, re.IGNORECASE) else f"по {raw_date}"
        parsed = dates.parse_period(raw_date)
        end = parsed.end or parsed.start
        if not end or end < today:
            continue

        href = (link.get("href") or "").strip()
        source_url = href if href.startswith("http") else BASE + href
        if source_url in seen:
            continue
        seen.add(source_url)

        image_url = ""
        img = card.find("img")
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        description, age_text = _detail_text(source_url)

        events.append({
            "source": "Дом учёных",
            "title": title,
            "date_raw": shown_date,
            "start_override": today.isoformat(),
            "end_override": end.isoformat(),
            "kind_hint": "long_term",
            "source_url": source_url,
            "image_url": image_url,
            "text_raw": description
            or f"Выставка в Доме учёных СО РАН: «{title}». Работает {shown_date}.",
            "venue": "Дом учёных СО РАН",
            "address": "Морской проспект, 23",
            "district": "Советский",
            "age_text": age_text,
            "tags": "выставки, красивые места",
            "category": "выставка",
        })
    return events
