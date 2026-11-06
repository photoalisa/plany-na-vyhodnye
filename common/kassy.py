"""Обогащение событий данными с билетного сайта kassy.ru.

Каталог не расширяем: события оттуда не добавляются. Берём только те, что
уже есть у нас, и добираем недостающее — описание, афишу, цену, площадку.

Свой поиск на сайте закрыт в robots.txt (`/search/`), поэтому совпадение
ищем по названиям из девяти категорий афиши и только за нужные выходные:
9 запросов на прогон плюс страница события для каждого совпадения.
Страница отдаёт разметку schema.org, поля берём оттуда.
"""

from __future__ import annotations

import json
import re
from datetime import date

import bs4

from . import dates as dts
from . import http, model, text as txt

BASE = "https://nsk.kassy.ru"
CATEGORIES = (
    "cirk", "detskie", "ehkskursiya", "filarmoniya", "koncerty-i-shou",
    "master-klass", "sport", "teatr-kabare", "teatr",
)
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
RE_CARD_DATE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})")

# Порог по названию: «Виола в Небесном граде» ↔ «Концерт «Виола в Небесном
# граде»» даёт 0.85. Ниже 0.7 начинаются чужие спектакли с общими словами.
TITLE_RATIO = 0.7
PLACE_RATIO = 0.6
# Короче этого описания не считаем: «Концерт» нам не нужен.
TEXT_MIN = 40


def _venue(place: bs4.Tag) -> str:
    """Название площадки из карточки: ссылка, а если её нет — до запятой."""
    link = place.find("a")
    if link:
        return link.get_text(" ", strip=True)
    return place.get_text(" ", strip=True).split(",")[0].strip()


def _image(item: bs4.Tag) -> str:
    img = item.select_one("img[src]")
    if not img:
        return ""
    src = (img.get("src") or "").strip()
    if src.startswith("/"):
        return BASE + src
    return src


def _cards(today: date) -> dict[str, dict]:
    """Карточки касс на целевые выходные, ключ — нормализованное название."""
    sat, sun = dts.weekend(today)
    window = {sat, sun}
    found: dict[str, dict] = {}

    for category in CATEGORIES:
        html = http.get_text(f"{BASE}/events/{category}/")
        if not html:
            continue
        soup = bs4.BeautifulSoup(html, "html.parser")
        for item in soup.select("ul.events > li"):
            link = item.select_one("a.event_link")
            place = item.select_one("p.venue")
            if not link or not place:
                continue
            match = RE_CARD_DATE.search(place.get_text(" ", strip=True))
            if not match:
                continue
            month = MONTHS.get(match.group(2).lower())
            if not month:
                continue
            try:
                when = date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                continue
            if when not in window:
                continue

            title = link.get_text(" ", strip=True)
            card = found.setdefault(txt.normalize_title(title), {
                "title": title,
                "url": BASE + link["href"],
                "venue": _venue(place),
                "dates": set(),
                "text": "",
                "image": _image(item),
            })
            card["dates"].add(when)
            light = item.select_one("p.fw-light")
            if light and not card["text"]:
                card["text"] = light.get_text(" ", strip=True)
    return found


def _detail(url: str) -> dict:
    """schema.org-разметка страницы события."""
    html = http.get_text(url)
    if not html:
        return {}
    soup = bs4.BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and str(data.get("@type", "")).endswith("Event"):
            return data
    return {}


def _poster(data: dict) -> str:
    image = data.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url")
    return image if isinstance(image, str) else ""


def _price(data: dict) -> str:
    offers = data.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return ""

    def number(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    low = number(offers.get("lowPrice") or offers.get("price"))
    high = number(offers.get("highPrice"))
    if low == 0:
        return "Бесплатно"
    if low and high and low != high:
        return f"{low}–{high} ₽"
    if low:
        return f"от {low} ₽"
    return ""


def _place_name(data: dict) -> str:
    location = data.get("location") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    if isinstance(location, dict):
        return (location.get("name") or "").strip()
    return ""


def _same_place(event: dict, card: dict) -> bool:
    if not event.get("venue") or not card.get("venue"):
        return False
    return txt.similar(
        txt.normalize_title(event["venue"]), txt.normalize_title(card["venue"])
    ) >= PLACE_RATIO


def _same_day(event: dict, card: dict) -> bool:
    start = event.get("date_start")
    if not start:
        return False
    end = event.get("date_end") or start
    try:
        low, high = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return False
    return any(low <= when <= high for when in card["dates"])


def _needs_text(event: dict) -> bool:
    current = (event.get("text_short") or "").strip()
    return len(current) < 60 or current == model.FALLBACK_TEXT


def enrich(events: list[dict], today: date | None = None) -> dict:
    """Добирает нашим событиям поля, которых у них нет. Возвращает счётчики."""
    today = today or date.today()
    cards = _cards(today)
    stats = {"cards": len(cards), "matched": 0, "text": 0, "image": 0,
             "price": 0, "venue": 0}
    if not cards:
        return stats

    for event in events:
        title = event.get("_norm_title") or txt.normalize_title(event.get("title", ""))
        best, score = None, 0.0
        for card in cards.values():
            ratio = txt.similar(title, txt.normalize_title(card["title"]))
            if ratio > score:
                best, score = card, ratio
        if best is None or score < TITLE_RATIO:
            continue
        # Одно название без совпадения места или дня — ещё не наше событие:
        # «Онегин» идёт в десятке театров.
        if not (_same_place(event, best) or _same_day(event, best)):
            continue
        stats["matched"] += 1

        data = _detail(best["url"])
        description = (data.get("description") or best["text"] or "").strip()
        if description and _needs_text(event):
            fresh = txt.shorten(txt.clean_announcement(description, today.year), 2, 300)
            if len(fresh) >= TEXT_MIN:
                event["text_short"] = fresh
                stats["text"] += 1

        poster = _poster(data) or best["image"]
        if poster and not event.get("image_url"):
            event["image_url"] = poster
            event["image_ok"] = False
            stats["image"] += 1

        price = txt.price_label(_price(data))
        if price and not event.get("price_text") and not event.get("is_free"):
            event["price_text"] = price
            stats["price"] += 1

        venue = _place_name(data)
        if venue and not event.get("venue"):
            event["venue"] = venue
            stats["venue"] += 1

    return stats


def summary(stats: dict) -> str:
    if not stats or not stats.get("matched"):
        return ""
    return (
        f"[i] Кассы: совпало {stats['matched']}, "
        f"описаний +{stats['text']}, афиш +{stats['image']}, "
        f"цен +{stats['price']}, площадок +{stats['venue']}"
    )
