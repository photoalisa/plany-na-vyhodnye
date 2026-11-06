"""Студия 84 (artweeknd.com) — текущая выставка и ближайшие события.

Сайт на Tilda Zero Block: ни фида, ни карточек в HTML, только текст блоков.

Что было сломано: порядок блоков на этих страницах необычный.
  * на странице выставки заголовок стоит ПОСЛЕ метки «текущая выставка»,
    а не перед датой;
  * на странице мероприятий блок идёт «дата → описание → заголовок»,
    то есть заголовок последний, а не первый.
Старый разбор брал строку перед датой и выдавал в заголовок «художники»,
«куратор» или служебную подпись.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://artweeknd.com"
EXHIBITION_URL = f"{BASE}/84exhibition"
EVENTS_URL = f"{BASE}/84events"

_NOISE = re.compile(
    r"(архив мероприятия|купить билет|пн-вт|ср-вс|режим работы|главная текущая выставка|"
    r"^текущая выставка$|^ближайшие события$|^мероприятия$|^архив$|^студия 84$|трц «аура»|"
    r"^этаж|^контакты$|^главная$|^меню$|^художники$|^куратор$|^саунд-дизайн$|"
    r"^партнеры выставки$|^концепция$|^реквизиты$|^устав|оферты|^инн |^огрн |^бик |"
    r"^расчетный счет|^банк получателя|^кор\. счёт|management@|@|^\+7|^за \d{4}$|"
    r"^отчет|свидетельство|уставные документы|^12\+$|meta запрещена)",
    re.IGNORECASE,
)
_RE_DATE = re.compile(
    r"(?<!\d)(\d{1,2}\.\d{1,2})(?:\s*(?:-|–|—)\s*(\d{1,2}\.\d{1,2}))?"
    r"(?:\s+в\s+(\d{1,2}:\d{2}))?"
)
_RE_DATE_TEXT = re.compile(
    r"(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря))"
    r"(?:\s*(?:-|–|—)\s*(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)))?",
    re.IGNORECASE,
)
_CONTACT_LINE = re.compile(r"(management@|^инн |^огрн |устав|свидетельство|^контакты$)", re.IGNORECASE)


_ADDRESS_LIKE = re.compile(r"^(г\.\s|ул\.|трц|пн-|ср-|этаж|2 этаж)", re.IGNORECASE)


def _looks_like_title(line: str) -> bool:
    """Заголовок, а не адрес и не служебная подпись."""
    if not line or len(line) < 5:
        return False
    if _NOISE.search(line) or _ADDRESS_LIKE.search(line):
        return False
    if "«" in line or "»" in line:
        return True
    if re.search(r"\d", line):
        return False
    return len(line) >= 8


_RE_VIEWBOX = re.compile(
    r'viewBox="\s*([\d.,\-]+)\s+([\d.,\-]+)\s+([\d.,\-]+)\s+([\d.,\-]+)'
)


def _svg_area(svg: str) -> int:
    m = _RE_VIEWBOX.search(svg)
    if not m:
        return 0
    try:
        width = float(m.group(3).replace(",", "."))
        height = float(m.group(4).replace(",", "."))
    except ValueError:
        return 0
    return int(width * height)


def _poster(html: str) -> str:
    """Афиша выставки.

    Отдельного постера у Tilda нет: заголовок выставки выложен векторной
    картинкой (у «В один день…» — SVG 4683×1383, 412 КБ), а рядом лежат
    логотипы, стрелки и линейки по 1–40 КБ. Поэтому берём самый крупный по
    площади SVG с внятным весом, и только если такого нет — обложку страницы
    (og:image), которая обычно оказывается логотипом студии.
    """
    soup = bs4.BeautifulSoup(html, "html.parser")
    best = ""
    best_area = 0
    for img in soup.find_all("img"):
        src = (img.get("data-original") or img.get("src") or "").strip()
        if not src.startswith("http") or not src.lower().endswith(".svg"):
            continue
        svg = http.get_text(src, ttl=24 * 3600)
        if len(svg) < 20_000:
            continue
        area = _svg_area(svg)
        if area > best_area:
            best, best_area = src, area
    if best and best_area >= 600_000:
        return best

    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"]
    return ""


def _lines(html: str) -> list[str]:
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.body or soup
    raw = body.get_text("\n", strip=True)
    return [text.clean(line) for line in raw.split("\n") if text.clean(line)]


def _date_in(line: str):
    return _RE_DATE_TEXT.search(line) or _RE_DATE.search(line)


def _description(lines: list[str], skip: int = 0) -> str:
    """Абзацы анонса: длинные строки до первого служебного блока."""
    pieces: list[str] = []
    for line in lines[skip:]:
        if _CONTACT_LINE.search(line):
            break
        if len(line) >= 80 and not _NOISE.search(line):
            pieces.append(line)
        if sum(len(p) for p in pieces) > 900:
            break
    return " ".join(pieces)


def _exhibition_cards(url: str, tags: str, category: str, kind_hint: str) -> list[dict]:
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    lines = _lines(html)

    # Заголовок выставки идёт сразу после метки «текущая выставка», но таких
    # меток на странице три (меню, шапка, сам блок) — берём первую, которая
    # действительно похожа на название.
    title = ""
    for i, line in enumerate(lines):
        if line.lower().strip() != "текущая выставка":
            continue
        for candidate in lines[i + 1:i + 3]:
            if _looks_like_title(candidate):
                title = candidate.strip("«»\"' ").strip()
                break
        if title:
            break
    if not title:
        print("  [!] не нашёл заголовок текущей выставки")
        return []

    date_raw = ""
    for line in lines:
        m = _date_in(line)
        if m and dates.is_weekend_relevant(m.group(0)):
            date_raw = m.group(0)
            break
    if not date_raw:
        print("  [!] не нашёл даты выставки")
        return []

    print(f"  выставка: {title[:60]} | {date_raw}")
    return [{
        "source": "Студия 84",
        "title": title,
        "date_raw": date_raw,
        "source_url": url,
        "image_url": _poster(html),
        "text_raw": _description(lines) or title,
        "venue": "Студия 84",
        "address": "ТРЦ «Аура», ул. Военная, 5, 2 этаж",
        "district": "Центральный",
        "tags": tags,
        "category": category,
        "kind_hint": kind_hint,
    }]


def _event_cards(url: str, tags: str, category: str, kind_hint: str) -> list[dict]:
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    lines = _lines(html)

    date_positions = [i for i, line in enumerate(lines) if _date_in(line)]
    out: list[dict] = []
    for pos, index in enumerate(date_positions):
        m = _date_in(lines[index])
        date_raw = m.group(0)
        if not dates.is_weekend_relevant(date_raw):
            continue

        # Блок события: дата → описание → заголовок (заголовок последний).
        finish = date_positions[pos + 1] if pos + 1 < len(date_positions) else index + 4
        block = [
            line for line in lines[index + 1:finish]
            if len(line) >= 3 and not _NOISE.search(line)
        ]
        if not block:
            continue
        title = block[-1]
        body = " ".join(block[:-1]) or title

        out.append({
            "source": "Студия 84",
            "title": title[:120],
            "date_raw": date_raw,
            "source_url": url,
            "image_url": "",
            "text_raw": body[:900],
            "venue": "Студия 84",
            "address": "ТРЦ «Аура», ул. Военная, 5, 2 этаж",
            "district": "Центральный",
            "tags": tags,
            "category": category,
            "kind_hint": kind_hint,
        })
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    events += _exhibition_cards(EXHIBITION_URL, "выставки, красивые места", "выставка", "long_term")
    events += _event_cards(EVENTS_URL, "встречи, лекции, мастер-классы", "лекция", "")
    return events
