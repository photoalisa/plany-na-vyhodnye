"""Культура.РФ (culture.ru) — афиша Новосибирской области по шести рубрикам.

Источник закрыт JS-проверкой: обычный GET отдаёт HTTP 425 с заголовком
Browser Verification, поэтому страницы открывает Chromium через Playwright
и забирает готовый JSON из <script id="__NEXT_DATA__">.

Дата выходных подставляется прямо в адрес:
    /afisha/novosibirskaya-oblast/{рубрика}/seanceStartDate-2026-11-21/seanceEndDate-2026-11-22
Фильтр серверный, но нестрогий (праздники отдают событие на 15 ноября),
поэтому локальные даты сеансов перепроверяются ещё раз.

Страница события без ?location=novosibirskaya-oblast показывает чужой город:
у выставки может быть четыре площадки в разных регионах. Площадку и сеансы
берём только те, что относятся к Новосибирской области.

Источник низкого приоритета (prio 95): он агрегатор, дубли с площадок
не должны перекрывать первоисточники.
"""

from __future__ import annotations

import json
import re
import time

from common import dates as dts

BASE = "https://www.culture.ru"
LOCALE = "novosibirskaya-oblast"

# рубрика в адресе → (категория проекта, тема для тегов)
RUBRICS = [
    ("vistavki", "выставка", "выставки"),
    ("obuchenie", "мастер-класс", "обучение"),
    ("vstrechi", "встреча", "встречи"),
    ("ekskursii", "экскурсия", "экскурсии"),
    ("prazdniki", "фестиваль", "праздники"),
    ("prochie", "событие", "прочее"),
]

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
CHALLENGE_WAIT = 25.0     # сколько ждём, пока пройдёт проверка браузера
PAGE_LIMIT = 100          # ?limit=100 — иначе выставки приходят по 20
PAUSE = 0.4               # пауза между страницами, чтобы не злить сайт
NSK_PREFIX = "Новосибирская обл"


def _weekend_dates() -> tuple[str, str]:
    sat, sun = dts.weekend()
    return sat.isoformat(), sun.isoformat()


def _payload(html: str) -> dict | None:
    m = NEXT_DATA.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _wait_data(page, timeout: float = CHALLENGE_WAIT) -> str:
    """Открывает страницу и ждёт, пока вместо проверки браузера появится JSON.

    page.content() падает, если страница в этот момент перерисовывается:
    проверка браузера подменяет документ целиком. Поэтому чтение обёрнуто
    в try, а ожидание — это повторные попытки, а не один фиксированный sleep.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            html = page.content()
        except Exception:
            page.wait_for_timeout(400)
            continue
        if NEXT_DATA.search(html):
            return html
        page.wait_for_timeout(400)
    try:
        return page.content()
    except Exception:
        return ""


def _event_url(item: dict) -> str:
    eid = item.get("_id")
    name = item.get("name") or ""
    return f"{BASE}/events/{eid}/{name}?location={LOCALE}"


def _image(item: dict) -> str:
    thumb = item.get("thumbnailFile") or {}
    public_id = thumb.get("publicId")
    if not public_id:
        return ""
    return f"https://cdn.culture.ru/images/{public_id}"


def _local_hm(raw) -> tuple[int, int] | None:
    """Время сеанса в Новосибирске: в API оно в UTC, разница +7 часов."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", str(raw or ""))
    if not m:
        return None
    minutes = int(m.group(4)) * 60 + int(m.group(5)) + 7 * 60
    return minutes // 60 % 24, minutes % 60


def _seance_time(seance: dict) -> str:
    """«11:00–19:00», если источник отдал конец сеанса, иначе только начало."""
    start = _local_hm(seance.get("startDate"))
    if not start:
        return ""
    text = f"{start[0]:02d}:{start[1]:02d}"
    end = _local_hm(seance.get("endDate"))
    if end and end[0] * 60 + end[1] > start[0] * 60 + start[1]:
        text += f"–{end[0]:02d}:{end[1]:02d}"
    return text


def _price_text(price: dict | None) -> str:
    if not price:
        return ""
    lo = price.get("min") or 0
    hi = price.get("max") or 0
    if lo == 0 and hi == 0:
        return "Бесплатно"
    if lo and hi and lo != hi:
        return f"от {lo} ₽"
    return f"{lo or hi} ₽"


def _age_text(age) -> str:
    try:
        value = int(age)
    except (TypeError, ValueError):
        return ""
    return f"{value}+" if value else ""


def _clean_text(ev: dict) -> str:
    raw = ev.get("text") or ev.get("jsonText") or ""
    if isinstance(raw, list):
        raw = " ".join(str(x.get("html", "")) for x in raw if isinstance(x, dict))
    raw = str(raw).replace("[HTML]", " ")
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = raw.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
    raw = raw.replace("&mdash;", "—").replace("&quot;", '"').replace("&amp;", "&")
    return re.sub(r"\s+", " ", raw).strip()


