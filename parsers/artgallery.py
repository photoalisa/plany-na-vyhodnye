"""Арт-галерея «Коллекция» (artgallerynsk.ru) — выставки и события.

Две ленты: `/exhibitions` и `/events`. Карточка — ссылка `a.article`, дата
в `.date` (либо «22.10.2026 — 10.12.2026», либо одна дата), афиша лежит
фоном `.article-logo` в инлайн-стиле, описания в листинге нет — за ним
идём на страницу события.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://artgallerynsk.ru"
VENUE = "Арт-галерея «Коллекция»"
ADDRESS = "ул. Советская, 26"
DISTRICT = "Центральный"

PAGES = (
    ("/exhibitions", "выставка"),
    ("/events", "событие"),
)

_RE_BG = re.compile(r"url\((['\"]?)([^'\")]+)\1\)")
_RE_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.](\d{2})(?!\d)")


def _absolute(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return url if url.startswith("http") else BASE + url


def _detail(url: str, want_time: bool) -> tuple[str, str]:
    """(описание, время начала) со страницы события.

    Время берём только у разовых событий: у выставки в тексте стоит время
    вернисажа, а не часы работы, и в карточке оно было бы неверным.
    """
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    container = soup.select_one(".content") or soup.body
    if container is None:
        return "", ""

    paragraphs = []
    for p in container.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) > 50 and not text.is_boilerplate(value):
            paragraphs.append(value)
    description = " ".join(paragraphs[:2])

    # Время стоит в тексте строкой «3 декабря, 18:00 сбор гостей».
    time_text = ""
    if want_time:
        plain = container.get_text(" ", strip=True)
        for match in _RE_TIME.finditer(plain):
            before = plain[:match.start()].rstrip()
            # телефон +7 953 861 59 37 иначе читается как время
            if before.endswith("+") or (before and before[-1].isdigit()):
                continue
            time_text = f"{int(match.group(1)):02d}:{match.group(2)}"
            break
    return description, time_text


def _enrich(ev: dict) -> dict:
    description, time_text = _detail(ev["source_url"], ev["category"] == "событие")
    ev["text_raw"] = description or ev["title"]
    if time_text:
        ev["time_text"] = time_text
    return ev


def _parse_page(path: str, category: str) -> list[dict]:
    html = http.get_text(BASE + path, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select("a.article")
    print(f"  [{path}] карточек: {len(cards)}")

    out = []
    for card in cards:
        title_el = card.select_one("h2.page-header") or card.find("h2")
        date_el = card.select_one(".date")
        if not (title_el and date_el):
            continue

        date_raw = text.clean(date_el.get_text(" ", strip=True))
        # Отсекаем по датам до чтения страницы: подробности нужны только тем
        # событиям, которые попадают в ближайшие выходные.
        if not date_raw or not dates.is_weekend_relevant(date_raw):
            continue

        logo = card.select_one(".article-logo")
        image_url = ""
        if logo:
            style = logo.get("style") or ""
            found = _RE_BG.search(style)
            if found:
                image_url = _absolute(found.group(2))

        out.append({
            "source": "Галерея «Коллекция»",
            "title": text.clean_title(title_el.get_text(" ", strip=True)),
            "date_raw": date_raw,
            "source_url": _absolute(card.get("href") or ""),
            "image_url": image_url,
            "venue": VENUE,
            "address": ADDRESS,
            "district": DISTRICT,
            "category": category,
            # Цены на сайте нет: в карточке останется «Уточняйте на сайте».
            "price_text": "",
        })
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for path, category in PAGES:
        for ev in _parse_page(path, category):
            if not ev["source_url"] or ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
    return http.parallel(_enrich, events, workers=5)
