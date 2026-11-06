"""Галерея KAPUSTACOR — блок афиши #rec1802627821 (Tilda, карточки t754).

На сайте нет ленты как таковой: афиша свёрстана плиткой, где в описании
карточки лежат строки «Даты: 08.10 - 15.11», «Часы работы», «Адрес», «500 р.».
Отдельный фид Tilda (856540446121) содержит не события, а выпуски журнала,
поэтому берём HTML-плитку.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://kapustacor.ru"
_PAGE = f"{BASE}/"

_RE_DATES_LINE = re.compile(r"Дат[аы]\s*:?\s*([^\n]{4,60})", re.IGNORECASE)
_RE_HOURS = re.compile(r"Часы работы\s*:?\s*([0-9:\-–\s]+)", re.IGNORECASE)
_RE_ADDRESS = re.compile(r"Адрес\s*:?\s*([^\n]{5,80})", re.IGNORECASE)
# Служебная часть карточки: «Даты: … Часы работы: … Адрес: … 500 р. Купить билет»
_RE_SERVICE = re.compile(
    r"(Дат[аы]\s*:|Часы работы|Адрес\s*:|Стоимость|Купить билет|Вход\b)", re.IGNORECASE
)


def _description(blob: str) -> str:
    """Только смысловая часть карточки, без «Даты / Часы работы / Адрес»."""
    parts = _RE_SERVICE.split(blob)
    head = parts[0] if parts else blob
    head = re.sub(r"^\s*\d+\+\s*", "", head).strip(" .,:;-|")
    return head if len(head) >= 40 else ""


def _card_date(blob: str) -> str:
    m = _RE_DATES_LINE.search(blob)
    if m:
        candidate = text.clean(m.group(1))
        candidate = re.split(r"(часы|адрес|стоимость|билет)", candidate, flags=re.IGNORECASE)[0]
        if dates.parse_period(candidate).start:
            return candidate.strip(" .,;:")
    m = re.search(r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?(?:\s*[-–—]\s*\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)?", blob)
    return m.group(0) if m else ""


def fetch() -> list[dict]:
    html = http.get_text(_PAGE, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")

    block = soup.select_one("#rec1802627821") or soup
    cards = block.select("div.t754__col, div.t754__content")
    print(f"  карточек афиши: {len(cards)}")

    events: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        title_el = card.select_one(".t754__title") or card.select_one(".js-product-name")
        descr_el = card.select_one(".t754__descr")
        if not title_el:
            continue
        title = text.clean(title_el.get_text(" ", strip=True))
        blob = text.clean(descr_el.get_text(" ", strip=True)) if descr_el else ""
        if not title or not blob:
            continue

        date_raw = _card_date(blob)
        if not date_raw or not dates.is_weekend_relevant(date_raw):
            continue

        link = card.select_one("a[href]")
        href = (link.get("href") or "").strip() if link else ""
        source_url = href if href.startswith("http") else (BASE + href if href else f"{BASE}/#rec1802627821")
        if source_url in seen:
            continue
        seen.add(source_url)

        image_url = ""
        img = card.find("img")
        if img:
            src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        # Возрастная маркировка — красный бейдж поверх афиши: «0+», «16+».
        age_el = card.select_one(".t754__mark")
        age = text.clean(age_el.get_text(" ", strip=True)) if age_el else ""
        if age and not re.fullmatch(r"\d{1,2}\+", age):
            age = ""

        address_m = _RE_ADDRESS.search(blob)
        address = "ул. Крылова, 2"
        if address_m:
            raw_address = text.clean(address_m.group(1))
            street = text.find_address(raw_address)
            address = street or raw_address
        events.append({
            "source": "Капустакор",
            "title": title,
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "text_raw": _description(blob)
            or f"Выставка в галерее KAPUSTACOR: «{title}». Работает {date_raw}.",
            "venue": "Галерея KAPUSTACOR",
            "address": text.clean(address_m.group(1)) if address_m else "ул. Крылова, 2",
            "district": "Центральный",
            "age_text": age,
            "tags": "выставки, современное искусство",
            "price_source": blob,
        })
    return events
