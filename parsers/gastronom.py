"""Гастрокорт (gastro-nsk.ru) — мероприятия гастрономического пространства.

Список /events/ — карточки с подписью даты: «13 ноября», «каждое воскресенье»,
«8 | 22 ноября», «в ноябре» или пусто. Подпись в списке бывает короче, чем в
блоке .top-meta на странице события, поэтому дату берём со страницы, а подпись
из списка используем как запасной вариант.

Площадка у всех событий одна — Гастрокорт на Мичурина, 12, поэтому venue,
адрес и район заданы здесь, а не угадываются по тексту.
"""

from __future__ import annotations

import calendar
import re

import bs4

from common import dates, http, text

BASE = "https://gastro-nsk.ru"
LIST_URL = f"{BASE}/events/"

VENUE = "Гастрокорт"
ADDRESS = "ул. Мичурина, 12"
DISTRICT = "Центральный"

_MONTH = (
    r"(?:январ[ья]|феврал[ья]|марта|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|"
    r"августа|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])"
)
_MONTH_NUM = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "ма": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

# «8 | 22 ноября» — в одной подписи две даты одного месяца.
_RE_TWO_DATES = re.compile(rf"(\d{{1,2}})\s*[|/]\s*(\d{{1,2}})\s+({_MONTH})", re.IGNORECASE)
# «в ноябре» — событие идёт весь месяц, конкретных чисел нет. На сайте месяц
# стоит в предложном падеже, поэтому к обычному шаблону добавлен этот вариант.
_RE_MONTH_ONLY = re.compile(
    r"^\s*в\s+(январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|"
    r"октябре|ноябре|декабре)\s*$",
    re.IGNORECASE,
)
_MONTH_NOM = {
    "январе": "января", "феврале": "февраля", "марте": "марта", "апреле": "апреля",
    "мае": "мая", "июне": "июня", "июле": "июля", "августе": "августа",
    "сентябре": "сентября", "октябре": "октября", "ноябре": "ноября",
    "декабре": "декабря",
}
_RE_DAY = re.compile(rf"(\d{{1,2}})\s+({_MONTH})", re.IGNORECASE)
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_RE_DAILY = re.compile(r"кажд\w*\s+день|ежедневно", re.IGNORECASE)
_RE_IMAGE = re.compile(r"background-image:\s*url\((['\"]?)([^)'\"]+)\1\)", re.IGNORECASE)


def fetch() -> list[dict]:
    page = http.get_text(LIST_URL, ttl=6 * 3600)
    if not page:
        return []

    events: list[dict] = []
    seen: set[str] = set()

    for card in _cards(page):
        url = card["url"]
        if url in seen:
            continue
        seen.add(url)

        title, labels, body = _detail(url)
        title = title or card["title"]
        if not title:
            continue

        body = body or card["text"]
        variants = _date_variants(" ".join(labels) or card["label"], body)

        for date_raw in variants:
            if not dates.is_weekend_relevant(date_raw):
                continue
            events.append({
                "source": "Гастрокорт",
                "title": title,
                "date_raw": date_raw,
                "time_text": _time(date_raw, body),
                "source_url": url,
                "image_url": card["image"],
                "text_raw": body or title,
                "venue": VENUE,
                "address": ADDRESS,
                "district": DISTRICT,
                "price_source": body[:300],
            })
    return events


def _cards(page: str) -> list[dict]:
    """Карточки списка: ссылка, заголовок, подпись даты, афиша."""
    soup = bs4.BeautifulSoup(page, "html.parser")
    cards: list[dict] = []
    for item in soup.select(".items-list__item"):
        link = item.select_one("a.items-list__link")
        href = (link.get("href") or "").strip() if link else ""
        if not href:
            continue
        title_tag = item.select_one(".items-list__item-title")
        label_tag = item.select_one(".items-list__item-company")
        image = ""
        style_tag = item.select_one(".card-image")
        if style_tag and style_tag.get("style"):
            found = _RE_IMAGE.search(style_tag["style"])
            if found:
                image = found.group(2).strip()
                if image.startswith("/"):
                    image = BASE + image
        cards.append({
            "url": href if href.startswith("http") else BASE + href,
            # clean() срезает навигационные префиксы и превращает «АФИША ФИЛЬМОВ»
            # в «ФИЛЬМОВ», а «МАСТЕР-КЛАССЫ» — в пустую строку: для заголовка
            # нужен clean_title().
            "title": text.clean_title(title_tag.get_text(" ", strip=True)) if title_tag else "",
            "label": text.clean(label_tag.get_text(" ", strip=True)) if label_tag else "",
            "image": image,
            "text": "",
        })
    return cards


def _detail(url: str) -> tuple[str, list[str], str]:
    """Заголовок, подписи из шапки события и текст анонса."""
    page = http.get_text(url, ttl=6 * 3600)
    if not page:
        return "", [], ""
    soup = bs4.BeautifulSoup(page, "html.parser")

    h1 = soup.select_one("h1")
    title = text.clean_title(h1.get_text(" ", strip=True)) if h1 else ""

    labels = [
        text.clean(tag.get_text(" ", strip=True))
        for tag in soup.select(".top-meta .font_14")
    ]
    labels = [label for label in labels if label]

    intro = soup.select_one(".introtext")
    body = text.clean(intro.get_text(" ", strip=True)) if intro else ""
    return title, labels, body


def _date_variants(label: str, body: str) -> list[str]:
    """Подпись → список строк дат: их бывает две, а иногда дата только в тексте."""
    label = text.clean(label or "")

    if label:
        if _RE_DAILY.search(label):
            return ["постоянно"]
        if "кажд" in label.lower():
            return [label]
        two = _RE_TWO_DATES.search(label)
        if two:
            month = two.group(3)
            return [f"{two.group(1)} {month}", f"{two.group(2)} {month}"]
        month_only = _RE_MONTH_ONLY.match(label)
        if month_only:
            return [_month_range(month_only.group(1))]
        if _RE_DAY.search(label):
            return [label]

    if _RE_DAILY.search(body or ""):
        return ["постоянно"]
    day = _RE_DAY.search(body or "")
    if day:
        return [day.group(0)]
    return []


def _month_range(word: str) -> str:
    """«в ноябре» → «1–30 ноября»: событие идёт весь месяц."""
    key = word.lower().strip()
    month_name = _MONTH_NOM.get(key)
    num = _MONTH_NUM.get(key[:3])
    if not month_name or not num:
        return ""
    last = calendar.monthrange(dates.date.today().year, num)[1]
    return f"1–{last} {month_name}"


def _time(date_raw: str, body: str) -> str:
    """Время из подписи («13 ноября в 19:00») или из первых строк анонса."""
    own = _RE_TIME.search(date_raw or "")
    if own:
        return f"{int(own.group(1)):02d}:{own.group(2)}"
    if "постоянно" in date_raw:
        return ""
    found = _RE_TIME.search((body or "")[:400])
    if found:
        return f"{int(found.group(1)):02d}:{found.group(2)}"
    return ""
