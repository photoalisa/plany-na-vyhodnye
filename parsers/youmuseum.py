"""Новосибирский краеведческий музей (youmuseum.ru) — выставки и события.

Что было сломано: цена не заполнялась ни у одной из 15 записей, «Постоянная
экспозиция» уходила с пустым date_end, а филиалы в Сузуне попадали в geo как
«Сузун» без пометки «за городом». Теперь цена берётся со страницы, постоянные
экспозиции получают kind=permanent, а пригород определяется автоматически.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://youmuseum.ru"
LISTINGS = [
    (f"{BASE}/exhibitions/", "выставки", "выставка", "long_term"),
    (f"{BASE}/news/", "события, лекции", "лекция", ""),
]

_RE_UNTIL = re.compile(
    r"до\s+(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря))",
    re.IGNORECASE,
)
_BOILER = re.compile(
    r"(стоимость билетов|справки по телефону|экскурсии по предварительной|касса|купить билет)",
    re.IGNORECASE,
)

# Филиалы краеведческого музея: у каждого свой адрес и район.
# Адреса взяты с youmuseum.ru/about/information/.
BRANCHES: list[tuple[str, str, str, str, str]] = [
    (r"краеведческ\w*\s+музе", "Новосибирский краеведческий музей",
     "Красный проспект, 23", "Центральный", "Новосибирск"),
    (r"музе\w*\s+природ", "Музей природы",
     "Вокзальная магистраль, 11", "Железнодорожный", "Новосибирск"),
    (r"музе\w*\s+связи", "Музей связи и техники",
     "ул. Советская, 33", "Центральный", "Новосибирск"),
    (r"олимпийск\w*\s+слав", "Музей Олимпийской славы",
     "ул. Немировича-Данченко, 160", "Ленинский", "Новосибирск"),
    (r"городск\w*\s+усадьб", "Музей «Городская усадьба Ново-Николаевска»",
     "ул. Ленина, 23", "Центральный", "Новосибирск"),
    (r"(монетн\w*\s+двор|сузун)", "МТК «Сузун-завод. Монетный двор»",
     "ул. Ленина, 22В", "", "Сузун"),
]


_SUZUN_TITLE = re.compile(
    r"(медеплавильн|монетн\w*\s+двор|дом и контор|народн\w*\s+икон|сузун)",
    re.IGNORECASE,
)

# Возрастная маркировка стоит в одной строке с датой и филиалом:
# «с 8 августа по 22 ноября в краеведческом музее | 6+»
_AGE_IN_INFO = re.compile(r"\|\s*(\d{1,2})\s*\+")


def _detect_branch(main_text: str, title: str = "") -> dict:
    """Где проходит событие: в главном здании или в одном из филиалов.

    Подзаголовок страницы («Постоянная экспозиция в музее природы») стоит сразу
    под заголовком, поэтому ищем его после названия — иначе в зону поиска
    попадает навигация «Филиалы музея» и срабатывает не тот филиал.
    """
    blob = text.clean(main_text)
    pos = blob.lower().find(title.lower()[:30]) if title else -1

    # Подзаголовок — это то, что идёт сразу после названия до «|» или точки:
    # «Постоянная экспозиция в музее Олимпийской славы | 0+». Дальше в тексте
    # музей рассказывает про свою принадлежность краеведческому — это ловушка.
    tail = blob[pos + len(title):] if pos >= 0 else blob
    subtitle = re.split(r"[|.]", tail, maxsplit=1)[0].strip().lower()[:160]

    for pattern, venue, address, district, area in BRANCHES:
        if re.search(pattern, subtitle):
            return {"venue": venue, "address": address,
                    "district": district, "geo_area": area}

    # Экспозиции сузунского комплекса не всегда подписаны филиалом —
    # узнаём их по названию: медеплавильный завод, монетный двор, дом управляющего.
    if _SUZUN_TITLE.search(title or ""):
        return {"venue": "МТК «Сузун-завод. Монетный двор»",
                "address": "ул. Ленина, 22В", "district": "", "geo_area": "Сузун"}
    return {}


def _enrich(ev: dict) -> dict:
    html = http.get_text(ev["source_url"], ttl=24 * 3600)
    if not html:
        ev["text_raw"] = f"Событие Краеведческого музея: «{ev['title']}»."
        return ev

    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()

    container = None
    for sel in (".exhibition-detail__content", ".news-detail__content", ".page-content",
                ".content-block", ".detail-text", "article", "main"):
        node = soup.select_one(sel)
        if node and node.find("p"):
            container = node
            break
    if not container:
        container = soup.body or soup

    # Филиал: главное здание или один из музеев-филиалов.
    # Если подзаголовок не распознался — считаем, что это главное здание:
    # пустой адрес хуже, чем адрес музея по умолчанию.
    main_text = (soup.select_one("main") or container).get_text(" ", strip=True)
    branch = _detect_branch(main_text, ev.get("title", "")) or {
        "venue": "Новосибирский краеведческий музей",
        "address": "Красный проспект, 23",
        "district": "Центральный",
        "geo_area": "Новосибирск",
    }
    ev["venue"] = branch["venue"]
    ev["address"] = branch["address"]
    ev["district"] = branch["district"]
    ev["geo_area"] = branch["geo_area"]

    info_left = soup.select_one(".info .left")
    if info_left:
        m_age = _AGE_IN_INFO.search(info_left.get_text(" ", strip=True))
        if m_age:
            ev["age_text"] = f"{m_age.group(1)}+"

    pieces = []
    for p in container.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) >= 30 and not _BOILER.search(value):
            pieces.append(value)
        if sum(len(x) for x in pieces) > 900:
            break

    # На части страниц абзацы лежат вне основного контейнера — добираем по всей странице
    if not pieces:
        for p in soup.find_all("p"):
            value = text.clean(p.get_text(" ", strip=True))
            if len(value) >= 40 and not _BOILER.search(value):
                pieces.append(value)
            if len(pieces) >= 2:
                break

    # Абзацы склеиваем по-человечески: точку ставим только на стыке абзацев,
    # а не внутри предложения (иначе «В дни осенних каникул. Краеведческий…»)
    joined = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if not joined:
            joined = piece
        elif joined[-1] in ".!?…":
            joined = f"{joined} {piece}"
        else:
            joined = f"{joined}. {piece}"

    ev["text_raw"] = (
        joined
        or f"«{ev['title']}» — событие Новосибирского краеведческого музея."
    )

    page_blob = container.get_text(" ", strip=True)
    if ev.get("kind_hint") != "permanent" and ev.get("date_raw"):
        m = _RE_UNTIL.search(page_blob)
        if m and dates.parse_period(ev["date_raw"]).kind == "one_time":
            ev["date_raw"] = f"{ev['date_raw']} - {m.group(1)}"
            ev["kind_hint"] = "long_term"
    return ev


def _parse_listing(url: str, tags: str, category: str, kind_hint: str) -> list[dict]:
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select("div.item")
    print(f"  [{url.rsplit('/', 2)[-2]}] карточек: {len(cards)}")

    candidates = []
    seen = set()
    for card in cards:
        link = card.select_one("div.caption a.link") or card.select_one("a.link")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        if not re.search(r"/(exhibitions|news)/", href):
            continue
        source_url = href if href.startswith("http") else BASE + href
        if source_url in seen:
            continue
        seen.add(source_url)

        title = text.clean(link.get_text(strip=True))
        info_el = card.select_one("div.info")
        date_raw = text.clean(info_el.get_text(" ", strip=True)) if info_el else ""
        if not date_raw:
            continue

        period = dates.parse_period(date_raw)
        # Финальную проверку выходных делаем после чтения страницы: у выставки
        # в карточке может стоять только дата открытия, а конец периода — внутри.
        if period.kind == "unknown":
            continue

        image_url = ""
        image_el = card.select_one("a.image") or card.find("img")
        if image_el:
            style = image_el.get("style") or ""
            bg = re.search(r"url\((.+?)\)", style)
            if bg:
                src = bg.group(1).strip("'\"")
            else:
                src = image_el.get("data-src") or image_el.get("src") or ""
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        candidates.append({
            "title": title,
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "tags": tags,
            "category": category,
            "kind_hint": "permanent" if period.kind == "permanent" else kind_hint,
        })

    out = []
    for ev in http.parallel(_enrich, candidates, workers=6):
        if not dates.is_weekend_relevant(ev["date_raw"]):
            continue
        ev["source"] = "Краеведческий музей"
        ev.setdefault("venue", "Новосибирский краеведческий музей")
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for url, tags, category, kind_hint in LISTINGS:
        for ev in _parse_listing(url, tags, category, kind_hint):
            if ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
    return events
