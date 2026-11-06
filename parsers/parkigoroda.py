"""Дирекция городских парков Новосибирска — афиша парков.

Сайт на Tilda: листинг /events2026 — карточки `a.t404__link`, у каждой своя
страница с текстом. Даты в расписании выложены картинками, поэтому берём
только те страницы, где дата написана словами («с 7 ноября…», «3 и 10 ноября»).

Афиши — og:image страницы события: у Tilda там лежит постер, а не логотип.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://parkigoroda-nsk.ru"
INDEX_URL = f"{BASE}/events2026"

# Парки дирекции: нужны для площадки и района в карточке.
PARKS = (
    "Берёзовая роща", "Бугринская роща", "Центральный", "У моря Обского",
    "имени С.М. Кирова", "Затулинский", "Сосновый бор", "Первомайский",
)


def _cards() -> list[dict]:
    """Карточки листинга: адрес, заголовок, афиша."""
    html = http.get_text(INDEX_URL, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    seen: set[str] = set()
    for a in soup.select("a.t404__link"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("http", "#", "mailto", "tel")):
            continue
        url = BASE + href if href.startswith("/") else f"{BASE}/{href}"
        if url in seen:
            continue
        seen.add(url)
        title_el = a.select_one(".t404__title")
        img_el = a.select_one(".t404__img")
        title = text.clean(title_el.get_text(" ", strip=True)) if title_el else ""
        image = (img_el.get("data-original") or "").strip() if img_el else ""
        if not title:
            continue
        cards.append({"url": url, "title": title, "image": image})
    return cards


def _body(html: str) -> list[str]:
    """Строки статьи без меню и подвала."""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    blocks = soup.select(".t-text, .t-title, .t-descr")
    source = blocks or [soup.body or soup]
    lines: list[str] = []
    for block in source:
        for line in block.get_text("\n", strip=True).split("\n"):
            value = text.clean(line)
            if value:
                lines.append(value)
    return lines


def _parks(lines: list[str]) -> list[str]:
    blob = " ".join(lines).lower()
    return [park for park in PARKS if park.lower() in blob]


def _description(lines: list[str]) -> str:
    """Абзацы анонса: без капсовых лозунгов и строк из одних эмодзи."""
    pieces: list[str] = []
    for line in lines:
        line = re.sub(r"^[^\w«\"(]+", "", line)
        if len(line) < 60 or line.isupper():
            continue
        if not any(ch.isalpha() for ch in line):
            continue
        pieces.append(line)
        if len(pieces) == 3:
            break
    return " ".join(pieces)


def _venue(parks: list[str]) -> str:
    """Один парк — называем его, несколько — программа идёт по всем паркам."""
    if len(parks) == 1:
        return f"Парк «{parks[0]}»"
    return "Парки Новосибирска"


def _weekend_date(lines: list[str], today) -> str:
    """Первая дата из текста, попадающая на целевые выходные."""
    for line in lines:
        raw = dates.find_period_text(line)
        if raw and dates.is_weekend_relevant(raw, today):
            return raw
    return ""


def _event(card: dict, today) -> dict | None:
    html = http.get_text(card["url"], ttl=24 * 3600)
    if not html:
        return None
    lines = _body(html)
    if not lines:
        return None

    date_raw = _weekend_date(lines, today)
    if not date_raw:
        return None

    body = " ".join(lines)
    # «Участие бесплатное» стоит в конце длинного анонса, далеко за первыми
    # 300 знаками, поэтому ищем по всему тексту страницы, а не по началу.
    free = "бесплат" in body.lower()
    parks = _parks(lines)
    venue = _venue(parks)
    address = text.find_address(body) if len(parks) == 1 else ""
    # Район имеет смысл только для одной площадки: у программы по четырём
    # паркам «Центральный» — это название парка, а не район города.
    district = text.guess_district(venue, address) if len(parks) == 1 else ""

    soup = bs4.BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:image")
    image = og["content"] if og and og.get("content") else card["image"]

    return {
        "source": "Парки",
        "title": card["title"],
        "date_raw": date_raw,
        "source_url": card["url"],
        "image_url": image,
        "text_raw": _description(lines) or body[:900],
        "venue": venue,
        "address": address,
        "district": district,
        "tags": "парки, прогулки, природа",
        "price_text": "Бесплатно" if free else "",
        "price_source": "" if free else body[:300],
    }


def fetch(today=None) -> list[dict]:
    today = today or dates.date.today()
    cards = _cards()
    print(f"  карточек в афише парков: {len(cards)}")
    events = []
    for card in cards:
        item = _event(card, today)
        if item:
            events.append(item)
    return events
