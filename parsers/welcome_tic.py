"""Городской ТИЦ (welcome-novosibirsk.ru) — афиша туристско-информационного центра.

Что было сломано: карточка — это li.card-list__item, а код искал
.event-card/.card-item/article и работал от <a>, поэтому дата (лежит вне ссылки)
не находилась и модуль отдавал ноль событий.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://welcome-novosibirsk.ru"
LISTING = f"{BASE}/events/"
MAX_PAGES = 3

_BOILER = re.compile(
    r"(стоимость билетов|купить билет|справки по|касса|поделиться|подписаться|"
    r"^условия|условия:|возрастное ограничение|^\d+\+$)",
    re.IGNORECASE,
)


def _detail(url: str) -> tuple[str, str, str]:
    """(описание, площадка, адрес) со страницы события.

    Площадка и адрес лежат в блоке «Место проведения»:
    h3 — название («Центр культуры ЦК19»), первый тег — адрес
    («г. Новосибирск, ул.Свердлова, 13»).
    """
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()

    venue = ""
    address = ""
    for block in soup.select(".events-detail__block-content-data"):
        head = block.find("h2")
        if not head or "место проведения" not in head.get_text(" ", strip=True).lower():
            continue
        title = block.select_one(".events-detail__block-content-data-text h3")
        if title:
            venue = text.clean(title.get_text(" ", strip=True))
        link = block.select_one(".events-detail__block-content-data-tags a")
        if link:
            address = text.clean(link.get_text(" ", strip=True))
        break

    container = None
    for sel in (".events-detail", ".event-detail__content", ".page-text", ".content-block",
                "article", "main"):
        node = soup.select_one(sel)
        if node and len(node.get_text(" ", strip=True)) > 80:
            container = node
            break
    if not container:
        container = soup.body or soup

    pieces = []
    for node in container.find_all(["p", "div"]):
        if node.find(["p", "div"]):
            continue
        value = text.clean(node.get_text(" ", strip=True))
        if 60 <= len(value) <= 1200 and not _BOILER.search(value):
            pieces.append(value)
        if sum(len(x) for x in pieces) > 900:
            break

    if not address:
        address = text.find_address(soup.get_text(" ", strip=True))
    return " ".join(pieces), venue, address


_TIC_PLACEHOLDER = "туристско-информационный центр"


def _venue_from_text(blob: str) -> str:
    """Зал из фразы перед адресом: «…, Большой зал "Энергия", Красный проспект, 171/4».

    Нужно, когда в блоке «Место проведения» указан сам ТИЦ как организатор,
    а настоящий зал назван только в тексте анонса.
    """
    address = text.find_address(blob)
    if not address:
        return ""
    idx = blob.find(address)
    if idx <= 0:
        return ""
    head = blob[:idx].rstrip(" ,;")
    part = head.split(",")[-1].strip(" ,;")
    if len(part) < 4 or re.search(r"\d{1,2}[:.]\d{2}", part):
        return ""
    return text.clean(part)


def _enrich(ev: dict) -> dict:
    """Площадка и адрес со страницы события.

    Блок «Место проведения» — самый надёжный источник: в карточке площадка
    есть не всегда, а здесь она вместе с адресом.
    """
    description, venue, address = _detail(ev["source_url"])
    ev["text_raw"] = description or f"«{ev['title']}» — событие, анонсированное ТИЦ."

    venue = venue or ev.get("venue", "")
    if not venue or venue.lower().startswith(_TIC_PLACEHOLDER):
        guessed = _venue_from_text(ev["text_raw"])
        if guessed:
            venue = guessed

    known = text.recognize_venue(venue, address, ev["text_raw"], ev["title"])
    if known:
        ev["venue"] = known.get("venue") or venue
        ev["address"] = known.get("address") or text.normalize_address(address)
        ev["district"] = known.get("district") or ev.get("district", "")
    else:
        ev["venue"] = venue
        if address:
            ev["address"] = text.normalize_address(address)
            ev["district"] = ev.get("district") or text.guess_district(address)
    return ev


_PRICE_LOOK = re.compile(r"^[\d\s]+(?:р\.?|руб\.?|₽)?$|бесплатно", re.IGNORECASE)


def _card_place(card) -> str:
    """Площадка из карточки: «Центр культуры ЦК19».

    В части карточек первым в списке идёт цена («300 р.»), поэтому значения,
    похожие на цену, площадкой не считаем.
    """
    for item in card.select(".events-card__content-info-item-text"):
        value = text.clean(item.get_text(" ", strip=True))
        if value and not _PRICE_LOOK.search(value):
            return value
    return ""


def _card_price(card) -> str:
    """Цена — во втором списке .events-card__content-info (первый занят местом)."""
    lists = card.select(".events-card__content-info")
    if len(lists) < 2:
        return ""
    return text.clean(lists[1].get_text(" ", strip=True))[:60]


def _parse_page(page: int) -> list[dict]:
    url = LISTING if page == 1 else f"{LISTING}?page={page}"
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select("li.card-list__item")
    print(f"  [{page}] карточек: {len(cards)}")

    candidates = []
    for card in cards:
        link = card.select_one("a[href*='/events/']")
        if not link:
            continue
        href = link.get("href", "").strip()
        if not href or href.rstrip("/").endswith("/events"):
            continue
        source_url = href if href.startswith("http") else BASE + href

        title_el = card.select_one(".events-card__content-data-title") or card.find(["h3", "h4"])
        date_el = card.select_one(".events-card__top-date") or card.select_one(
            ".events-card__top-date-block"
        )
        if not title_el or not date_el:
            continue
        date_raw = text.clean(date_el.get_text(" ", strip=True))
        if not dates.is_weekend_relevant(date_raw):
            continue

        image_url = ""
        img = card.select_one("img.events-card__top-picture-image") or card.find("img")
        if img:
            src = (img.get("data-src") or img.get("src") or "").strip()
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        candidates.append({
            "title": title_el.get_text(strip=True),
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "venue": _card_place(card),
            "price_source": _card_price(card),
            "tags": "ТИЦ",
            "kind_hint": "long_term" if "—" in date_raw else "",
        })

    out = []
    for ev in http.parallel(_enrich, candidates, workers=5):
        ev["source"] = "ТИЦ"
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        chunk = _parse_page(page)
        if not chunk:
            break
        for ev in chunk:
            if ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
    return events
