"""Бердский историко-художественный музей (музейбердска.рф).

Даты на сайте есть только у части карточек: либо диапазон
«09.11.2026 20:00:00 - 16.11.2026 20:00:00», либо одна дата открытия.
У экспозиций дат нет вовсе. Отсюда правило: если даты окончания нет,
событие считается постоянным — музей показывает его, пока не снимет.

График работы решает, попадать ли музею в каталог выходных:
  вт, ср, чт, пт — с 10:00 до 18:00;
  сб             — с 10:00 до 16:00;
  вс, пн         — выходной.
То есть на выходных музей открыт только в субботу. Дни открытия уходят
в поле open_days, остальные дни сборка отбрасывает.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import bs4

from common import dates, http, text

BASE = "https://xn--80abjcbjpll9bmq.xn--p1ai"
# Оба раздела перечислены в меню на каждой странице, поэтому ссылки
# различаем по пути: выставки и постоянные экспозиции.
SECTIONS = ("/ru/exhibition/", "/ru/exposition/")

VENUE = "Бердский историко-художественный музей"
ADDRESS = "ул. Спортивная, 11/1"
GEO_AREA = "Бердск"

_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}
_RE_DAY = re.compile(r"\d{2}\.\d{2}\.\d{4}")
_RE_URL = re.compile(r"/ru/(?:exhibition|exposition)/[^/]+/$")
_RE_BG = re.compile(r"url\(\s*['\"]?([^'\")]+)")
# В начале описания сайт выводит имена файлов фотографий.
_RE_FILE = re.compile(
    r"^(?:\s*\S+\.(?:jpe?g|png|gif|webp|bmp)(?=\s|$))+", re.IGNORECASE)
_RE_ADDRESS = re.compile(r"по\s+адресу[:\s]+([^.;]{5,70})", re.IGNORECASE)

_open_days_cache: str = ""


def _clean(value: str) -> str:
    return text.clean(re.sub(r"\s+", " ", value or ""))


def _absolute(src: str) -> str:
    src = (src or "").strip()
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    return urljoin(BASE, src)


def _schedule(soup: bs4.BeautifulSoup) -> str:
    """Текст «Режим работы» со страницы события."""
    node = soup.select_one("span.pt-4.d-block.t-copy")
    if node is not None:
        return _clean(node.get_text(" ", strip=True))
    for span in soup.find_all("span"):
        if "режим работы" in span.get_text(" ", strip=True).lower():
            parent = span.parent
            return _clean(parent.get_text(" ", strip=True)) if parent else ""
    return ""


def _open_days(schedule: str) -> str:
    """Дни недели, когда музей открыт: «1,2,3,4,5».

    Строка графика разбита на части перечислением дней; «выходной день»
    в части означает, что перечисленные в ней дни закрыты. Санитарный
    день пропускаем: он не делает четверг выходным.
    """
    if not schedule:
        return "1,2,3,4,5"
    opened: set[int] = set()
    closed: set[int] = set()
    for chunk in re.split(r"(?<=[.;])\s+", schedule):
        low = chunk.lower()
        if "санитарн" in low:
            continue
        days = {num for word, num in _WEEKDAYS.items() if word in low}
        if not days:
            continue
        if "выходн" in low:
            closed |= days
        else:
            opened |= days
    if not opened:
        return "1,2,3,4,5"
    return ",".join(str(day) for day in sorted(opened - closed))


def _raw_date(card_text: str) -> tuple[str, str]:
    """Строка дат для таблицы и подсказка вида события.

    Диапазон -> «с 09.11.2026 по 16.11.2026».
    Одна дата или ничего -> «постоянно»: сайт показывает выставку без
    даты окончания, значит она идёт до снятия.
    """
    found = _RE_DAY.findall(card_text or "")
    if len(found) >= 2:
        return f"с {found[0]} по {found[1]}", "long_term"
    return "постоянно", "permanent"


def _cards(html_doc: str, section: str) -> list[dict]:
    """Карточки раздела: ссылка, название, даты и афиша из плитки."""
    soup = bs4.BeautifulSoup(html_doc, "html.parser")
    cards: list[dict] = []
    seen: set[str] = set()

    for block in soup.select("div.iblock-list-item-wrap"):
        link = block.find("a", href=_RE_URL)
        if link is None:
            continue
        href = link["href"]
        if section not in href or href in seen:
            continue
        seen.add(href)

        title_node = block.select_one("p.t-1 a") or link
        title = _clean(title_node.get_text(" ", strip=True))
        if not title:
            continue

        stamp = block.select_one("p.t--1.c-text-secondary")
        raw_date, hint = _raw_date(stamp.get_text(" ", strip=True) if stamp else "")

        image = ""
        cover = block.select_one("[data-bg]")
        if cover is not None:
            image = _absolute(cover.get("data-bg") or "")

        cards.append({
            "url": urljoin(BASE, href),
            "title": title,
            "date_raw": raw_date,
            "kind_hint": hint,
            "image": image,
        })
    return cards


def _leading_echo(value: str, title: str) -> str:
    """Убирает в начале описания обрубок названия без слова «выставка».

    Текст на сайте набран как «Выставка «Первая Бердская» Первая Бердская
    В историко-художественном музее…»: после заголовка остаётся повтор.
    Режем его только тогда, когда дальше начинается новое предложение,
    иначе от фразы останется продолжение с маленькой буквы.
    """
    words = {word.strip("«»\".,—–-:").lower() for word in title.split()}
    words.discard("")
    parts = value.split()
    cut = 0
    while cut < len(parts) and cut < 8:
        word = parts[cut].strip("«»\".,—–-:").lower()
        if not word or word not in words:
            break
        cut += 1
    if not cut or cut >= len(parts):
        return value
    rest = " ".join(parts[cut:]).lstrip(" .—–-:,")
    return rest if rest[:1].isupper() else value


def _detail(url: str, image_fallback: str) -> tuple[str, str, str, str]:
    """Описание, афиша, график работы и адрес со страницы события."""
    html_doc = http.get_text(url, ttl=6 * 3600)
    if not html_doc:
        return "", image_fallback, "", ""
    soup = bs4.BeautifulSoup(html_doc, "html.parser")
    schedule = _schedule(soup)

    image = ""
    for node in soup.select("[style*=background-image]"):
        match = _RE_BG.search(node.get("style") or "")
        if match:
            image = _absolute(match.group(1))
            if image:
                break

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    container = soup.select_one("main") or soup.body or soup
    flat = _clean(container.get_text(" ", strip=True))

    # Дальше в разметке идут телефон, график, кнопки «Поделиться» и ссылки
    # на соседние выставки: к описанию они не относятся.
    for marker in ("Телефон для справок", "Режим работы", "Поделиться"):
        index = flat.find(marker)
        if index > 0:
            flat = flat[:index]

    heading = soup.find("h1")
    title = ""
    if heading is not None:
        title = _clean(heading.get_text(" ", strip=True))

    # Название повторяется в описании дважды, а между повторами стоят имена
    # файлов фотографий. Снимаем и то и другое, пока начало не устоится.
    for _ in range(4):
        changed = False
        if title and flat.lower().startswith(title.lower()):
            rest = flat[len(title):].lstrip(" .—–-:,")
            if rest[:1].isupper() or _RE_FILE.match(rest):
                flat, changed = rest, True
        stripped = _RE_FILE.sub("", flat).lstrip(" .—–-:,")
        if stripped != flat:
            flat, changed = stripped, True
        if not changed:
            break
    flat = _leading_echo(flat, title)

    # Адрес ищем до обрезки: строка «по адресу …» часто стоит в конце.
    address = ""
    match = _RE_ADDRESS.search(flat)
    if match:
        address = _clean(match.group(1)).strip(" ,")

    # Описание режем по концу предложения: обрыв на полуслове в карточке
    # выглядит как ошибка вёрстки.
    if len(flat) > 1400:
        cut = flat.rfind(". ", 0, 1400)
        flat = flat[:cut + 1] if cut > 600 else flat[:1400]

    return _clean(flat), image or image_fallback, schedule, address


def fetch() -> list[dict]:
    global _open_days_cache

    seen: set[str] = set()
    cards: list[dict] = []
    for section in SECTIONS:
        html_doc = http.get_text(BASE + section, ttl=6 * 3600)
        if not html_doc:
            print(f"  [!] {section} недоступен")
            continue
        part = _cards(html_doc, section)
        print(f"  {section}: карточек {len(part)}")
        for card in part:
            if card["url"] in seen:
                continue
            seen.add(card["url"])
            cards.append(card)

    if not cards:
        return []

    events: list[dict] = []
    virtual = 0
    off_weekend = 0

    for card in cards:
        if "виртуальн" in card["title"].lower():
            # Виртуальную выставку смотрят онлайн, график музея к ней
            # отношения не имеет — в каталог выходных она не попадает.
            virtual += 1
            continue

        if card["kind_hint"] != "permanent" and not dates.is_weekend_relevant(card["date_raw"]):
            off_weekend += 1
            continue

        body, image, schedule, address = _detail(card["url"], card["image"])
        if not _open_days_cache and schedule:
            _open_days_cache = _open_days(schedule)

        events.append({
            "source": "Бердский музей",
            "title": card["title"],
            "date_raw": card["date_raw"],
            "kind_hint": card["kind_hint"],
            "source_url": card["url"],
            "text_raw": body or f"{card['title']} — выставка Бердского историко-художественного музея.",
            "venue": VENUE,
            "address": address or ADDRESS,
            "district": "",
            "geo_area": GEO_AREA,
            "category": "выставка",
            "tags": "выставки, музеи, бердск",
            "open_days": _open_days_cache or "1,2,3,4,5",
            "image_url": image,
        })

    print(f"  виртуальных пропущено: {virtual}; не на выходные: {off_weekend}")
    print(f"  дни открытия: {_open_days_cache or '1,2,3,4,5'}")
    return events
