"""Общая лента организатора на Timepad: карточки обычным HTML, без токена.

Лента `https://<org>.timepad.ru/events/` отдаёт постраничный список карточек
`.t-card_event`: название, ссылка на событие, строка даты, короткое описание
и постер фоном. Прошедшие карточки помечены `t-card_event__passed`.

Вынесено из `parsers/kartamira.py`, когда появился второй организатор на
Timepad: логика одна, различаются только адрес ленты и площадка.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

_RE_BG = re.compile(r"url\((['\"]?)([^'\")]+)\1\)")

# С адресов хостингов (раннер GitHub) Timepad отдавал пустую страницу, хотя
# браузер с того же адреса получает карточки. Отправляем полный набор
# заголовков навигации: проверяем, дело в запросе или в самом адресе.
BROWSER_HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


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


def fetch_org(base: str, *, source: str, venue: str, address: str,
              district: str = "", afisha_path: str = "/events/") -> list[dict]:
    url = base.rstrip("/") + afisha_path
    html = http.get_text(url, ttl=1800, headers=BROWSER_HEADERS)
    if not html:
        # Из облака Timepad иногда отвечает заглушкой: на раннере GitHub
        # источник молча отдавал ноль. Повторяем без кеша.
        print("  [!] лента не загрузилась, повторяю без кеша")
        html = http.get_text(url, timeout=45, ttl=0, refresh=True,
                             headers=BROWSER_HEADERS)
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
            "source": source,
            "title": text.clean_title(link.get_text(" ", strip=True)),
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": _image(card),
            "venue": venue,
            "address": address,
            "district": (district
                         or text.guess_district(address, strict=True)
                         or text.guess_district(description, strict=True)),
            "text_raw": description,
            # Цены в ленте нет: сайт покажет «Цена на сайте».
            "price_text": "",
        })
    return out
