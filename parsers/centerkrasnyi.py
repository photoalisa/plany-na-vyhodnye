"""АРТ-центр «Красный» — выставки, лекции и мастерские.

Сайт центра — витрина его стены ВКонтакте: блок «Новости и статьи»
(`#wall-news`) собран из постов сообщества, у каждого есть страница
`/news/{id}` с полным текстом и ссылкой на исходный пост.

Две особенности:

  * `wall-date` на карточке — дата публикации поста, а не дата события.
    Событие датируется строкой внутри текста («📆 22 ноября (вс), 15:00»).
    Поэтому карточку без разобранной даты пропускаем: иначе анонс лекции,
    написанный в ноябре, попал бы в те выходные, когда его опубликовали.
  * Афиша одной страницей без постраничного вывода: 50 постов, из них
    около десяти — рекламные вставки без заголовка. Такие не берём.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import bs4

from common import dates, http, text

BASE = "https://centerkrasnyi.orgs.biz"
LIST = f"{BASE}/"

VENUE_FALLBACK = "АРТ-центр «Красный»"

_RE_SPACES = re.compile(r"\s+")
# Строки поста размечены эмодзи: 📆 дата, 🏫 адрес, 🎫 цена, ✍ запись.
# В плоском тексте разметка пропадает, поэтому режем по этим знакам.
_MARKS = "🏫📍🎫✍🧒🎟💳📞"
_RE_SEGMENT = (
    r"{mark}\s*(.+?)(?=\s*[" + _MARKS + r"]|\s*Источник:|$)"
)
_RE_TIME = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
_RE_AGE = re.compile(r"(?<!\d)(\d{1,2})\s*\+")
_RE_PRICE = re.compile(r"(?:билет|стоимость|цена)[^\d]{0,20}(\d[\d\s]{0,8})\s*(?:руб|₽)", re.I)
_RE_SOURCE = re.compile(r"Источник:\s*(https?://\S+)")

_CATEGORIES = (
    ("мастер-класс", "мастер-класс"),
    ("мастерская", "мастер-класс"),
    ("лекц", "лекция"),
    ("концерт", "концерт"),
    ("спектакл", "спектакль"),
    ("выставк", "выставка"),
    ("экскурс", "экскурсия"),
    ("кинопоказ", "кино"),
    ("показ", "кино"),
    ("встреч", "встреча"),
    ("клуб", "встреча"),
)
_TAGS = "арт-центр, выставки, лекции"


def _clean(value: str) -> str:
    return text.clean(_RE_SPACES.sub(" ", value or "")).strip()


def _segment(body: str, mark: str) -> str:
    """Кусок текста после эмодзи-метки до следующей метки."""
    match = re.search(_RE_SEGMENT.format(mark=re.escape(mark)), body)
    return _clean(match.group(1)).strip(" .,-—") if match else ""


def _category(title: str) -> str:
    low = title.lower()
    for marker, value in _CATEGORIES:
        if marker in low:
            return value
    return "встреча"


def _cards(soup: bs4.BeautifulSoup) -> list[dict]:
    """Посты стены: страница, заголовок из картинки и афиша для превью."""
    cards: list[dict] = []
    seen: set[str] = set()
    for item in soup.select("div.wall-item div.text-news"):
        link = item.select_one("a[href^='/news/']")
        if link is None:
            continue
        href = link["href"]
        if href in seen:
            continue
        seen.add(href)
        image = item.select_one("img[src]")
        cards.append({
            "url": urljoin(BASE, href),
            "image": (image.get("src") or "") if image else "",
        })
    return cards


def _detail(url: str) -> dict:
    """Заголовок, полный текст, дата события, адрес, цена и возраст."""
    html_doc = http.get_text(url, ttl=6 * 3600)
    if not html_doc:
        return {}
    soup = bs4.BeautifulSoup(html_doc, "html.parser")

    title_el = soup.select_one("h1[itemprop='headline']") or soup.select_one("h1")
    body_el = soup.select_one("div.wall-body")
    if title_el is None or body_el is None:
        return {}

    title = _clean(title_el.get_text(" ", strip=True))
    body = _clean(body_el.get_text(" ", strip=True))
    if not title or not body:
        return {}

    result = {
        "title": title,
        "body": body.split("Источник:")[0].strip(),
        "date_raw": "",
        "time": "",
        "address": "",
        "price": "",
        "free": False,
        "age": "",
        "source_url": "",
        "image": "",
    }

    date_segment = _segment(body, "📆") or _segment(body, "🗓")
    if date_segment:
        clock = _RE_TIME.search(date_segment)
        if clock:
            result["time"] = clock.group(0)
            date_segment = date_segment[:clock.start()].strip(" ,-—")
        result["date_raw"] = date_segment

    place_segment = _segment(body, "🏫") or _segment(body, "📍")
    if place_segment:
        # «Красный пр-кт, 169/2 (метро «Заельцовская»)» — метро в адресе лишнее.
        result["address"] = _clean(re.sub(r"\([^)]*\)", "", place_segment)).strip(" .,-")

    money = _RE_PRICE.search(body)
    if money:
        result["price"] = f"{money.group(1).strip()} руб"
    if not result["price"] and re.search(r"вход свободн|бесплатн", body, re.I):
        result["free"] = True

    age = _RE_AGE.search(body[-260:])
    if age:
        result["age"] = f"{age.group(1)}+"

    origin = _RE_SOURCE.search(body)
    if origin:
        result["source_url"] = origin.group(1).strip()

    image = soup.select_one("div.images-footer img[src]") or soup.select_one("img[src^='https']")
    if image:
        result["image"] = image.get("src") or ""

    return result


def fetch() -> list[dict]:
    html_doc = http.get_text(LIST, ttl=6 * 3600)
    if not html_doc:
        print("  [!] Стена АРТ-центра недоступна")
        return []
    cards = _cards(bs4.BeautifulSoup(html_doc, "html.parser"))
    print(f"  постов на стене: {len(cards)}")

    events: list[dict] = []
    for card in cards:
        detail = _detail(card["url"])
        if not detail:
            continue
        date_raw = detail["date_raw"]
        if not date_raw or not dates.is_weekend_relevant(date_raw):
            continue

        events.append({
            "source": "АРТ-центр «Красный»",
            "title": detail["title"],
            "date_raw": date_raw,
            "time_text": detail["time"],
            "source_url": detail["source_url"] or card["url"],
            "text_raw": detail["body"],
            "venue": VENUE_FALLBACK,
            "address": detail["address"],
            "district": "",
            "category": _category(detail["title"]),
            "tags": _TAGS,
            "age_text": detail["age"],
            "price_source": "Вход свободный" if detail["free"] else detail["price"],
            "image_url": detail["image"] or card["image"],
        })

    print(f"  на выходные: {len(events)}")
    return events
