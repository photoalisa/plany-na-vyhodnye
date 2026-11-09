"""Музей Новосибирска (m-nsk.ru) — выставки.

На листинге дат нет вообще: только обложка и заголовок. Дата живёт внутри
страницы выставки одной строкой «Выставка будет работать до 31 декабря 2026
года», причём вне блока main. Поэтому:

  * ходим в каждую карточку;
  * ищем «до <дата>» по всей странице;
  * если дата окончания в будущем — выставка идёт сейчас, начало = сегодня;
  * если дата в прошлом или её нет — карточка пропускается (лучше пусто,
    чем выдуманный период).
"""

from __future__ import annotations

import re
from datetime import date

import bs4

from common import dates, http, text

BASE = "https://m-nsk.ru"
LISTING = f"{BASE}/exibitions/vystavki-v-muzee"

_MONTHS = (
    "января|февраля|марта|апреля|мая|июня|июля|августа|"
    "сентября|октября|ноября|декабря"
)
_RE_UNTIL = re.compile(rf"до\s+(\d{{1,2}}\s+(?:{_MONTHS})(?:\s+\d{{4}})?)", re.IGNORECASE)
_RE_ADDRESS = re.compile(
    r"по\s+адресу\s+([А-ЯЁа-яё0-9][^.;!?]{2,60}?,\s*\d+[А-Яа-яA-Za-z0-9/\-]*)",
    re.IGNORECASE,
)
# Филиалы музея: по адресу понятно, в каком здании идёт выставка.
_BRANCHES = {
    "обская": "Музей на набережной",
}


def _card_title(card) -> str:
    for sel in ("h3", "h4", "h2"):
        node = card.select_one(sel)
        if node and node.get_text(strip=True):
            return text.clean(node.get_text(" ", strip=True))
    img = card.find("img")
    if img and img.get("alt"):
        return text.clean(img["alt"])
    return ""


def _listing() -> list[dict]:
    html = http.get_text(LISTING, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")

    cards: list[dict] = []
    seen: set[str] = set()
    for card in soup.select("a.isotope-item[href], a.has-post-thumbnail[href]"):
        href = (card.get("href") or "").strip()
        if not href:
            continue
        url = href if href.startswith("http") else BASE + href
        if url in seen:
            continue
        title = _card_title(card)
        if not title:
            continue
        seen.add(url)
        cards.append({"title": title, "source_url": url})
    return cards


def _until_in(blob: str) -> str:
    """Первое «до <дата>» в тексте, с приоритетом варианта с годом."""
    matches = _RE_UNTIL.findall(blob or "")
    if not matches:
        return ""
    with_year = [m for m in matches if re.search(r"\d{4}", m)]
    return (with_year or matches)[0]


def _find_until(soup) -> str:
    """Ищем дату сначала в блоке с заголовком выставки, потом по всей странице.

    Сайт выводит «похожие выставки» и общий список, где тоже есть «до …» —
    поэтому приоритет у варианта с годом, а не у первого попавшегося.
    """
    candidates: list[str] = []
    heading = soup.find(["h1", "h2"])
    block = heading
    for _ in range(5):
        if block is None:
            break
        found = _until_in(text.clean(block.get_text(" ", strip=True)))
        if found:
            candidates.append(found)
        block = block.parent

    page_level = _until_in(text.clean(soup.get_text(" ", strip=True)))
    if page_level:
        candidates.append(page_level)

    with_year = [c for c in candidates if re.search(r"\d{4}", c)]
    return (with_year or candidates)[0] if candidates else ""


def _place(soup) -> tuple[str, str]:
    """Адрес и филиал из текста страницы: «работает по адресу Обская, 4».

    У музея один сайт на все филиалы, и выставки идут в разных зданиях.
    Раньше всем карточкам подставлялся адрес главного здания на Советской,
    из-за чего выставка на набережной показывалась не по тому адресу.
    """
    blob = soup.get_text(" ", strip=True)
    match = _RE_ADDRESS.search(blob)
    if not match:
        return "ул. Советская, 24", "Музей Новосибирска"

    raw = match.group(1).strip().rstrip(".")
    venue = "Музей Новосибирска"
    for pattern, name in _BRANCHES.items():
        if pattern in raw.lower():
            venue = name
            break
    return text.normalize_address(raw), venue


def _detail(card: dict, today: date) -> dict | None:
    html = http.get_text(card["source_url"], ttl=24 * 3600)
    if not html:
        return None

    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    until = _find_until(soup)
    if not until:
        return None
    period = dates.parse_period(until)
    end = period.end or period.start
    if not end or end < today:
        return None

    main = soup.select_one("main") or soup.select_one("#main") or soup.body or soup
    description = ""
    for p in main.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) >= 60 and not text.is_boilerplate(value):
            description = value
            break

    image_url = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = og["content"]

    address, venue = _place(soup)

    return {
        "source": "Музей Новосибирска",
        "title": card["title"],
        "date_raw": f"до {until}",
        "start_override": today.isoformat(),
        "end_override": end.isoformat(),
        "kind_hint": "long_term",
        "source_url": card["source_url"],
        "image_url": image_url,
        "text_raw": description or f"Выставка Музея Новосибирска: «{card['title']}». Работает до {until}.",
        "venue": venue,
        "address": address,
        "tags": "выставки",
        "category": "выставка",
    }


def fetch() -> list[dict]:
    today = date.today()
    cards = _listing()
    print(f"  карточек: {len(cards)}")

    events: list[dict] = []
    for item in http.parallel(lambda c: _detail(c, today), cards, workers=5):
        if item:
            events.append(item)
    print(f"  идут сейчас: {len(events)}")
    return events
