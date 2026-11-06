"""Исторический парк «Россия — моя история» в Новосибирске.

Две страницы: `/expositions/` — три постоянные мультимедийные выставки,
`/for-visitors/events/` — афиша, даты там записаны как «08 / 11 / 2026».
Описание события лежит на его странице в блоке `.twosides-left`.

Цены не берём: на страницах они сидят в форме бронирования экскурсий и в
таблице льгот, и «Стоимость 1 000» оттуда легко попадает в карточку как
цена билета. Пока честнее «Цена на сайте».
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import bs4

from common import dates, http, text

BASE = "https://novosibirsk.myhistorypark.ru"
EXPOSITIONS = f"{BASE}/expositions/"
EVENTS = f"{BASE}/for-visitors/events/"
VENUE = "Исторический парк «Россия — моя история»"
ADDRESS = "ул. Василия Старощука, 24"
DISTRICT = "Октябрьский"

RE_DATE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})")
# Хвосты со страниц: льготы, форма брони и напоминание про расписание.
NOISE = re.compile(
    r"бесплатно посетить исторический парк|сопровождающ|льгот|"
    r"обработку персональных данных|забронировать|кол-во человек|"
    r"стоимость|уточняйте еженедельное расписание|пушкинской карт",
    re.IGNORECASE,
)


def _abs(href: str) -> str:
    return urljoin(BASE + "/", (href or "").strip())


def _image(node) -> str:
    """Первая настоящая картинка: в карточке события первым идёт svg-стрелка."""
    for img in node.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src or src.lower().endswith(".svg"):
            continue
        return _abs(src)
    return ""


def _cap(value: str) -> str:
    """На сайте заголовки событий начинаются с маленькой буквы."""
    return value[:1].upper() + value[1:] if value else value


def _date(day: str, month: str, year: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _expositions() -> list[dict]:
    html = http.get_text(EXPOSITIONS, ttl=6 * 3600)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    events = []
    for item in soup.select(".exhibition-list-item"):
        title_el = item.select_one(".exhibition-list-item-title")
        link = item.select_one("a[href*='expositions/']")
        if not title_el or not link:
            continue
        desc_el = item.select_one(".event-item-description")
        events.append({
            "source": "Моя история",
            "title": _cap(text.clean(title_el.get_text(" ", strip=True))),
            "date_raw": "Постоянная экспозиция",
            "kind_hint": "permanent",
            "source_url": _abs(link.get("href")),
            "image_url": _image(item),
            "text_raw": text.clean(desc_el.get_text(" ", strip=True)) if desc_el else "",
            "venue": VENUE,
            "address": ADDRESS,
            "district": DISTRICT,
            "tags": "выставки, история",
        })
    return events


def _events(today: date) -> list[dict]:
    html = http.get_text(EVENTS, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    sat, sun = dates.weekend(today)
    events = []
    for item in soup.select("a.afisha-item"):
        href = (item.get("href") or "").strip()
        title_el = item.select_one(".afisha-item_big-title")
        dates_el = item.select_one(".afisha-item_big-dates")
        if not href or not title_el or not dates_el:
            continue
        stamps = RE_DATE.findall(dates_el.get_text(" ", strip=True))
        start = _date(*stamps[0]) if stamps else None
        end = _date(*stamps[-1]) if stamps else None
        if not start:
            continue
        end = end or start
        if start > sun or end < sat:
            continue
        events.append({
            "source": "Моя история",
            "title": _cap(text.clean(title_el.get_text(" ", strip=True))),
            "date_raw": (f"{start:%d.%m.%Y}" if start == end
                         else f"{start:%d.%m.%Y} - {end:%d.%m.%Y}"),
            "start_override": start.isoformat(),
            "end_override": end.isoformat(),
            "kind_hint": "one_time" if start == end else "long_term",
            "source_url": _abs(href),
            "image_url": _image(item),
            "text_raw": "",
            "venue": VENUE,
            "address": ADDRESS,
            "district": DISTRICT,
            "tags": "выставки, история",
        })
    return events


def _detail(url: str) -> tuple[str, str]:
    """(описание, афиша со страницы события).

    В листинге лежит кадрированная миниатюра 765×420: у постеров средин
    верх и низ. На странице события та же афиша целиком — 1150×1633.
    """
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    poster = ""
    img = soup.select_one(".news-image img") or soup.select_one("img.news-image")
    if img:
        src = (img.get("src") or img.get("data-src") or "").strip()
        if src:
            poster = _abs(src)

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    box = soup.select_one(".twosides-left") or soup.select_one("main") or soup.body or soup
    pieces: list[str] = []
    for p in box.find_all(["p", "li"]):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) < 40 or value in pieces or NOISE.search(value):
            continue
        pieces.append(value)
        if sum(len(x) for x in pieces) > 1200:
            break
    return " ".join(pieces), poster


def _drop_lead(description: str, title: str) -> str:
    """Первый абзац часто повторяет заголовок — в карточке он лишний."""
    parts = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)
    if len(parts) == 2 and text.similar(parts[0], title) >= 0.55:
        return parts[1]
    return description


def _enrich(ev: dict) -> dict:
    if not ev.get("text_raw"):
        description, poster = _detail(ev["source_url"])
        ev["text_raw"] = _drop_lead(description, ev["title"]) or ev["title"]
        if poster:
            ev["image_url"] = poster
    return ev


def fetch(today: date | None = None) -> list[dict]:
    today = today or dates.date.today()
    events = _expositions() + _events(today)
    print(f"  карточек: {len(events)}")
    return http.parallel(_enrich, events, workers=4)
