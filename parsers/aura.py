"""ТРЦ «Аура» — события торгового центра.

Карточка листинга отдаёт всё нужное сразу: заголовок, полный период
(«c 5 ноября 2026 по 8 ноября 2026») и афишу в 500 px. Детальную страницу
открываем только ради описания: её og:image — общая заглушка сайта
(«aurajpg1»), а дата приходит без начала периода, из-за чего длинные
выставки терялись, если старт приходился на будни.
"""

from __future__ import annotations

import re
import time

import bs4

from common import dates, http, text

BASE = "https://aura.planeta-mall.ru"
LISTING_PAGES = [f"{BASE}/events?filter=week", f"{BASE}/events"]

EXCLUDE = re.compile(
    r"(премьер|корпоратив|скидк|акци[яию]|акций|распродаж|дисконт|промо[-\s]?|"
    r"чёрн\w* пятниц|черн\w* пятниц|black[-\s]?friday|sale\b|шопинг|худи|бомбер|коллекци|"
    r"открыл(ся|ась|ся)|открытие\s+(магазин|салон|офис)|обновлённ\w*|нового|обновление|"
    r"афиша\s+событий|чем\s+заняться|новогодн\w*\s+корпоратив|анонс\b|изменение\s+входа|"
    r"дарим|розыгрыш|конкурс\b|выиграй|"
    # ретейл и общепит: у ТРЦ это основной поток анонсов
    r"обед|ужин|завтрак|бизнес-ланч|меню\b|привилеги|новинки|старт продаж|"
    r"парковк|шаттл|вакансии|приглашаем на работу|качай|скачай|аренд\w*|"
    r"рекламн\w* кампани)",
    re.IGNORECASE,
)

# Афиша ТРЦ на две трети состоит из анонсов магазинов и ресторанов, поэтому
# культуру ищем по явным меткам, а не по остаточному принципу.
CULT = re.compile(
    r"(кино|фильм|показ|выставк|экспозиц|спектакл|концерт|мастер-класс|мастер класс|"
    r"лекци|фестивал|театр|музык|квест|экскурс|детск|ёлк|елк|ярмарк|встреч|чтен|"
    r"презентац|игротек|анимац)",
    re.IGNORECASE,
)

# og:image у всех событий ведёт на один и тот же файл-заглушку.
GENERIC_IMAGE = re.compile(r"/res/images/")


def _cards() -> list[dict]:
    """Карточки листинга: адрес, заголовок, период и афиша."""
    for page_url in LISTING_PAGES:
        html = http.get_text(page_url, ttl=1800)
        if not html:
            continue
        soup = bs4.BeautifulSoup(html, "html.parser")
        cards: list[dict] = []
        seen: set[str] = set()
        for a in soup.select("a.item-event"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else BASE + href
            if url in seen:
                continue
            seen.add(url)

            title_el = a.select_one(".itemHeader")
            date_el = a.select_one("time.itemDate")
            img_el = a.select_one("img.itemImage")
            title = text.clean(title_el.get_text(" ", strip=True)) if title_el else ""
            date_raw = date_el.get_text(" ", strip=True).strip() if date_el else ""
            src = (img_el.get("src") or "").strip() if img_el else ""
            image = (src if src.startswith("http") else BASE + src) if src else ""
            if not title or not date_raw:
                continue
            cards.append({"url": url, "title": title, "date_raw": date_raw, "image": image})
        if cards:
            return cards
    return []


def _detail(url: str) -> tuple[str, str]:
    """Описание и афиша с детальной страницы (афиша — только не заглушка)."""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    image = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content") and not GENERIC_IMAGE.search(og["content"]):
        image = og["content"]

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    main = soup.select_one("main") or soup.body or soup

    description = ""
    for p in main.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) > 60 and not text.is_boilerplate(value):
            description = value
            break
    return description, image


def _event(card: dict) -> dict | None:
    title = card["title"]
    if EXCLUDE.search(title) or not CULT.search(title):
        return None
    if not dates.is_weekend_relevant(card["date_raw"]):
        return None

    description, og_image = _detail(card["url"])
    return {
        "source": "Аура",
        "title": title,
        "date_raw": card["date_raw"],
        "source_url": card["url"],
        "image_url": card["image"] or og_image,
        "text_raw": description or f"Событие в ТРЦ «Аура»: «{title}».",
        "venue": "ТРЦ «Аура»",
        "address": "ул. Военная, 5",
        "district": "Центральный",
        "tags": "торговый центр",
        "price_source": description[:300],
    }


def fetch() -> list[dict]:
    cards = _cards()
    print(f"  карточек в листинге: {len(cards)}")
    if not cards:
        return []

    # Сайт ТРЦ отдаёт connection reset при частых параллельных запросах,
    # поэтому детальные страницы читаем по одной с паузами.
    events = []
    for card in cards:
        item = _event(card)
        if item:
            events.append(item)
        time.sleep(0.3)
    return events