def _detail(page, item: dict, sat: str, sun: str) -> dict | None:
    """Открывает событие и достаёт описание, площадку и сеансы на выходные."""
    html = _wait_data(page)
    data = _payload(html) or {}
    ev = (data.get("props") or {}).get("pageProps", {}).get("event")
    if not ev:
        return None

    places = [
        p for p in (ev.get("places") or [])
        if str(p.get("address", "")).startswith(NSK_PREFIX)
    ]
    place_ids = {p.get("_id") for p in places}
    seances = [
        s for s in (ev.get("seances") or [])
        if s.get("placeId") in place_ids
        and str(s.get("localeStartDate", ""))[:10] in (sat, sun)
    ]

    place = places[0] if places else {}
    text = _clean_text(ev)
    time_text = ""
    if seances:
        # Берём первый сеанс выходных: время и его конец приходят в UTC.
        time_text = _seance_time(seances[0])

    start = min((s.get("localeStartDate") for s in seances), default="") or ""
    end = max((s.get("localeStartDate") for s in seances), default="") or ""

    return {
        "place": place.get("title") or item.get("topPlaceTitle") or "",
        "address": place.get("address") or "",
        "text": text,
        "time_text": time_text,
        "start": start,
        "end": end,
        "seances": len(seances),
    }


def _collect_list(page, sat: str, sun: str) -> tuple[list[dict], dict[int, str]]:
    """Проходит все рубрики и отдаёт карточки + id → категория."""
    items: dict[int, dict] = {}
    kinds: dict[int, str] = {}
    for rubric, category, tag in RUBRICS:
        page_no = 1
        while True:
            url = (
                f"{BASE}/afisha/{LOCALE}/{rubric}"
                f"/seanceStartDate-{sat}/seanceEndDate-{sun}"
                f"?limit={PAGE_LIMIT}&page={page_no}"
            )
            page.goto(url, wait_until="domcontentloaded")
            data = _payload(_wait_data(page)) or {}
            events = ((data.get("props") or {}).get("pageProps") or {}).get("events") or {}
            batch = events.get("items") or []
            for item in batch:
                eid = item.get("_id")
                if not eid:
                    continue
                if eid in items:
                    continue
                item["_category"] = category
                item["_tag"] = tag
                items[eid] = item
                kinds[eid] = rubric
            total_pages = (events.get("pagination") or {}).get("total") or 1
            print(f"  [{rubric}] страница {page_no}/{total_pages}: {len(batch)} карточек")
            if page_no >= total_pages or not batch:
                break
            page_no += 1
            time.sleep(PAUSE)
        time.sleep(PAUSE)
    return list(items.values()), kinds


def _fetch_playwright() -> list[dict]:
    from playwright.sync_api import sync_playwright

    sat, sun = _weekend_dates()
    print(f"  culture.ru: выходные {sat} — {sun}")

    out: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ru-RU",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        # Проверку браузера проходим один раз на любой странице сайта.
        page.goto(f"{BASE}/afisha/{LOCALE}", wait_until="domcontentloaded")
        _wait_data(page)

        items, _ = _collect_list(page, sat, sun)
        print(f"  culture.ru: уникальных событий в выдаче {len(items)}")

        detail_page = context.new_page()
        for number, item in enumerate(items, 1):
            detail_page.goto(_event_url(item), wait_until="domcontentloaded")
            detail = _detail(detail_page, item, sat, sun)
            time.sleep(PAUSE)
            if not detail or not detail["seances"]:
                continue
            out.append({
                "source": "Культура.РФ",
                "title": item.get("title") or "",
                "date_raw": item.get("date", {}).get("dayWeek")
                            or item.get("date", {}).get("dayMonth", [""])[0],
                "text_raw": detail["text"],
                "time_text": detail["time_text"],
                "venue": detail["place"],
                "address": detail["address"],
                # Район оставляем пустым: в адресах культура.рф его нет, а улицу
                # в «Центральный» или «Ленинский» переводит common/text.py
                # (guess_district), когда собирается карточка.
                "district": "",
                "category": item.get("_category", "событие"),
                "tags": item.get("_tag", ""),
                "price_text": _price_text(item.get("price")),
                "image_url": _image(item),
                "source_url": f"{BASE}/events/{item['_id']}/{item.get('name', '')}",
                "start_override": detail["start"] or sat,
                "end_override": detail["end"] or detail["start"] or sun,
                "kind_hint": "one_time" if detail["start"] == detail["end"] else "long_term",
                "age_text": _age_text(item.get("ageRestriction")),
            })
            if number % 25 == 0:
                print(f"  culture.ru: разобрано {number}/{len(items)}, на выходные {len(out)}")

        browser.close()

    print(f"  culture.ru: на выходные {sat}—{sun} собрано {len(out)}")
    return out


def fetch() -> list[dict]:
    """Никогда не роняет общий прогон: нет браузера — источник просто пропущен."""
    try:
        return _fetch_playwright()
    except Exception as exc:  # noqa: BLE001 — Playwright падает по-разному
        print(f"  culture.ru: источник пропущен ({type(exc).__name__}: {exc})")
        return []
