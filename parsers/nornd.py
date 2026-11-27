"""Российско-Немецкий Дом — афиша на одной странице.

Сайт на WordPress: афиша это обычная страница, внутри которой события
разложены заголовками `h2`, без карточек и без дат в разметке. Даты стоят
в тексте («Мероприятие состоится 29 ноября 2026 года с 12:00 до 16:00»,
«6 декабря в 14:00 | РСКЦ им. Жданько, г. Черепаново»), поэтому заголовок
делит страницу на блоки, а дату и площадку ищем в тексте блока.

У одного блока бывает несколько показов (спектакль играют в двух городах).
В таблицу идёт один показ — ближайший к выходным: `id` считается от
названия и ссылки, поэтому два показа с одной страницей склеились бы
в дубль. Остальные даты остаются в описании.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://nornd.ru"
AFISHA = f"{BASE}/afisha-rossijsko-nemetskogo-doma/"

VENUE = "Новосибирский областной Российско-Немецкий Дом"
ADDRESS = "ул. Ядринцевская, 68"
DISTRICT = "Центральный"

# Заголовки на странице, которые событиями не являются.
SKIP_TITLES = re.compile(
    r"поиск публикаций|пушкинск|решаем вместе|слабовидящ|найти",
    re.IGNORECASE,
)

_RE_SHOW = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s+({dates.MONTH_PATTERN})"
    rf"(?:\s+(\d{{4}}))?(?:\s+года)?\s*,?\s*(?:в|с)\s+(\d{{1,2}}):(\d{{2}})",
    re.IGNORECASE,
)
_RE_END_TIME = re.compile(r"\s*до\s+(\d{1,2}):(\d{2})")
_RE_CITY = re.compile(r"\bг\.\s*([А-ЯЁ][а-яё-]+)")
_RE_ADDRESS = re.compile(r"\(([^)]{5,80})\)")
_RE_AGE = re.compile(r"(?<!\d)(\d{1,2})\+")
_RE_PRICE = re.compile(r"(\d[\d\s]{2,7})\s*(?:руб|₽)", re.IGNORECASE)
_RE_FREE = re.compile(r"вход\s+[^.]{0,40}свободн|свободный\s+вход|бесплатн", re.IGNORECASE)


def _blocks(soup: bs4.BeautifulSoup) -> list[dict]:
    """Делит содержимое страницы на блоки по `h2`."""
    content = soup.select_one(".entry-content")
    if content is None:
        return []

    blocks: list[dict] = []
    current: dict | None = None
    for node in content.children:
        name = getattr(node, "name", None)
        if name in ("h2", "h3"):
            title = text.clean(node.get_text(" ", strip=True))
            current = None if not title or SKIP_TITLES.search(title) else {
                "title": title, "text": [], "image": "",
            }
            if current is not None:
                blocks.append(current)
            continue
        if current is None:
            continue
        if name is None:
            value = str(node).strip()
            if value:
                current["text"].append(value)
            continue
        if not current["image"] and name == "figure":
            img = node.find("img")
            if img:
                src = (img.get("src") or img.get("data-src") or "").strip()
                if src:
                    current["image"] = src
        current["text"].append(node.get_text(" ", strip=True))

    for block in blocks:
        block["text"] = " ".join(" ".join(block["text"]).split())
    return blocks


def _place(tail: str) -> dict:
    """Площадка и адрес из хвоста показа: «| РСКЦ им. Жданько, г. Черепаново (…)»."""
    if not tail or "Российско-Немецк" in tail:
        return {"venue": VENUE, "address": ADDRESS, "district": DISTRICT,
                "geo_area": "Новосибирск", "is_outside_city": False}

    name = text.clean(tail.split(",")[0])[:80]
    address = ""
    found = _RE_ADDRESS.search(tail)
    if found:
        address = text.clean(found.group(1))

    city = ""
    found_city = _RE_CITY.search(tail)
    if found_city:
        city = found_city.group(1)

    if city and city.lower() != "новосибирск":
        return {"venue": name or city, "address": address, "district": "",
                "geo_area": city, "is_outside_city": True}
    return {"venue": name or VENUE, "address": address or ADDRESS,
            "district": DISTRICT, "geo_area": "Новосибирск",
            "is_outside_city": False}


def _category(title: str) -> str:
    low = title.lower()
    for word, value in (
        ("спектакл", "спектакль"), ("концерт", "концерт"), ("ярмарк", "ярмарка"),
        ("выставк", "выставка"), ("мастер-класс", "мастер-класс"),
        ("лекци", "лекция"), ("экскурс", "экскурсия"), ("фестивал", "фестиваль"),
        ("показ", "кино"), ("фильм", "кино"), ("кинопоказ", "кино"),
    ):
        if word in low:
            return value
    return "событие"


def _description(block_text: str, title: str) -> str:
    """Описание без служебной строки про билеты."""
    body = block_text
    cut = body.find("Билеты – на Яндекс.Афише")
    if cut > 0:
        body = body[:cut]
    body = body.replace(title, " ", 1).strip()
    if len(body) > 900:
        body = body[:900].rsplit(" ", 1)[0] + "…"
    return body


def _shows(block: dict, today: dates.date) -> list[dict]:
    """Все показы блока: дата, время, площадка."""
    body = block["text"]
    found = list(_RE_SHOW.finditer(body))
    out: list[dict] = []
    for idx, m in enumerate(found):
        day, month, year, hour, minute = m.groups()
        start = dates._year_for_single(int(day), dates._month_from_word(month),
                                      int(year) if year else None, today)
        if not start:
            continue

        tail_end = found[idx + 1].start() if idx + 1 < len(found) else len(body)
        tail = body[m.end():min(tail_end, m.end() + 200)]

        time_text = f"{int(hour):02d}:{minute}"
        finish = _RE_END_TIME.match(tail)
        if finish:
            time_text += f"–{int(finish.group(1)):02d}:{finish.group(2)}"

        venue_part = tail.split("|", 1)[1].strip() if "|" in tail else ""
        place = _place(venue_part)
        out.append({
            "start": start,
            "date_raw": f"{start.day} {month} {start.year}, {time_text}",
            "time_text": time_text,
            **place,
        })
    return out


def fetch(today: dates.date | None = None) -> list[dict]:
    today = today or dates.date.today()
    html = http.get_text(AFISHA, timeout=30, ttl=1800)
    if not html:
        html = http.get_text(AFISHA, timeout=45, ttl=0, refresh=True)
    if not html:
        print("  [!] афиша недоступна: пустой ответ")
        return []

    soup = bs4.BeautifulSoup(html, "html.parser")
    blocks = _blocks(soup)
    print(f"  блоков: {len(blocks)} (байт: {len(html)})")

    events: list[dict] = []
    for block in blocks:
        shows = _shows(block, today)
        relevant = [s for s in shows if dates.is_weekend_relevant(s["date_raw"], today)]
        if relevant:
            show = relevant[0]
        elif shows:
            continue
        else:
            # Даты в будни или периодом: берём первую дату из текста.
            raw = dates.find_period_text(block["text"])
            if not raw or not dates.is_weekend_relevant(raw, today):
                continue
            period = dates.parse_period(raw, today)
            show = {
                "date_raw": raw, "time_text": period.time_text,
                "venue": VENUE, "address": ADDRESS, "district": DISTRICT,
                "geo_area": "Новосибирск", "is_outside_city": False,
            }

        body = block["text"]
        price_text = ""
        is_free = bool(_RE_FREE.search(body))
        if is_free:
            price_text = "Бесплатно"
        else:
            money = _RE_PRICE.search(body)
            if money:
                price_text = f"{money.group(1).strip().replace(chr(160), ' ')} ₽"

        age = _RE_AGE.search(body)

        events.append({
            "source": "РНД",
            "title": block["title"],
            "date_raw": show["date_raw"],
            "source_url": AFISHA,
            "image_url": block["image"],
            "text_raw": _description(body, block["title"]),
            "venue": show["venue"],
            "address": show["address"],
            "district": show["district"],
            "geo_area": show["geo_area"],
            "is_outside_city": show["is_outside_city"],
            "category": _category(block["title"]),
            "tags": "российско-немецкий дом",
            "age_text": f"{age.group(1)}+" if age else "",
            "price_text": price_text,
            "is_free": is_free,
        })

    print(f"  событий на выходные: {len(events)}")
    return events
