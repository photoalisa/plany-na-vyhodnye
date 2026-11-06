"""Культура НСК (kulturansk.ru) — шесть разделов афиши.

Что было сломано: карточка обрывалась на div.catalog__card-body, а <img> лежит
выше — в article.catalog__card → div.catalog__card-image. Из-за этого у всех 28
записей была битая ссылка на плейсхолдер (404). Теперь берём article целиком.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://kulturansk.ru"
SECTIONS = [
    ("/events/kino/", "кино", "кино"),
    ("/events/kontserty/", "концерты", "концерт"),
    ("/events/vystavki/", "выставки, красивые места", "выставка"),
    ("/events/vstrechi-lektsii-master-klassy/", "встречи, лекции, мастер-классы", ""),
    ("/events/festivali/", "фестиваль", "фестиваль"),
    ("/events/ekskursii/", "экскурсия", "экскурсия"),
]

_BOILER = re.compile(
    r"(стоимость билетов|купить билет|заказать билет|справки по|оставить отзыв|"
    r"поделиться|поделитесь|как добраться|подписаться на новости)", re.IGNORECASE
)


def _detail(url: str) -> tuple[str, str, str, str]:
    """(описание, площадка, ссылка на страницу места).

    Площадка лежит не в тексте, а в отдельной метке:
    `<p class="article__meta-item _venue"><a href="/places/…">…</a></p>`.
    Вместе с ней приходит ссылка на страницу места, откуда берётся адрес.
    """
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", "", "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    venue = ""
    place_url = ""
    node = soup.select_one(".article__meta-item._venue")
    if node:
        venue = text.clean(node.get_text(" ", strip=True))
        link = node.select_one("a")
        href = (link.get("href") or "").strip() if link else ""
        if href:
            place_url = href if href.startswith("http") else BASE + href

    # Возрастная маркировка стоит отдельным заголовком: «<h3>12+</h3>».
    # Ищем только в заголовках: в тексте анонса «5+» может оказаться частью
    # обычного предложения.
    age_text = ""
    mark = soup.find(["h1", "h2", "h3", "h4"],
                     string=re.compile(r"^\s*\d{1,2}\s*\+\s*$"))
    if mark:
        age_text = text.clean(str(mark)).replace(" ", "")

    for tag in soup.find_all(class_=re.compile(r"breadcrumb|share|social|banner|related", re.I)):
        tag.decompose()
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "aside"]):
        tag.decompose()

    container = None
    for sel in (".event-detail__description", ".event-detail__text", ".article-content",
                ".content-text", ".detail-text", "article", "main"):
        candidate = soup.select_one(sel)
        if candidate and len(candidate.get_text(" ", strip=True)) > 80:
            container = candidate
            break
    if not container:
        return "", venue, place_url, age_text

    pieces = []
    for p in container.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if 40 <= len(value) <= 1200 and not _BOILER.search(value):
            pieces.append(value)
        if sum(len(x) for x in pieces) > 900:
            break
    return " ".join(pieces), venue, place_url, age_text


_PLACE_CACHE: dict[str, str] = {}


def _place_address(place_url: str) -> str:
    """Адрес площадки со страницы /places/: у события адреса нет, у места есть."""
    if not place_url:
        return ""
    if place_url in _PLACE_CACHE:
        return _PLACE_CACHE[place_url]
    html = http.get_text(place_url, ttl=7 * 24 * 3600)
    address = ""
    if html:
        soup = bs4.BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        flat = " ".join(soup.get_text(" ", strip=True).split())
        address = text.find_address(flat)
        # В навигации страницы места тоже встречаются «улицы»: «Площадь
        # Свердлова Телефон». Настоящий адрес всегда с номером дома.
        if address and not re.search(r"\d", address):
            address = ""
    _PLACE_CACHE[place_url] = address
    return address


# Место обычно спрятано в первом абзаце: «в Выставочном зале Новосибирского
# областного Дома народного творчества (ул. Каинская, 5) состоится…».
_RE_PLACE = re.compile(
    r"(?:в|во)\s+([А-ЯЁ][^()]{8,120}?)\s*\((?:ул\.|улица|проспект|пр-т|площадь|"
    r"набережная|переулок|шоссе)[^)]{2,60}\)",
)


def _place(description: str) -> tuple[str, str, str]:
    """(площадка, адрес, территория) из текста анонса."""
    if not description:
        return "", "", ""

    address = text.find_address(description)
    venue = ""
    m = _RE_PLACE.search(description)
    if m:
        venue = text.clean(m.group(1))
        venue = re.sub(r"\s+(состоится|пройд[её]т|откроется|начнётся).*$", "", venue, flags=re.I)
        venue = venue.strip(" ,;—-")
    if address:
        known = text.recognize_venue(address)
        if known:
            venue = venue or known.get("venue", "")
    return venue, address, ""


def _enrich(ev: dict) -> dict:
    description, venue, place_url, age_text = _detail(ev["source_url"])
    ev["text_raw"] = description or ev["title"]
    if age_text:
        ev["age_text"] = age_text

    address = ""
    if not venue:
        # Метки _venue нет — пробуем вытащить место из первого абзаца.
        venue, address, _ = _place(ev["text_raw"])
    if not address:
        address = _place_address(place_url)

    known = text.recognize_venue(venue, address)
    if known:
        venue = known.get("venue") or venue
        address = known.get("address") or address
        ev["district"] = known.get("district") or ev.get("district", "")

    ev["venue"] = venue
    ev["address"] = address
    # Сайт — афиша Новосибирска, поэтому город берём из адреса, а не из
    # упоминаний в тексте: «бердская студия» иначе делает событие бердским.
    ev["geo_area"] = text.guess_area(venue, address) or "Новосибирск"
    ev["source"] = "Культура НСК"
    return ev



def _parse_section(path: str, tags: str, category: str) -> list[dict]:
    url = f"{BASE}{path}"
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select("article.catalog__card")
    print(f"  [{path}] карточек: {len(cards)}")

    candidates = []
    for card in cards:
        heading = card.select_one(".catalog__card-heading")
        date_el = card.select_one("time.catalog__card-info-date") or card.select_one(".catalog__card-info")
        if not heading or not date_el:
            continue
        date_raw = text.clean(date_el.get_text(" ", strip=True))
        if not dates.is_weekend_relevant(date_raw):
            continue

        type_el = card.select_one(".catalog__card-info-type")
        kind = (type_el.get_text(strip=True) if type_el else "").lower()
        link = heading.find("a") or card.find("a", href=True)
        href = link.get("href", "") if link else path
        source_url = href if href.startswith("http") else BASE + href

        image_url = ""
        img = card.select_one(".catalog__card-image img") or card.find("img")
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        candidates.append({
            "title": heading.get_text(" ", strip=True),
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "tags": tags,
            "category": category,
            "kind_hint": "long_term" if ("выставк" in kind or "выставк" in tags) else "",
        })

    out = []
    for ev in http.parallel(_enrich, candidates, workers=6):
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for path, tags, category in SECTIONS:
        for ev in _parse_section(path, tags, category):
            key = f"{ev['source_url']}|{ev['title']}"
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    return events
