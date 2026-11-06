"""Новосибирский планетарий (nebo-nsk.ru) — сеансы афиши на выходные."""

from __future__ import annotations

import re
from datetime import datetime

import bs4

from common import dates, http, text

BASE = "https://nebo-nsk.ru"
AFISHA = f"{BASE}/afisha?date=all"


def _session_dt(span) -> datetime | None:
    content = (span.get("content") or "").strip()
    if content:
        try:
            return datetime.fromisoformat(content)
        except ValueError:
            pass
    value = span.get_text(" ", strip=True)
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2}):(\d{2})", value)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, h, mi)
        except ValueError:
            return None
    return None


def fetch() -> list[dict]:
    # Короткий таймаут: сайт периодически отдаёт 502 на стороне сервера, и
    # ждать по 20 секунд на каждый из трёх ретраев бессмысленно — источник
    # просто простаивает. Со коротким таймаутом прогон не тормозит, а когда
    # сайт оживёт, события вернутся сами.
    html = http.get_text(AFISHA, ttl=1800, timeout=8)
    if not html:
        print("  [!] Афиша планетария недоступна (сервер отвечает ошибкой)")
        return []
    soup = bs4.BeautifulSoup(html, "html.parser")
    cards = soup.select("#afisha_items .afisha_item") or soup.select(".afisha_item")
    print(f"  карточек: {len(cards)}")

    sat, sun = dates.weekend()
    window = {sat.isoformat(), sun.isoformat()}
    events: list[dict] = []
    seen: set[str] = set()

    for card in cards:
        title_el = card.select_one(".afisha_title a")
        if not title_el:
            continue
        title = text.clean(title_el.get_text(strip=True))
        href = (title_el.get("href") or "").strip()
        source_url = href if href.startswith("http") else BASE + href
        if source_url in seen:
            continue

        sessions = []
        for span in card.select(".afisha_date span.date-display-single"):
            dt = _session_dt(span)
            if dt:
                sessions.append(dt)
        sessions.sort()

        relevant = [s for s in sessions if s.date().isoformat() in window]
        if not relevant:
            continue
        first = relevant[0]

        body_el = card.select_one(".afisha_body")
        if body_el:
            for a in body_el.select("a.afisha_more"):
                a.decompose()
        body = text.clean(body_el.get_text(" ", strip=True)) if body_el else ""

        image_el = card.select_one(".afisha_img img")
        image_url = ""
        if image_el:
            src = image_el.get("data-src") or image_el.get("src") or ""
            if src:
                image_url = src if src.startswith("http") else BASE + src

        age_el = card.select_one(".afisha_age")
        age = text.clean(age_el.get_text(strip=True)) if age_el else ""
        if age and not re.fullmatch(r"\d{1,2}\+", age):
            age = ""

        seen.add(source_url)
        events.append({
            "source": "Планетарий",
            "title": title,
            "date_raw": first.strftime("%d.%m.%Y - %H:%M"),
            "source_url": source_url,
            "image_url": image_url,
            "text_raw": body or f"Программа Новосибирского планетария: «{title}».",
            "venue": "Новосибирский планетарий",
            "address": "Ключ-Камышенское плато, 1/1",
            "district": "Октябрьский",
            "age_text": age,
            "tags": "лекции, астрономия, экскурсии",
            "price_source": card.get_text(" ", strip=True)[:250],
        })
    return events
