"""НаДвиж (nadvizh.ru) — городская афиша. Карточки .single_events."""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://nadvizh.ru"
MAX_PAGES = 3


def _detail(url: str) -> tuple[str, str, str]:
    """(описание, адрес площадки, полноразмерная афиша)."""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return "", "", ""
    soup = bs4.BeautifulSoup(html, "html.parser")

    # В листинге НаДвиж отдаёт миниатюры `_thumb`: источник уже кадрирует их
    # по центру, поэтому постер теряет края. В og:image лежит полный файл.
    og = soup.find("meta", property="og:image")
    image = (og.get("content") or "").strip() if og else ""

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()
    container = (
        soup.select_one(".event-single__content")
        or soup.select_one(".event-detail__content")
        or soup.select_one(".description-text")
        or soup.select_one(".page-content")
        or soup.find("article")
        or soup.find("main")
    )
    page_text = soup.get_text(" ", strip=True)
    address = text.find_address(page_text)
    if not container:
        return "", address, image
    for p in container.find_all("p"):
        value = text.clean(p.get_text(" ", strip=True))
        if len(value) > 60 and not text.is_boilerplate(value):
            return value, address, image
    return "", address, image


def _enrich(ev: dict) -> dict:
    """Описание, место и афиша со страницы события.

    В карточке НаДвижа место есть не всегда: у части событий вторая строка
    .location пустая, и тогда площадку приходится искать на самой странице.
    """
    description, address, image = _detail(ev["source_url"])
    ev["text_raw"] = description or ev["title"]

    if image and "_thumb" in (ev.get("image_url") or ""):
        ev["image_url"] = image
    elif image and not ev.get("image_url"):
        ev["image_url"] = image

    if not ev.get("address") or not any(ch.isdigit() for ch in ev["address"]):
        found = address or text.find_address(ev["text_raw"])
        known = text.recognize_venue(found, ev["text_raw"], ev["title"])
        if known:
            ev["venue"] = known.get("venue") or ev.get("venue", "")
            ev["address"] = known.get("address") or ev.get("address", "")
            ev["district"] = known.get("district") or ev.get("district", "")
        elif found:
            ev["address"] = found
            ev["district"] = ev.get("district") or text.guess_district(found)
    return ev


def _parse_page(page: int) -> list[dict]:
    url = f"{BASE}/events/" if page == 1 else f"{BASE}/events/?page={page}"
    html = http.get_text(url, ttl=1800)
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select(".single_events")
    print(f"  [{page}] карточек: {len(cards)}")

    candidates = []
    for card in cards:
        link = card.select_one("a[href*='/events/post/']")
        title_el = card.select_one("h4.header-color") or card.find("h4")
        date_el = card.select_one(".date p") or card.select_one(".date")
        if not (link and title_el and date_el):
            continue

        date_raw = text.clean(date_el.get_text(" ", strip=True))
        if not dates.is_weekend_relevant(date_raw):
            continue

        href = (link.get("href") or "").strip()
        source_url = href if href.startswith("http") else BASE + href

        img = card.select_one("img.card-img-top") or card.find("img")
        image_url = ""
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE + src

        tags = [t.get_text(strip=True) for t in card.select(".event-tags .badge")
                if t.get_text(strip=True)]
        # Среди бейджей есть возрастная метка («18+») — в описании её нет,
        # без неё клубный вечер 18+ попадал в детскую подборку.
        age_text = next((t for t in tags if re.fullmatch(r"\d{1,2}\+", t)), "")
        tags = [t for t in tags if t != age_text]
        locations = card.select(".links_locat .location p")
        # clean() срезает навигационные префиксы и превращает «Фестивали и ярмарки»
        # в «и ярмарки» — для категории нужен clean_title
        category = text.clean_title(locations[0].get_text(" ", strip=True)) if len(locations) >= 1 else ""
        raw_address = text.clean_title(locations[1].get_text(" ", strip=True)) if len(locations) >= 2 else ""

        # В адресе НаДвиж иногда есть настоящая площадка:
        # «Новосибирск, Кировский район, Garage, Советская улица, 23»
        place = text.parse_address(raw_address)
        venue = place["venue"]
        # Если улицы в карточке нет, то это не адрес, а «Новосибирск,
        # Ленинский район»: такой строкой место не считается, иначе парсер
        # решает, что адрес уже найден, и не идёт за ним на страницу события.
        address = place["address"]
        district = place["district"] or text.guess_district(raw_address, category)
        tags_line = f"{category}, {', '.join(tags)}" if category else ", ".join(tags)

        candidates.append({
            "source": "НаДвиж",
            "title": text.clean_title(title_el.get_text(" ", strip=True)),
            "date_raw": date_raw,
            "source_url": source_url,
            "image_url": image_url,
            "venue": venue,
            "address": address,
            "district": district,
            "tags": tags_line.strip(", "),
            "age_text": age_text,
            "price_source": ", ".join(tags),
        })

    out = []
    for ev in http.parallel(_enrich, candidates, workers=5):
        out.append(ev)
    return out


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        chunk = _parse_page(page)
        for ev in chunk:
            if ev["source_url"] in seen:
                continue
            seen.add(ev["source_url"])
            events.append(ev)
        if not chunk:
            break
    return events
