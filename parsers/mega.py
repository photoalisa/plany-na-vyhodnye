"""ТРЦ «МЕГА Новосибирск» — афиша через headless-браузер (контент рендерится JS)."""

from __future__ import annotations

import re

from common import dates, http, text

BASE = "https://mega.ru"
URLS = [f"{BASE}/events/novosibirsk/", f"{BASE}/offers/novosibirsk/?category=events"]


def _fetch_playwright() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [!] playwright не установлен — МЕГА пропущена")
        return []

    events: list[dict] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=http.USER_AGENT,
                    locale="ru-RU",
                    viewport={"width": 1440, "height": 900},
                )
                for url in URLS:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2500)
                        for _ in range(4):
                            page.mouse.wheel(0, 2500)
                            page.wait_for_timeout(400)
                        cards = page.query_selector_all("article.card-shop, article.card-offer")
                        print(f"  [{url.rstrip('/').rsplit('/', 1)[-1]}] карточек: {len(cards)}")
                        for card in cards:
                            link = card.query_selector("a[href*='/events/'], a[href*='/offers/']")
                            if not link:
                                continue
                            href = (link.get_attribute("href") or "").strip()
                            if not href:
                                continue
                            source_url = href if href.startswith("http") else BASE + href
                            if source_url in seen:
                                continue
                            title = text.clean(link.inner_text())
                            if not title:
                                continue
                            date_el = card.query_selector(".card-offer__date, time, [class*='date']")
                            blob = date_el.inner_text() if date_el else card.inner_text()
                            date_raw = _first_date(blob)
                            if not date_raw or not dates.is_weekend_relevant(date_raw):
                                continue
                            img = card.query_selector("img")
                            image_url = ""
                            if img:
                                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                                if src:
                                    image_url = src if src.startswith("http") else BASE + src
                            desc_el = card.query_selector(".card-offer__description, [class*='description']")
                            description = text.clean(desc_el.inner_text()) if desc_el else ""
                            seen.add(source_url)
                            events.append({
                                "source": "МЕГА",
                                "title": title,
                                "date_raw": date_raw,
                                "source_url": source_url,
                                "image_url": image_url,
                                "text_raw": description or f"Событие в ТРЦ «МЕГА»: «{title}».",
                                "venue": "ТРЦ «МЕГА»",
                                "address": "ул. Ватутина, 107",
                                "district": "Кировский",
                                "tags": "торговый центр, можно с детьми",
                                "price_source": card.inner_text()[:250],
                            })
                    except Exception as e:  # noqa: BLE001
                        print(f"  [-] {url}: {type(e).__name__}: {e}")
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        print(f"  [!] Браузер не запустился: {type(e).__name__}: {e}")
    return events


_RE_MONTH_RANGE = re.compile(
    r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s*[-–—]\s*(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря))?",
    re.IGNORECASE,
)


def _first_date(blob: str) -> str:
    if not blob:
        return ""
    m = _RE_MONTH_RANGE.search(blob)
    if m:
        if m.group(3):
            return f"{m.group(1)} {m.group(2)} - {m.group(3)} {m.group(4)}"
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"\d{1,2}\.\d{1,2}(?:\.\d{4})?", blob)
    return m.group(0) if m else ""


def fetch() -> list[dict]:
    return _fetch_playwright()
