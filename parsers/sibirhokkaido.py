"""ЦТиПС «Сибирь-Хоккайдо» — центр туризма и побратимских связей.

Сайт на WordPress с The Events Calendar, но REST-API календаря отдаёт
`total: 0` даже для будущих событий: данные есть только в разметке. Читаем
списочный вид `/events/list/` — там у каждой строки название, ссылка, период
строкой «08.10.2026 | 08:00 - 28.02.2027 | 17:00», площадка и адрес (если
место — сам центр). Описание, цена и расписание берутся со страницы события.

Две особенности источника:

  * события здесь длинные (клуб на полгода, выставка на два месяца), поэтому
    дата берётся периодом, а не днём;
  * часть событий идёт по будням («встречи каждую среду»). В календаре у них
    стоит период до февраля, и без проверки расписания такая карточка висела
    бы в каждом выпуске. Поэтому «каждую среду/четверг/…» отсеиваем, а
    субботу и воскресенье оставляем.

Часть выставок проходит на площадке-партнёре («Диалог Сибирь») — там адреса
на сайте нет, поэтому площадка пишется словами, а район остаётся пустым.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://sibirhokkaido.ru"
LIST = f"{BASE}/events/list/"

VENUE = "ЦТиПС «Сибирь-Хоккайдо»"
ADDRESS = "ул. Шевченко, 28/1"
DISTRICT = "Центральный"

_RE_PERIOD = re.compile(
    r"(\d{1,2}\.\d{1,2}\.\d{4})\s*\|\s*(\d{1,2}:\d{2})\s*[-–]\s*"
    r"(\d{1,2}\.\d{1,2}\.\d{4})\s*\|\s*(\d{1,2}:\d{2})"
)
# «каждую среду», «каждую пятницу», «по субботам»
_RE_WEEKDAY = re.compile(
    r"(?:кажд\w+|по)\s+(понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*)",
    re.IGNORECASE,
)
_WORKDAYS = ("понедельник", "вторник", "сред", "четверг", "пятниц")
_RE_PRICE = re.compile(r"(\d[\d\s]{1,6})\s*(?:руб|₽)", re.IGNORECASE)
_RE_FREE = re.compile(r"вход\s+(?:свободн|бесплатн)|свободный\s+вход|бесплатн",
                      re.IGNORECASE)
_RE_ADDRESS = re.compile(r"(?:Адрес|адрес)\s*:\s*(ул\.[^.,;]{3,60}(?:,\s*\d+[а-я]?)?)")


def _cards(soup: bs4.BeautifulSoup) -> list[bs4.element.Tag]:
    return soup.select(".tribe-events-calendar-list__event-row")


def _is_weekday_only(plain: str) -> bool:
    """Событие идёт только по будням — в выходные его в каталоге быть не должно."""
    for found in _RE_WEEKDAY.finditer(plain):
        word = found.group(1).lower()
        if word.startswith(_WORKDAYS):
            return True
    return False


def _detail(url: str) -> dict:
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return {}
    soup = bs4.BeautifulSoup(html, "html.parser")
    image = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image = og["content"]

    body = soup.select_one(".tribe-events-single-event-description") or soup.select_one("main") or soup
    for junk in body(["script", "style", "noscript"]):
        junk.decompose()
    plain = " ".join(body.get_text(" ", strip=True).split())

    address = ""
    found = _RE_ADDRESS.search(plain)
    if found:
        address = text.clean(found.group(1))

    return {"plain": plain, "image": image, "address": address}


def _category(title: str, plain: str) -> str:
    blob = f"{title} {plain[:400]}".lower()
    for marker, value in (
        ("выставк", "выставка"), ("концерт", "концерт"), ("спектакл", "спектакль"),
        ("мастер-класс", "мастер-класс"), ("мастер класс", "мастер-класс"),
        ("лекци", "лекция"), ("экскурс", "экскурсия"), ("фестивал", "фестиваль"),
        ("ярмарк", "ярмарка"), ("кинопоказ", "кино"), ("конкурс", "событие"),
        ("встреч", "встреча"), ("клуб", "встреча"),
    ):
        if marker in blob:
            return value
    return "событие"


def fetch(today: dates.date | None = None) -> list[dict]:
    today = today or dates.date.today()
    html = http.get_text(LIST, timeout=30, ttl=1800)
    if not html:
        html = http.get_text(LIST, timeout=45, ttl=0, refresh=True)
    if not html:
        print("  [!] афиша недоступна: пустой ответ")
        return []

    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = _cards(soup)
    print(f"  карточек в списке: {len(cards)} (байт: {len(html)})")
    if not cards:
        print("  [!] разметка не найдена: строк списка нет")
        return []

    events: list[dict] = []
    for card in cards:
        link = card.select_one(".tribe-events-calendar-list__event-title a")
        when_el = card.select_one(".tribe-events-calendar-list__event-datetime")
        if link is None or when_el is None:
            continue

        title = text.clean(link.get_text(" ", strip=True))
        url = (link.get("href") or "").strip()
        when = " ".join(when_el.get_text(" ", strip=True).split())
        found = _RE_PERIOD.search(when)
        if not found or not url:
            print(f"  [!] период не разобран: {when!r}")
            continue

        start_raw, start_time, end_raw, end_time = found.groups()
        raw = f"{start_raw} - {end_raw}"
        period = dates.parse_period(raw, today)
        if not period.start or period.is_past(today):
            continue

        # У длинных событий часы в календаре — служебные («08:00 - 17:00»),
        # реальный режим работы стоит в тексте («пн–сб 10:00–18:00»). Время
        # к такой дате не приписываем, иначе карточка покажет 08:00.
        long_event = period.end and (period.end - period.start).days >= 3
        date_raw = raw if long_event else f"{start_raw}, {start_time}"

        detail = _detail(url)
        plain = detail.get("plain", "")
        if _is_weekday_only(plain):
            print(f"  [-] только по будням: {title[:50]}")
            continue

        price_text = ""
        is_free = bool(_RE_FREE.search(plain))
        if is_free:
            price_text = "Бесплатно"
        else:
            money = _RE_PRICE.search(plain)
            if money:
                price_text = f"{money.group(1).strip().replace(chr(160), ' ')} ₽"

        venue_el = card.select_one(".tribe-events-calendar-list__event-venue-title")
        address_el = card.select_one(".tribe-events-calendar-list__event-venue-address")
        venue = text.clean(venue_el.get_text(" ", strip=True)) if venue_el else ""
        address = text.clean(address_el.get_text(" ", strip=True)) if address_el else ""

        if not venue:
            # Площадка-партнёр: «Центр «Диалог Сибирь» приглашает…», адреса нет.
            partner = re.search(r"Центр\s+«?([А-ЯЁ][^»,.]{2,30})»?", plain)
            venue = f"Центр «{text.clean(partner.group(1))}»" if partner else "Площадка уточняется"
            district = ""
        else:
            district = DISTRICT
            address = address or detail.get("address") or ADDRESS

        description = ""
        descr_el = card.select_one(".tribe-events-calendar-list__event-description")
        if descr_el is not None:
            description = text.clean(descr_el.get_text(" ", strip=True))
        if len(description) < 80 and plain:
            description = text.clean(plain[:900])

        events.append({
            "source": "Сибирь-Хоккайдо",
            "title": title,
            "date_raw": date_raw,
            "source_url": url,
            "image_url": detail.get("image") or "",
            "text_raw": description,
            "venue": venue,
            "address": address,
            "district": district,
            "geo_area": "Новосибирск",
            "category": _category(title, plain),
            "tags": "японская культура",
            "price_text": price_text,
            "is_free": is_free,
        })

    print(f"  событий: {len(events)}")
    return events
