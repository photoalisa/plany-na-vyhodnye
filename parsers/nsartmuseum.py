"""Новосибирский государственный художественный музей (nsartmuseum.ru).

Что было сломано: сайт переехал, старые адреса `/exhibitions/active` и
`/exhibitions` отдают 404, и источник молча возвращал ноль событий.

Теперь адреса новые (`/events/exhibitions/`, `/events/events/`), а даты в
списках не публикуются вовсе — они лежат только на странице события, в
`span.event-detail-hero__meta-item--date` («23 октября 2026 г. — 5 декабря
2026 г.»). Поэтому карточка листинга даёт название, ссылку и афишу, а дату и
описание добираем со страницы.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://nsartmuseum.ru"
LISTINGS = [
    (f"{BASE}/events/exhibitions/?status=current", "выставки, красивые места", "выставка", "long_term"),
    (f"{BASE}/events/events/", "встречи, лекции", "лекция", ""),
]

DATE_SELECTOR = ".event-detail-hero__meta-item--date"
_BOILER = re.compile(
    r"(купить билет|стоимость|телефон|касса|справки|записаться|билеты|цена)", re.IGNORECASE
)
_IMAGE_NOISE = re.compile(r"(arrow|design|icon|svg|logo|pushkin)", re.IGNORECASE)


def _clean_date(raw: str) -> str:
    """«23 октября 2026 г. — 5 декабря 2026 г.» -> «23 октября 2026 - 5 декабря 2026»."""
    value = re.sub(r"\s*\bг\.\s*", " ", raw or "")
    value = re.sub(r"\s+", " ", value).strip(" .—-")
    return value.strip()


def _detail(url: str) -> tuple[str, str]:
    """(описание, дата события со страницы)."""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()

    date_raw = ""
    node = soup.select_one(DATE_SELECTOR)
    if node:
        date_raw = _clean_date(node.get_text(" ", strip=True))

    preview = soup.select_one(".event-detail-hero__preview")
    pieces: list[str] = []
    if preview:
        value = text.clean(preview.get_text(" ", strip=True))
        if len(value) >= 20:
            pieces.append(value)

    container = None
    for sel in (".event-detail-page__text", ".event-detail-page__description",
                ".event-detail-page__body", "article", "main"):
        candidate = soup.select_one(sel)
        if candidate and len(candidate.get_text(" ", strip=True)) > 120:
            container = candidate
            break
    container = container or soup.body or soup

    for p in container.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) < 40 or value in pieces:
            continue
        if len(value) < 90 and _BOILER.search(value):
            continue
        pieces.append(value)
        if sum(len(x) for x in pieces) > 900:
            break

    return " ".join(pieces), date_raw


def _cards(soup: bs4.BeautifulSoup, tags: str, category: str, kind_hint: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for card in soup.select("article.content-card"):
        link = card.select_one(".content-card__title a")
        if link is None:
            continue
        href = (link.get("href") or "").strip()
        if not href:
            continue
        source_url = href if href.startswith("http") else BASE + href
        if source_url in seen:
            continue

        title = text.clean(link.get_text(" ", strip=True))
        if not title:
            continue

        image_url = ""
        img = card.select_one(".content-card__media img")
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src and not _IMAGE_NOISE.search(src):
                image_url = src if src.startswith("http") else BASE + src

        # Возрастная маркировка — бейдж на карточке: «<span class="content-card__badge">12+</span>»
        age_text = ""
        for badge in card.select(".content-card__badge"):
            value = text.clean(badge.get_text(" ", strip=True))
            if re.fullmatch(r"\d{1,2}\s*\+", value):
                age_text = value.replace(" ", "")
                break

        seen.add(source_url)
        out.append({
            "title": title,
            "source_url": source_url,
            "image_url": image_url,
            "age_text": age_text,
            "tags": tags,
            "category": category,
            "kind_hint": kind_hint,
        })
    return out


def _enrich(ev: dict) -> dict:
    description, date_raw = _detail(ev["source_url"])
    ev["date_raw"] = date_raw
    ev["text_raw"] = description or f"Программа Художественного музея: «{ev['title']}»."
    return ev


def _parse_listing(url: str, tags: str, category: str, kind_hint: str) -> list[dict]:
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    candidates = _cards(soup, tags, category, kind_hint)
    print(f"  [{url.rstrip('/').rsplit('/', 1)[-1]}] карточек: {len(candidates)}")
    out = []
    for ev in http.parallel(_enrich, candidates, workers=5):
        if not ev.get("date_raw") or not dates.is_weekend_relevant(ev["date_raw"]):
            continue
        ev["source"] = "Художественный музей"
        ev["venue"] = "Новосибирский художественный музей"
        ev["address"] = "Красный проспект, 5"
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for url, tags, category, kind_hint in LISTINGS:
        for ev in _parse_listing(url, tags, category, kind_hint):
            if ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
    return events
