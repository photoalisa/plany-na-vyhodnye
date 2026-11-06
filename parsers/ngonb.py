"""Новосибирская областная научная библиотека (ngonb.ru) — афиша.

Что было сломано: в карточке листинга нет описания, поэтому `text_raw`
собирался как «заголовок + метки + адрес». Метки на сайте фильтровые
(«Тематические»), и они попадали в короткое описание: «Выставка журналов
«Эта многогранная геодезия». Тематические.»

Теперь описание берётся со страницы события (`.event-content`), а метки идут
только в `tags`. Заодно из меток разбираются категория и признак «бесплатно»,
и адрес делится на площадку и улицу.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import bs4

from common import dates, http, text

BASE = "https://ngonb.ru"
# Афиша разбита на разделы: «События» — разовые с датой и временем, страницы
# по 8 штук; «Выставки» — длительные периоды. Раньше читали только первую
# страницу /afisha/, и всё, что начиналось со второй, терялось.
EVENTS = f"{BASE}/afisha/events/"
EXHIBITIONS = f"{BASE}/afisha/exhibitions/"
PAGE_LIMIT = 8

# Служебные метки: это не тема события, а способ фильтрации на сайте.
_SERVICE_TAGS = {
    "тематические", "бесплатно", "платно", "по билетам", "пушкинская карта",
    "доступно по пушкинской карте", "событие", "афиша",
}
_CATEGORY_TAGS = {
    "лекция": "лекция",
    "концерт": "концерт",
    "выставка": "выставка",
    "фотовыставк": "выставка",
    "спектакл": "спектакль",
    "встреча": "встреча",
    "экскурсия": "экскурсия",
    "мастер-класс": "мастер-класс",
    "фестивал": "фестиваль",
    "кино": "кино",
}
_STREET_WORDS = (
    "ул.", "улица", "проспект", "пр-т", "переулок", "проезд", "площадь",
    "набережная", "шоссе", "бульвар",
)
# Служебный хвост страницы: даты правки и счётчик аналитики. Один раз попал
# в описание заседания филателистов вместо самого текста.
_NOISE = re.compile(
    r"дата\s+(?:создания|изменения)|статистика и аналитика|"
    r"отраслевой\s+счётчик|все права защищены",
    re.IGNORECASE,
)


def _clean_tags(card: bs4.element.Tag) -> list[str]:
    """Метки карточки без служебных: «Тематические», «бесплатно», «платно»."""
    tag_el = card.select_one(".event-tags")
    if tag_el is None:
        return []
    out = []
    for link in tag_el.find_all("a"):
        value = text.clean(link.get_text(" ", strip=True))
        if value and value.lower() not in _SERVICE_TAGS:
            out.append(value)
    return out


def _is_free_tag(card: bs4.element.Tag) -> bool:
    tag_el = card.select_one(".event-tags")
    if tag_el is None:
        return False
    return "бесплатно" in text.clean(tag_el.get_text(" ", strip=True)).lower()


def _category(tags: list[str], title: str) -> str:
    blob = f"{' '.join(tags)} {title}".lower()
    for key, value in _CATEGORY_TAGS.items():
        if key in blob:
            return value
    return ""


def _place(location: str) -> tuple[str, str, str]:
    """«"Дом да Винчи", ул. Коммунистическая, 34» -> (площадка, адрес, район)."""
    value = text.clean(location)
    if not value:
        return "", "", ""
    parts = [p.strip(" .") for p in value.split(",") if p.strip(" .")]
    street_idx = None
    for idx, part in enumerate(parts):
        if any(word in part.lower() for word in _STREET_WORDS):
            street_idx = idx
            break

    if street_idx is None:
        known = text.recognize_venue(value, value)
        if known:
            return known.get("venue", ""), known.get("address", ""), known.get("district", "")
        # В карточке указан только зал или кабинет («к. 301», «лофт „Книжный
        # шкаф“») — это главное здание библиотеки. Без адреса «к. 301»
        # ничего не говорит читателю, поэтому дописываем улицу.
        return (
            "Новосибирская областная научная библиотека",
            f"ул. Советская, 6, {value}",
            "Центральный",
        )

    address = text.normalize_address(", ".join(parts[street_idx:street_idx + 2]))
    venue = ", ".join(parts[:street_idx])
    known = text.recognize_venue(address)
    if known:
        return known.get("venue") or venue, known.get("address") or address, known.get("district", "")
    return venue, address, ""


def _detail(url: str) -> tuple[str, str]:
    """(описание, текст страницы для разбора цены)."""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()

    container = None
    for sel in (".event-content", ".event-detail__content", ".event-detail", "article", "main"):
        node = soup.select_one(sel)
        if node and len(node.get_text(" ", strip=True)) > 120:
            container = node
            break
    container = container or soup.body or soup

    pieces: list[str] = []
    for p in container.find_all(["p", "li"]):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) < 30 or value in pieces or _NOISE.search(value):
            continue
        pieces.append(value)
        if sum(len(x) for x in pieces) > 1200:
            break

    description = " ".join(pieces)
    if not description:
        description = text.clean(container.get_text(" ", strip=True))[:900]
    # «Дата создания: …» и счётчик идут хвостом — обрезаем по первому маркеру.
    description = _NOISE.split(description)[0].strip()
    return description, text.clean(container.get_text(" ", strip=True))[:1200]


def _enrich(ev: dict) -> dict:
    description, page_text = _detail(ev["source_url"])
    ev["text_raw"] = description or ev["title"]
    ev["price_source"] = page_text
    return ev


def _date_text(card: bs4.element.Tag) -> str:
    el = card.select_one(".event-date")
    return text.clean(el.get_text(" ", strip=True)) if el else ""


def _beyond_weekend(cards: list, today) -> bool:
    """На странице уже пошли даты после выходных — листать дальше незачем."""
    _, sunday = dates.weekend(today)
    for card in cards:
        period = dates.parse_period(_date_text(card), today)
        if period.start and period.start > sunday:
            return True
    return False


def _cards(url: str, today, paginate: bool = False) -> list:
    """Карточки раздела. С пагинацией идём, пока не упрёмся в даты после выходных."""
    found: list = []
    seen: set[str] = set()
    for page in range(1, PAGE_LIMIT + 1):
        page_url = url if page == 1 else f"{url}?PAGEN_2={page}"
        html = http.get_text(page_url, ttl=1800)
        if not html:
            break
        soup = bs4.BeautifulSoup(html, "html.parser")
        page_cards = [c for c in soup.select(".event-card") if _date_text(c)]
        if not page_cards:
            break
        for card in page_cards:
            link = card.find("a", href=True)
            href = (link.get("href") or "").strip() if link else ""
            key = urljoin(BASE + "/", href) if href else ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            found.append(card)
        if not paginate or _beyond_weekend(page_cards, today):
            break
    return found


def fetch() -> list[dict]:
    today = dates.date.today()
    cards = _cards(EVENTS, today, paginate=True) + _cards(EXHIBITIONS, today)
    print(f"  карточек: {len(cards)}")

    events: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        title_el = card.select_one(".event-title") or card.find(["h2", "h3", "h4"])
        date_el = card.select_one(".event-date")
        if not title_el or not date_el:
            continue

        title = text.clean(title_el.get_text(" ", strip=True))
        date_raw = text.clean(date_el.get_text(" ", strip=True))
        if not title or not dates.is_weekend_relevant(date_raw):
            continue

        link = card.find("a", href=True)
        href = (link.get("href") or "").strip() if link else ""
        source_url = urljoin(BASE + "/", href) if href else LISTING
        if source_url in seen:
            continue
        seen.add(source_url)

        image_url = ""
        img = card.select_one(".event-img img") or card.find("img")
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src:
                image_url = urljoin(BASE + "/", src)

        tags = _clean_tags(card)
        # Возрастная метка есть только в карточке: «18+» на странице события
        # может не быть, и тогда психоаналитический кружок попадал в детские.
        age_el = card.select_one(".age-dopusk")
        age_text = text.clean(age_el.get_text(" ", strip=True)) if age_el else ""
        loc_el = card.select_one(".event-location")
        location = text.clean(loc_el.get_text(" ", strip=True)) if loc_el else ""
        venue, address, district = _place(location)

        events.append({
            "source": "НГОНБ",
            "title": title,
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "venue": venue or "Новосибирская областная научная библиотека",
            "address": address,
            "district": district or "Центральный",
            "tags": ", ".join(tags),
            "age_text": age_text,
            "category": _category(tags, title),
            "price_source": "бесплатно" if _is_free_tag(card) else "",
            "is_free": _is_free_tag(card),
        })

    return http.parallel(_enrich, events, workers=5)
