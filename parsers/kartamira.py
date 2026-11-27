"""Книжный магазин «Карта мира» (kartamira.timepad.ru) — афиша на Timepad.

Лента организатора на Timepad отдаёт карточки обычным HTML, без токена API:
`https://kartamira.timepad.ru/events/`. В карточке есть название, ссылка на
событие, строка даты («с 15:00 7 ноября до 18:00 28 ноября» или
«26 ноября c 20:00 до 22:00»), короткое описание и постер фоном.

Прошедшие карточки помечены классом `t-card_event__passed` — их пропускаем.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://kartamira.timepad.ru"
AFISHA = f"{BASE}/events/"

VENUE = "Книжный магазин «Карта мира»"
ADDRESS = "ул. Красноярская, 3"

_RE_BG = re.compile(r"url\((['\"]?)([^'\")]+)\1\)")


def _date_raw(card: bs4.element.Tag) -> str:
    """Строка даты без города, пометки «идёт сейчас» и описания.

    Описание вложено в тот же `.t-card_event__info`, и без вырезания оно
    попадало в `date_raw` вместе с датами из текста анонса.
    """
    info = card.select_one(".t-card_event__info")
    if info is None:
        return ""
    node = bs4.BeautifulSoup(str(info), "html.parser")
    for junk in node.select("span, img, a, .t-description"):
        junk.decompose()
    return text.clean(node.get_text(" ", strip=True))


def _image(card: bs4.element.Tag) -> str:
    poster = card.select_one(".__poster")
    if poster is None:
        return ""
    found = _RE_BG.search(poster.get("style") or "")
    return found.group(2).strip() if found else ""


def fetch() -> list[dict]:
    html = http.get_text(AFISHA, ttl=1800)
    if not html:
        # Timepad из облака иногда отвечает не страницей, а заглушкой:
        # на GitHub-раннере 27.11 источник отдал ноль. Повторяем без кеша.
        print("  [!] лента не загрузилась, повторяю без кеша")
        html = http.get_text(AFISHA, timeout=45, ttl=0, refresh=True)
    if not html:
        print("  [!] лента недоступна: пустой ответ")
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select(".t-card_event")
    print(f"  карточек: {len(cards)} (байт: {len(html)})")
    if not cards:
        # Молчаливый ноль неотличим от «событий нет»: пишем, что пришло.
        blok = "блок" if "t-card_event" in html else "нет разметки карточек"
        print(f"  [!] разметка не найдена: {blok}; первые байты: {html[:120]!r}")
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        if "t-card_event__passed" in (card.get("class") or []):
            continue
        link = card.select_one("h3 a[href]")
        if link is None:
            continue
        source_url = (link.get("href") or "").strip()
        if not source_url or source_url in seen:
            continue

        date_raw = _date_raw(card)
        if not date_raw or not dates.is_weekend_relevant(date_raw):
            continue

        seen.add(source_url)
        description = ""
        desc = card.select_one(".t-description")
        if desc is not None:
            description = text.clean(desc.get_text(" ", strip=True))
        # У части карточек вместо описания стоит только адрес («Красноярская, 3»).
        # Такая строка не текст события: подставляем название, иначе валидатор
        # отбрасывает карточку как пустую.
        if len(description) < 40:
            description = ""

        out.append({
            "source": "Карта мира",
            "title": text.clean_title(link.get_text(" ", strip=True)),
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": _image(card),
            "venue": VENUE,
            "address": ADDRESS,
            # Улицы нет в справочнике улиц: район останется пустым, пока
            # его не подскажет текст описания.
            "district": text.guess_district(description, strict=True),
            "text_raw": description,
            # Цены в ленте нет: в карточке будет «Уточняйте на сайте».
            "price_text": "",
        })
    return out
