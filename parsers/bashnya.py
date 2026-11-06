"""Креативный центр «Башня» — через открытый фид Tilda (самый надёжный источник в проекте).

Обычно фид отдаёт полный текст поста. Но бывает, что поле `text` пустое и
остаётся только превью из афиши — тогда читаем страницу поста: там текст
лежит в блоках `.t-text`.
"""

from __future__ import annotations

import re

import bs4

from common import dates, http, text

BASE = "https://bashnya-site.ru"
FEED_UID = "248935481171-296918835761"
FEED_URL = f"https://feeds.tildacdn.com/api/getfeed/?feeduid={FEED_UID}&size=500&sort=asc"


def _posts() -> list[dict]:
    data = http.get_json(FEED_URL, ttl=3600)
    if not data:
        print("  [-] Фид не получен, пробую страницу события напрямую")
        return []
    posts = data.get("posts") if isinstance(data, dict) else data
    return [p for p in (posts or []) if isinstance(p, dict)]


def fetch() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()

    for post in _posts():
        title = text.clean(_strip(post.get("title") or ""))
        if not title:
            continue

        blob = " ".join(
            _strip(post.get(key) or "") for key in ("descr", "text", "parts")
        )
        date_raw = _extract_date(blob)
        if not date_raw:
            continue
        if not dates.is_weekend_relevant(date_raw):
            continue

        url = (post.get("url") or post.get("directlink") or "").strip()
        if url and not url.startswith("http"):
            url = BASE + "/" + url.lstrip("/")
        if not url:
            url = f"{BASE}/#rec1188210791"

        image = (post.get("image") or post.get("thumb") or "").strip()
        if image.startswith("//"):
            image = "https:" + image

        body = _strip(post.get("text") or "")
        if len(body) < 120:
            page = _page_text(url)
            if len(page) > len(body):
                body = page
        if not body:
            body = _strip(post.get("descr") or "") or title
        body = _cut_heading(body, title)
        if len(body) < 40:
            body = f"{title}. {_strip(post.get('descr') or '')}".strip()

        key = f"{url}|{title}"
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "source": "Башня",
            "title": title,
            "date_raw": date_raw,
            "source_url": url,
            "image_url": image,
            "text_raw": body,
            "venue": "Креативный центр «Башня»",
            "address": "ул. Ватутина, 29/1",
            "district": "Ленинский",
            "tags": "встречи, лекции, красивые места",
            "price_source": body[:300],
        })
    return events


def _page_text(url: str) -> str:
    """Текст поста со страницы: в фиде поле text иногда приходит пустым.

    Пустое поле дает превью на 25 символов — из него карточка обрывалась на
    «Креативный центр «Башня», ул.».
    """
    if not url.startswith("http"):
        return ""
    html = http.get_text(url, ttl=6 * 3600)
    if not html:
        return ""
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    pieces: list[str] = []
    for block in soup.select(".t-text"):
        value = _strip(block.decode_contents())
        if len(value) >= 40 and value not in pieces:
            pieces.append(value)
        if len(pieces) == 2:
            break
    return " ".join(pieces)


def _cut_heading(body: str, title: str) -> str:
    """На странице текст начинается с заголовка — в карточке он уже есть."""
    words = body.split()
    target = text.normalize_title(title).split()
    if not target or len(words) < len(target):
        return body
    seen: list[str] = []
    for index, word in enumerate(words[: len(target) + 4]):
        seen.append(text.normalize_title(word))
        if set(target) <= set(seen):
            rest = " ".join(words[index + 1:]).strip()
            # Если после заголовка ничего не осталось — не режем.
            return rest if len(rest) >= 40 else body
    return body


def _strip(html: str) -> str:
    if not html:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(html))
    return text.clean(value)


_RE_DATE_TEXT = re.compile(
    r"(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря|янв|фев|мар|апр|июн|июл|авг|сен|окт|ноя|дек)"
    r"(?:\s+(?:в\s+)?\d{1,2}:\d{2})?)",
    re.IGNORECASE,
)
_RE_DATE_NUM = re.compile(r"(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)(?:\s+(?:в\s+)?\d{1,2}:\d{2})?")
_RE_RECURRING = re.compile(
    r"(кажд\w*\s+(?:субботу|воскресенье|выходн\w*)|по\s+(?:субботам|воскресеньям|выходным)|"
    r"ежедневно|еженедельно)",
    re.IGNORECASE,
)


def _extract_date(blob: str) -> str:
    m = _RE_RECURRING.search(blob)
    if m:
        return m.group(0)
    m = _RE_DATE_TEXT.search(blob)
    if m:
        return m.group(1)
    m = _RE_DATE_NUM.search(blob)
    if m:
        return m.group(1)
    return ""
