"""Искитимский городской историко-художественный музей (iskmuseum.ru).

Что здесь важно: экспозиции постоянные, дат на сайте нет — значит kind
permanent. Зато есть график работы, и именно он решает, попадать ли музею в
каталог выходных:

  летний период (с 16 мая по 31 августа):  выходные — суббота и воскресенье;
  зимний  период (с сентября по май):      выходные — воскресенье и понедельник,
                                           суббота работает 11:00–16:00.

То есть зимой событие идёт только в субботу, а летом на выходные музей
закрыт. Дни открытия пишем в поле open_days, сборка выкидывает остальные дни.
"""

from __future__ import annotations

import re
from datetime import date

import bs4

from common import dates, http, text

BASE = "https://iskmuseum.ru"
EXPOSITIONS = f"{BASE}/?page_id=8"
CONTACTS = f"{BASE}/?page_id=21"
TICKETS = f"{BASE}/?page_id=10"

VENUE = "Искитимский городской историко-художественный музей"
ADDRESS = "ул. Коротеева, 28"
GEO_AREA = "Искитим"

_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}
_RE_CLOSED = re.compile(r"выходны[ех][^:]*:\s*([^.\n]{3,90})", re.IGNORECASE)

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _slug(title: str) -> str:
    """Латинский якорь из названия экспозиции.

    Все экспозиции живут на одной странице, и без разных ссылок таблица
    склеивает их в одну строку: ключом в листе служит адрес.
    """
    value = re.sub(r"^экспозиц\w*\s*", "", title, flags=re.IGNORECASE)
    value = "".join(_TRANSLIT.get(char, char) for char in value.lower())
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:40] or "exposition"


def _clean(text_value: str) -> str:
    return text.clean(re.sub(r"\s+", " ", text_value or ""))


def _body(soup: bs4.BeautifulSoup) -> tuple[bs4.BeautifulSoup, str]:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    container = soup.select_one(".entry-content") or soup.body or soup
    return soup, container.get_text("\n", strip=True)


def _is_summer(today: date) -> bool:
    return (5, 16) <= (today.month, today.day) <= (8, 31)


def _closed_weekdays(flat: str, today: date) -> set[int]:
    """Выходные дни по графику на текущий сезон.

    Сайт пишет два периода подряд; ищем нужный и берём список после
    «Выходные:». Формулировки на странице меняются, поэтому при неудаче
    подставляем то, что видно в тексте прямо сейчас.
    """
    marker = "летний период" if _is_summer(today) else "зимний период"
    index = flat.lower().find(marker)
    if index >= 0:
        chunk = flat[index:index + 500]
        match = _RE_CLOSED.search(chunk)
        if match:
            closed = {num for word, num in _WEEKDAYS.items()
                      if re.search(word[:4], match.group(1), re.IGNORECASE)}
            if closed:
                return closed
    return {5, 6} if _is_summer(today) else {0, 6}


def open_days(today: date | None = None) -> str:
    """Номера дней недели, когда музей открыт: «1,2,3,4,5»."""
    today = today or date.today()
    html_text = http.get_text(CONTACTS, ttl=7 * 24 * 3600)
    closed: set[int] = set()
    if html_text:
        soup, flat = _body(bs4.BeautifulSoup(html_text, "html.parser"))
        closed = _closed_weekdays(flat, today)
    else:
        closed = {5, 6} if _is_summer(today) else {0, 6}
    return ",".join(str(day) for day in range(7) if day not in closed)


