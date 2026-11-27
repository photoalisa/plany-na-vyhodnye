"""Культурный центр «Новый Акрополь» — лекции, музыкальные гостиные, клубы.

Сайт общий для всех филиалов: по умолчанию отдаёт Москву (19 карточек), и
новосибирские события приходят только с параметром `?city_ext=nsk`. Адрес
филиала один — Ядринцевская, 18, поэтому площадка и район стоят константами.

Карточка листинга: ссылка `a[href^="/events/"]`, внутри `div.a-event` — тип
мероприятия, название, число крупной цифрой, «нояб., вс», время, город и цена
строкой «450 / 300 ₽». Года в карточке нет, он выводится по календарю; адрес,
длительность и описание берутся со страницы события.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://acropolis.org.ru"
LIST = f"{BASE}/events/?city_ext=nsk"

VENUE = "Культурный центр «Новый Акрополь»"
ADDRESS = "ул. Ядринцевская, 18"
DISTRICT = "Центральный"

# Тип в карточке: «Лекция», «Философский фестиваль», «Книжный клуб».
# Значения только из справочника common/model.py, иначе категория не попадёт
# в фильтры сайта.
_CATEGORIES = (
    ("концерт", "концерт"), ("спектакл", "спектакль"), ("выставк", "выставка"),
    ("фестивал", "фестиваль"), ("лекци", "лекция"), ("мастер-класс", "мастер-класс"),
    ("клуб", "встреча"), ("экскурс", "экскурсия"), ("кинопоказ", "кино"),
    ("фильм", "кино"), ("ярмарк", "ярмарка"),
)

_RE_ADDRESS = re.compile(r"ул\.\s*[А-ЯЁ][^,.]{2,40},\s*\d+[а-яА-Я]?", re.IGNORECASE)
_RE_PRICE = re.compile(r"(\d[\d\s]{1,6})\s*/?\s*(\d[\d\s]{1,6})?\s*₽")


def _cards(soup: bs4.BeautifulSoup) -> list[bs4.element.Tag]:
    """Ссылки на события: класс карточки висит на вложенном `div`, не на `a`."""
    return [a for a in soup.select("a[href^='/events/']") if a.select_one(".a-event")]


def _category(kind: str, title: str) -> str:
    blob = f"{kind} {title}".lower()
    for marker, value in _CATEGORIES:
        if marker in blob:
            return value
    return "событие"


def _detail(url: str) -> tuple[str, str, str]:
    """Описание, афиша и адрес со страницы события."""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    image = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image = og["content"]

    for junk in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        junk.decompose()
    body = soup.select_one("main") or soup.body or soup
    plain = " ".join(body.get_text(" ", strip=True).split())

    address = ""
    found = _RE_ADDRESS.search(plain)
    if found:
        address = text.clean(found.group(0))

    description = ""
    start = plain.find("Источник:")
    if start >= 0:
        # «Источник: Insula Magica «Хроматика античности»…» — метка служебная,
        # оставляем сам текст программы.
        description = text.clean(plain[start + len("Источник:"):start + 900])
    else:
        start = plain.find("В рамках")
        if start >= 0:
            description = text.clean(plain[start:start + 900])
    return description, image, address


def fetch() -> list[dict]:
    html = http.get_text(LIST, timeout=30, ttl=1800)
    if not html:
        html = http.get_text(LIST, timeout=45, ttl=0, refresh=True)
    if not html:
        print("  [!] афиша недоступна: пустой ответ")
        return []

    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = _cards(soup)
    print(f"  карточек для Новосибирска: {len(cards)} (байт: {len(html)})")
    if not cards:
        print("  [i] у филиала нет ближайших событий")
        return []

    events: list[dict] = []
    for card in cards:
        title_el = card.select_one(".a-event-title")
        day_el = card.select_one(".a-text-h1")
        when_el = card.select_one(".mon_d_n")
        if not (title_el and day_el and when_el):
            continue

        title = text.clean(title_el.get_text(" ", strip=True))
        kind_el = card.select_one(".a-event-smtitle")
        kind = text.clean(kind_el.get_text(" ", strip=True)) if kind_el else ""

        meta = [" ".join(s.get_text(" ", strip=True).split())
                for s in card.select(".a-event-text .d-flex.ml-5 span")]
        time_text = meta[0] if meta else ""
        price_raw = meta[-1] if len(meta) > 1 else ""

        date_raw = f"{text.clean(day_el.get_text(' ', strip=True))} " \
                   f"{text.clean(when_el.get_text(' ', strip=True))} {time_text}"
        if not dates.is_weekend_relevant(date_raw):
            continue

        url = BASE + (card.get("href") or "")
        description, image, address = _detail(url)

        price_text = ""
        is_free = "свободн" in price_raw.lower() or "бесплатн" in price_raw.lower()
        if is_free:
            price_text = "Бесплатно"
        else:
            money = _RE_PRICE.search(price_raw)
            if money:
                # «450 / 300 ₽» — полный и льготный: на сайте показываем меньший.
                value = money.group(2) or money.group(1)
                price_text = f"от {value.strip()} ₽"

        events.append({
            "source": "Акрополь",
            "title": title,
            "date_raw": date_raw,
            "source_url": url,
            "image_url": image,
            "text_raw": description or f"{kind or 'Встреча'} в «Новом Акрополе»: «{title}».",
            "venue": VENUE,
            "address": address or ADDRESS,
            "district": DISTRICT,
            "category": _category(kind, title),
            "tags": text.clean(kind),
            "price_text": price_text,
            "is_free": is_free,
        })

    print(f"  событий на выходные: {len(events)}")
    return events
