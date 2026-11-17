"""ЦБС Центрального округа (sshbn.ru) — афиша библиотек.

Здесь важная особенность: страницы отдельных мероприятий отвечают кодом
500, но приходят целиком — с описанием, адресом, пометкой о регистрации,
ценой и возрастом. Поэтому страницу читаем в обход проверки статуса:
иначе от афиши остались бы только название и дата из списка.

Афиша лежит одной страницей без постраничного вывода: 22 карточки, из них
на конкретные выходные попадают обычно пять-шесть. Глубоко открываем
только их.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import bs4
import requests

from common import dates, http, text

BASE = "https://sshbn.ru"
AFISHA = f"{BASE}/chitatelyam/afisha/"

VENUE_FALLBACK = "Библиотеки ЦБС Центрального округа"

_RE_SPACES = re.compile(r"\s+")
_RE_AGE = re.compile(r"(?<!\d)(\d{1,2})\s*\+")

# Раздел «Афиша» — это лекции, встречи клубов и мастерские; категорию
# ставим по названию, чтобы фильтры на сайте работали.
_CATEGORIES = (
    ("мастер-класс", "мастер-класс"),
    ("мастерская", "мастер-класс"),
    ("лекц", "лекция"),
    ("концерт", "концерт"),
    ("спектакл", "спектакль"),
    ("выставк", "выставка"),
    ("экскурс", "экскурсия"),
    ("игра", "встреча"),
    ("игросмотр", "встреча"),
    ("клуб", "встреча"),
    ("встреч", "встреча"),
)
_TAGS = "лекции, встречи, библиотеки"


def _clean(value: str) -> str:
    return text.clean(_RE_SPACES.sub(" ", value or ""))


def _hard_page(url: str, timeout: int = 30) -> str:
    """Страница мероприятия без проверки кода ответа.

    Сайт отдаёт 500 вместе с полным содержимым карточки — ошибка на стороне
    шаблона, а не в данных. Общая сессия проекта считает 500 сбоем и после
    повторов выбрасывает RetryError, поэтому здесь отдельный запрос.
    """
    for attempt in range(2):
        try:
            response = requests.get(
                url, timeout=timeout, headers={"User-Agent": http.USER_AGENT})
        except requests.RequestException as e:
            print(f"  [-] {url}: {type(e).__name__}")
            continue
        if len(response.text or "") > 5000:
            return response.text
    return ""


def _category(title: str) -> str:
    low = title.lower()
    for marker, value in _CATEGORIES:
        if marker in low:
            return value
    return "встреча"


def _cards(soup: bs4.BeautifulSoup) -> list[dict]:
    """Карточки афиши: ссылка, название, строка даты и площадка."""
    cards: list[dict] = []
    seen: set[str] = set()

    for item in soup.select("div.item"):
        link = item.select_one("div.title a[href]")
        preview = item.select_one("div.previewtext")
        if link is None or preview is None:
            continue
        href = link["href"]
        if href in seen:
            continue
        seen.add(href)

        lines = [_clean(p.get_text(" ", strip=True)) for p in preview.find_all("p")]
        lines = [line for line in lines if line]
        if not lines:
            continue

        image = item.select_one("img")
        cards.append({
            "url": urljoin(BASE, href),
            "title": _clean(link.get_text(" ", strip=True)),
            "when": lines[0],
            "where": lines[1] if len(lines) > 1 else "",
            "image": urljoin(BASE, (image.get("src") or "")) if image else "",
        })
    return cards


def _detail(url: str, title: str) -> dict:
    """Поля карточки: описание, дата, адрес, цена и возраст."""
    html_doc = _hard_page(url)
    if not html_doc:
        return {}
    soup = bs4.BeautifulSoup(html_doc, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    container = soup.select_one("main") or soup.body or soup
    flat = _clean(container.get_text(" ", strip=True))

    result = {"text": "", "when": "", "address": "", "venue": "",
              "age": "", "free": False, "price": ""}

    # Ошибка шаблона внизу страницы к мероприятию не относится.
    flat = flat.split("При выполнении скрипта возникла ошибка")[0]

    # Перед описанием идут меню, хлебные крошки и список конкурсов: описание
    # начинается со своего заголовка, поэтому отсчитываем от него.
    start = flat.find(title)
    if start < 0:
        start = flat.find(title[:30])
    if start > 0:
        flat = flat[start + len(title):].strip(" .—–-:")

    stamp = flat.find("Дата и время:")
    if stamp > 0:
        result["text"] = flat[:stamp].strip(" .—–-")
        tail = flat[stamp + len("Дата и время:"):]
        stop = re.search(r"Адрес:|Место:|Необходима|Регистрация|Вход", tail)
        result["when"] = _clean(tail[:stop.start()] if stop else tail[:80]).strip(" .")

    marker = flat.find("Адрес:")
    if marker >= 0:
        tail = flat[marker + len("Адрес:"):]
        stop = re.search(r"Необходима|Регистрация|Вход|Стоимость|Цена|\d{1,2}\+", tail)
        chunk = _clean(tail[:stop.start()] if stop else tail[:160]).strip(" .")
        # «Красный проспект, 83. Модельная библиотека им. …» — адрес и
        # площадка идут одной строкой через точку.
        parts = [part.strip(" .") for part in chunk.split(". ") if part.strip(" .")]
        if parts:
            result["address"] = parts[0]
            result["venue"] = " ".join(parts[1:])

    if re.search(r"вход\s+свободн|бесплатн", flat, re.IGNORECASE):
        result["free"] = True

    # «Стоимость билета: 200 руб. Действует «Пушкинская карта»» — цена есть
    # только у платных мастерских, у лекций и клубов пишут «Вход свободный».
    money = re.search(r"(?:Стоимость(?: билета)?|Цена)\s*:\s*([^.]{1,60})", flat, re.IGNORECASE)
    if money:
        found = re.search(r"\d[\d\s]*\s*(?:руб|₽)", _clean(money.group(1)), re.IGNORECASE)
        if found:
            result["price"] = _clean(found.group(0)).replace("₽", "руб").strip()

    age = _RE_AGE.search(flat[-260:])
    if age:
        result["age"] = f"{age.group(1)}+"

    if result["text"]:
        cut = result["text"].rfind(". ", 0, 1200)
        result["text"] = result["text"][:cut + 1] if cut > 400 else result["text"][:1200]
    return result


def fetch() -> list[dict]:
    html_doc = http.get_text(AFISHA, ttl=6 * 3600)
    if not html_doc:
        print("  [!] Афиша ЦБС недоступна")
        return []
    cards = _cards(bs4.BeautifulSoup(html_doc, "html.parser"))
    print(f"  карточек в афише: {len(cards)}")

    events: list[dict] = []
    for card in cards:
        when = card["when"]
        if not when or not dates.is_weekend_relevant(when):
            continue

        detail = _detail(card["url"], card["title"])
        raw_date = detail.get("when") or when
        venue = card["where"] or detail.get("venue") or VENUE_FALLBACK

        events.append({
            "source": "ЦБС Центрального округа",
            "title": card["title"],
            "date_raw": raw_date,
            "source_url": card["url"],
            "text_raw": detail.get("text") or f"{card['title']}. {when}. {venue}.",
            "venue": venue,
            "address": detail.get("address", ""),
            "district": "",
            "category": _category(card["title"]),
            "tags": _TAGS,
            "age_text": detail.get("age", ""),
            "price_source": "Вход свободный" if detail.get("free") else detail.get("price", ""),
            "image_url": card["image"],
        })

    print(f"  на выходные: {len(events)}")
    return events