def _price() -> str:
    """Минимальная цена взрослого входа — из таблицы на странице «Билеты»."""
    html_text = http.get_text(TICKETS, ttl=7 * 24 * 3600)
    if not html_text:
        return ""
    soup = bs4.BeautifulSoup(html_text, "html.parser")
    for row in soup.find_all("tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        if len(cells) >= 2 and "взросл" in cells[0].lower():
            numbers = re.findall(r"\d+", cells[1])
            if numbers:
                return f"{numbers[0]} руб"
    return ""


_SECTION_BREAK = re.compile(r"^[А-ЯЁ][^.!?]{3,70}\.$")


def _full_image(element) -> str:
    """Полноразмерная фотография из описания.

    В разметке WordPress отдаёт миниатюры вида «21117_05-150x150.jpg»:
    убираем суффикс размеров и получаем оригинал.
    """
    src = (element.get("data-src") or element.get("src") or "").strip()
    if not src:
        return ""
    src = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", src)
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http://"):
        return "https://" + src[len("http://"):]
    return src


def _expositions(soup: bs4.BeautifulSoup) -> list[dict]:
    """Постоянные экспозиции: заголовок h4, абзацы и первая фотография.

    Разделы идут подряд в одном контейнере, поэтому идём по документу сверху
    вниз: картинка относится к той экспозиции, под заголовком которой стоит.
    Короткие абзацы-заголовки вроде «Природа Искитимского района.» открывают
    новый раздел — их фотографии к предыдущей экспозиции уже не относятся.
    """
    container = soup.select_one(".entry-content") or soup
    items: list[dict] = []
    current: dict | None = None

    for element in container.descendants:
        name = getattr(element, "name", None)

        if name == "h4":
            value = _clean(element.get_text(" ", strip=True))
            if value.lower().startswith("экспозиц"):
                current = {"title": value, "parts": [], "image": ""}
                items.append(current)
            else:
                current = None
            continue

        if name == "img":
            if current is not None and not current["image"]:
                current["image"] = _full_image(element)
            continue

        if name != "p" or current is None:
            continue

        value = _clean(element.get_text(" ", strip=True))
        if not value:
            continue
        if len(value) < 60:
            if _SECTION_BREAK.match(value):
                current = None
            continue
        current["parts"].append(value)

    for item in items:
        joined = " ".join(item["parts"]).strip()
        item["text"] = joined or f"{item['title']} — постоянная экспозиция музея в Искитиме."
    return [item for item in items if item["title"]]


_MONTHS = (r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
           r"сентября|октября|ноября|декабря)")
_RE_RANGE = re.compile(
    rf"с\s+\d{{1,2}}\s+{_MONTHS}[^.]{{0,25}}?по\s+\d{{1,2}}\s+{_MONTHS}", re.IGNORECASE)
_RE_SINGLE = re.compile(
    rf"\d{{1,2}}\s+{_MONTHS}(?:\s+\d{{4}})?(?:\s+в\s+\d{{1,2}}[:.]\d{{2}})?", re.IGNORECASE)
_RE_POST_DATE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
_RE_READ_MORE = re.compile(r"\(ДАЛЕЕ\)\s*$", re.IGNORECASE)


def _period(value: str) -> str:
    """Период события из текста поста: «с 28 октября по 8 ноября» или «2 сентября»."""
    for pattern in (_RE_RANGE, _RE_SINGLE):
        match = pattern.search(value or "")
        if match:
            return _clean(match.group(0))
    return ""


def _post_text(url: str) -> str:
    """Полный текст поста — открываем только у анонсов, попавших на выходные.

    В ленте текст обрезан: не видно ни полной цены, ни графика вроде
    «без перерывов и выходных». Поэтому листаем одну страницу ленты, а
    карточку открываем лишь тогда, когда даты уже подошли.
    """
    html_text = http.get_text(url, ttl=7 * 24 * 3600)
    if not html_text:
        return ""
    soup = bs4.BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    container = soup.select_one(".entry-content, .post-entry-content, article") or soup.body
    if container is None:
        return ""
    value = _clean(container.get_text(" ", strip=True))
    return re.split(r"←\s*Предыдущая запись", value)[0].strip()


def _post_image(post) -> str:
    """Афиша поста: самый крупный вариант из srcset, иначе src."""
    img = post.find("img")
    if not img:
        return ""
    srcset = img.get("srcset") or ""
    candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
    src = candidates[-1] if candidates else (img.get("data-src") or img.get("src") or "")
    if not src:
        return ""
    src = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", src)
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http://"):
        return "https://" + src[len("http://"):]
    return src


def _news(today: date) -> list[dict]:
    """События из ленты новостей на главной — только те, что попадают на выходные.

    Обходим ровно одну страницу: в архиве музея больше тридцати страниц, и
    качать их все ради двух-трёх актуальных анонсов незачем. Даты берём из
    текста поста, а не из даты публикации: новость от 29 октября может
    описывать событие, которое идёт до 8 ноября.
    """
    html_text = http.get_text(BASE, ttl=6 * 3600)
    if not html_text:
        return []

    soup = bs4.BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    days = open_days()
    price = _price()
    events: list[dict] = []
    seen: set[str] = set()

    for post in soup.select("article.post-entry"):
        link = post.select_one("h2.post-entry-headline a") or post.select_one("a[rel=bookmark]")
        if link is None:
            continue
        title = _clean(link.get_text(" ", strip=True))
        href = (link.get("href") or "").strip()
        if not title or not href or href in seen:
            continue

        body = post.select_one(".post-entry-content")
        excerpt = _clean(body.get_text(" ", strip=True)) if body else ""
        excerpt = _RE_READ_MORE.sub("", excerpt).strip()
        if len(excerpt) < 40:
            continue

        raw_date = _period(excerpt) or _period(title)
        if not raw_date or not dates.is_weekend_relevant(raw_date):
            continue

        # Совсем старые новости не берём: у музея в ленте висят посты с августа.
        stamp = post.select_one(".post-info-date")
        posted = _RE_POST_DATE.search(stamp.get_text(" ", strip=True) if stamp else "")
        if posted:
            published = dates.parse_period(posted.group(0))
            if published.start and (today - published.start).days > 120:
                continue

        # В ленте анонс обрезан: открываем карточку, чтобы увидеть полную цену
        # и график работы. Запросов будет столько же, сколько событий попали
        # на выходные (обычно один-два), а не тридцать страниц архива.
        full = _post_text(href)
        if len(full) > len(excerpt):
            excerpt = full[:1800]

        # «Без перерывов и выходных» — площадка работает каждый день, значит
        # график музея (Вт–Сб) к этому событию применять нельзя.
        news_days = "" if re.search(r"без\s+(?:перерыв|выходн)", excerpt, re.IGNORECASE) else days
        # Цена из самого анонса: у гастролей она своя, а не музейные 100 ₽.
        news_price = excerpt if re.search(r"\d+\s*(?:₽|руб)", excerpt, re.IGNORECASE) else price

        seen.add(href)
        events.append({
            "source": "Искитимский музей",
            "title": title,
            "date_raw": raw_date,
            "source_url": href,
            "text_raw": excerpt,
            "venue": VENUE,
            "address": ADDRESS,
            "district": "",
            "geo_area": GEO_AREA,
            "category": "событие",
            "tags": "события, музеи",
            "price_source": news_price,
            "open_days": news_days,
            "image_url": _post_image(post),
        })
    return events


def fetch() -> list[dict]:
    html_text = http.get_text(EXPOSITIONS, ttl=6 * 3600)
    if not html_text:
        print("  [!] Страница экспозиций недоступна")
        return []
    soup = bs4.BeautifulSoup(html_text, "html.parser")
    _, flat = _body(soup)

    expositions = _expositions(soup)
    print(f"  экспозиций: {len(expositions)}")

    days = open_days()
    price = _price()

    events: list[dict] = []
    for item in expositions:
        events.append({
            "source": "Искитимский музей",
            "title": item["title"],
            "date_raw": "постоянно",
            "kind_hint": "permanent",
            "source_url": f"{EXPOSITIONS}#{_slug(item['title'])}",
            "text_raw": item["text"],
            "venue": VENUE,
            "address": ADDRESS,
            "district": "",
            "geo_area": GEO_AREA,
            "category": "выставка",
            "tags": "выставки, музеи",
            "price_source": price,
            "open_days": days,
            "image_url": item["image"],
        })

    # Анонсы с главной: только те, чьи даты попадают на ближайшие выходные.
    news = _news(date.today())
    print(f"  анонсов из новостей на выходные: {len(news)}")
    events.extend(news)
    return events
