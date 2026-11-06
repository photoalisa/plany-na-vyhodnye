"""ЦК19 — три раздела афиши.

Две ловушки этого сайта:

1. Все три раздела выглядят одинаково, поэтому раньше парсер трижды читал
   главную страницу вместо /events/, /exhibitions/, /concerts/.
2. Лента отсортирована от новых к старым, а год в датах не указан —
   «01.11_15:00» может быть и 2026, и 2025 годом. Из-за этого прошлогодние
   события попадали в таблицу как актуальные.

Решение второй: год восстанавливается по позиции в ленте
(common.dates.assign_years_descending) — пока даты убывают, год тот же,
на «перескоке» вверх год уменьшается; явный год в строке («3.09_5.09.2020»)
имеет приоритет.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://cc19.ru"
SECTIONS = [
    ("/events/", "встречи, лекции", "one_time"),
    ("/exhibitions/", "выставки, красивые места", "long_term"),
    ("/concerts/", "концерты", "one_time"),
]

_SKIP_PARAGRAPH = re.compile(
    r"(купить билет|стоимость|руб\.|еш группа|@ ?2020|возрастн|пушкинск)", re.IGNORECASE
)


def _detail_text(url: str) -> str:
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()
    pieces = []
    for p in soup.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) > 25 and not _SKIP_PARAGRAPH.search(value):
            pieces.append(value)
        if sum(len(x) for x in pieces) > 900:
            break
    return " ".join(pieces)


def _parse_section(path: str, tags: str, kind_hint: str) -> list[dict]:
    url = f"{BASE}{path}"
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")

    cards: list[dict] = []
    raw_dates: list[str] = []
    for item in soup.select(".post-grid__item"):
        date_el = item.select_one(".post-grid__item-date")
        title_el = item.select_one(".post-grid__item-title")
        if not date_el or not title_el:
            continue
        raw = date_el.get_text(strip=True)
        raw_dates.append(raw)

        link = title_el.find("a")
        source_url = (link.get("href") if link else "") or url
        if source_url.startswith("/"):
            source_url = BASE + source_url

        img = item.select_one(".post-grid__item-img")
        image_url = ""
        if img:
            image_url = img.get("src") or img.get("data-src") or ""
            if image_url.startswith("/"):
                image_url = BASE + image_url

        price_el = item.select_one(".post-grid__item-price")
        cards.append({
            "title": title_el.get_text(strip=True),
            "date_raw": raw,
            "source_url": source_url,
            "image_url": image_url,
            "price_text": price_el.get_text(strip=True) if price_el else "",
            "tags": tags,
        })

    periods = dates.assign_years_descending(raw_dates)
    print(f"  [{path}] карточек: {len(cards)}, на выходные: "
          f"{sum(1 for p in periods if p.is_relevant())}")

    candidates = []
    for card, period in zip(cards, periods):
        if not period.is_relevant():
            continue
        card["start_override"] = period.start_iso
        card["end_override"] = period.end_iso
        card["kind_hint"] = period.kind if period.kind != "unknown" else kind_hint
        candidates.append(card)

    out = []
    for ev in http.parallel(
        lambda e: dict(e, text_raw=_detail_text(e["source_url"]) or f"Программа ЦК19: «{e['title']}»."),
        candidates, workers=6,
    ):
        ev["source"] = "ЦК19"
        ev["venue"] = "Центр культуры ЦК19"
        ev["address"] = "ул. Свердлова, 13"
        ev["district"] = "Центральный"
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for path, tags, kind_hint in SECTIONS:
        for ev in _parse_section(path, tags, kind_hint):
            if ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
    return events
