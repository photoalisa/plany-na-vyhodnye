"""Добавляет события из текстовых файлов в Google-таблицу.

Часть площадок не отдаёт афишу машинно: например, парки публикуют расписание
картинками. Такие события вносим руками, но не строкой в таблице, а файлом с
текстом анонса — скрипт сам разбирает дату, время, площадку и цену, считает
фильтры «бесплатно / с детьми / природа», ставит id и пишет строку в лист
«events». Дальше сайт подхватывает её как обычное событие.

Формат файла (content/manual/*.txt)::

    title: Мастер-класс «Глина» в парке «Берёзовая роща»
    date: 14 ноября
    time: 15:00
    venue: Парк «Берёзовая роща»
    address: ул. Планетная, 53
    district: Дзержинский
    price: Бесплатно
    url: https://parkigoroda-nsk.ru/november_park

    Пара абзацев анонса — попадёт в текст карточки.

Первая часть — пары «ключ: значение» до первой пустой строки, остальное —
описание. Несколько событий в одном файле разделяются строкой «---».
Файлы, имя которых начинается с «_», не читаются: там лежит шаблон.

Скопированные из ВК посты (файл из строк «1. Источник …», «Изображение …»,
шапки и текста) разбираются сами: файл режется по постам, площадка, адрес,
дата, время, цена и картинка берутся из текста, заголовок — из первой строки
поста (приветствие пропускается). Заголовок черновой, его правят в таблице.
Если черновик нужно посмотреть до записи —

    python scripts/manual_add.py --from-vk "content/manual/dump.txt"

положит рядом `dump-vk.txt` в формате «ключ: значение».

Запуск:
    python scripts/manual_add.py                          # всё из content/manual
    python scripts/manual_add.py content/manual/park.txt --dry-run
    python scripts/manual_add.py --source "Вручную"
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import dates as dts  # noqa: E402
from common import http, model, sheets  # noqa: E402

MANUAL_DIR = ROOT / "content" / "manual"

# Русские и английские названия полей: писать можно как удобно.
ALIASES = {
    "title": "title", "название": "title", "событие": "title",
    "date": "date_raw", "дата": "date_raw", "даты": "date_raw", "когда": "date_raw",
    "time": "time_text", "время": "time_text",
    "venue": "venue", "площадка": "venue", "место": "venue",
    "address": "address", "адрес": "address",
    "district": "district", "район": "district",
    "city": "geo_area", "город": "geo_area",
    "price": "price_text", "цена": "price_text",
    "url": "source_url", "ссылка": "source_url",
    "image": "image_url", "картинка": "image_url", "афиша": "image_url",
    "category": "category", "категория": "category",
    "age": "age_text", "возраст": "age_text",
    "kind": "kind_hint", "тип": "kind_hint",
}


# Живой анонс, скопированный с афиши: шапка одной строкой (площадка, адрес,
# цена, ссылка), дальше блоки, каждый начинается со значка календаря.
DATE_MARK = "📅"
_AGE = re.compile(r"\s*\b(\d{1,2}\+)\s*$")
_URL = re.compile(r"https?://\S+")
_PRICE_HINT = re.compile(
    r"(вход\s+платн\w*|вход\s+бесплатн\w*|бесплатн\w*|платн\w*\s+вход|по\s+билетам)",
    re.IGNORECASE,
)
_WHEN = re.compile(r"^\s*(.+?),?\s*(\d{1,2}[:.]\d{2})\s*(?:[—–-]\s*(.+))?$")
# «Вход платный» — значит, событие не бесплатное, даже если в описании
# упомянуто бесплатное обслуживание или мастер-класс.
_PAID = re.compile(r"(\bплатн|по\s+билетам|₽|\bруб)", re.IGNORECASE)
_DISTRICTS = {
    "центральный", "железнодорожный", "заельцовский", "калининский",
    "кировский", "ленинский", "октябрьский", "первомайский", "советский",
    "дзержинский",
}


def apply_price_guard(ev: dict) -> dict:
    """Если цена написана как «Вход платный», флаг «бесплатно» снимаем."""
    if _PAID.search(ev.get("price_text") or ""):
        ev["is_free"] = False
    return ev


# --- Выгрузка постов «ВКонтакте» -------------------------------------------
# Так выглядит файл после копирования нескольких постов подряд: номер, строка
# «Источник», строка «Изображение», дальше шапка (площадка, адрес, название)
# и текст поста. Порядок и состав строк в шапке у разных сообществ свои,
# поэтому разбирать такой файл как анонс нельзя: получаются события с пустой
# площадкой и заголовком из времени. Сначала он переводится в формат
# «ключ: значение» (--from-vk), а дальше разбирается как обычный текст.

_POST_START = re.compile(r"(?m)^\s*\d{1,2}[.)]\s*Источник\s+(\S+)")
_IMAGE_LINE = re.compile(r"(?m)^\s*Изображение\s+(\S+)")
_STREET = re.compile(
    r"(?:ул\.|улица|проспект|пр-т|пр\.|набережная|площадь|переулок|шоссе|бульвар)"
    r"\s*\S+",
    re.IGNORECASE,
)
# «Музей космонавтики имени Ю.В. Кондратюка Советская, 24» → название и адрес.
_VENUE_ADDRESS = re.compile(
    r"^(.*?)\s+((?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)?),\s*\d+\S*)\s*\.?$"
)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200d]"
)
_TIME_NAMED = re.compile(r"начало\s+в\s+(\d{1,2}[:.]\d{2})", re.IGNORECASE)
_TIME_RANGE = re.compile(
    r"с\s+(\d{1,2}[:.]\d{2})\s+до\s+(\d{1,2}[:.]\d{2})", re.IGNORECASE
)
_TIME_DASH = re.compile(r"(\d{1,2}[:.]\d{2})\s*[—–-]\s*(\d{1,2}[:.]\d{2})")
_TIME_LABEL = re.compile(
    r"время[^:\n]{0,12}[:\-—]\s*(?:с\s+)?(\d{1,2}[:.]\d{2})"
    r"(?:\s*до\s*(\d{1,2}[:.]\d{2}))?",
    re.IGNORECASE,
)
_TIME_AT = re.compile(r"\bв\s+(\d{1,2}[:.]\d{2})")
_PRICE_LINE = re.compile(
    r"(?:стоимость|цена|билет\w*)[^:\n]{0,30}[:\-—]\s*([^\n]{1,90})", re.IGNORECASE
)
_ENTRY_LINE = re.compile(
    r"вход[^:\n]{0,20}[:\-—]?\s*(свободн\w*|бесплатн\w*|платн\w*)", re.IGNORECASE
)
_PLACE_LABEL = re.compile(r"(?:место|где)\s*[?:]\s*([^\n]{3,90})", re.IGNORECASE)
_IN_BRACKETS = re.compile(r"\(([^)]*\d[^)]*)\)")
_ADDRESS_SHORT = re.compile(
    r"([А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁа-яё-]+)?),\s*\d+\S*$"
)
# Строка-приветствие: названием она не годится, берём следующую.
_GREETING = re.compile(
    r"^(?:(?:дорогие|уважаемые|милые)\s+)?(?:друзья|коллеги|гости|читатели)[!.,]?$"
    r"|^(?:привет|здравствуйте|внимание|добрый\s+(?:день|вечер))[!.,]?$",
    re.IGNORECASE,
)


def is_vk_dump(raw: str) -> bool:
    """В файле несколько постов ВК подряд, а не один анонс с полями."""
    return len(_POST_START.findall(raw)) >= 2


def _clean(line: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", line)).strip(" -—–·|")


def _time_from(text: str) -> str:
    """Время начала из живого текста: «в 16:00», «с 12:00 до 18:00»."""
    match = _TIME_NAMED.search(text)
    if match:
        return match.group(1).replace(".", ":")
    for pattern in (_TIME_RANGE, _TIME_DASH):
        match = pattern.search(text)
        if match:
            start, end = (part.replace(".", ":") for part in match.groups())
            return f"{start}–{end}"
    match = _TIME_LABEL.search(text)
    if match:
        start = match.group(1).replace(".", ":")
        end = (match.group(2) or "").replace(".", ":")
        return f"{start}–{end}" if end else start
    match = _TIME_AT.search(text)
    if match:
        return match.group(1).replace(".", ":")
    return ""


def _price_from(text: str) -> str:
    """Цена или «Бесплатно»: «Стоимость участия — 1 100 ₽», «Вход свободный»."""
    match = _PRICE_LINE.search(text)
    if match:
        value = _clean(match.group(1))
        for stop in (".", "(", "!", "/"):
            value = value.split(stop)[0].strip()
        if re.search(r"\d", value):
            return value
    match = _ENTRY_LINE.search(text)
    if match:
        word = match.group(1).lower()
        return "Бесплатно" if ("свободн" in word or "бесплатн" in word) else "Вход платный"
    return ""


def _post_header(lines: list[str]) -> tuple[str, str, list[str]]:
    """Шапка поста → (площадка, адрес, остальные строки)."""
    venue = address = ""
    index = 0
    if index < len(lines):
        first = lines[index]
        if "\t" in first:
            parts = [part.strip() for part in first.split("\t") if part.strip()]
            venue, address = parts[0], (parts[1] if len(parts) > 1 else "")
        elif " | " in first:
            venue, address = (part.strip() for part in first.split(" | ", 1))
        else:
            venue = first
        index += 1
        if not address:
            merged = _VENUE_ADDRESS.match(venue)
            if merged:
                venue, address = merged.group(1).strip(), merged.group(2).strip()
        # Адрес отдельной строкой: «улица Ленина, 71, Новосибирск».
        if index < len(lines) and _STREET.search(lines[index]):
            address = lines[index]
            index += 1
    return venue, address, lines[index:]


def vk_posts(raw: str) -> list[dict]:
    """Посты ВК из файла: ссылка, картинка и строки после шапки."""
    marks = list(_POST_START.finditer(raw))
    posts: list[dict] = []
    for number, mark in enumerate(marks):
        end = marks[number + 1].start() if number + 1 < len(marks) else len(raw)
        chunk = raw[mark.end():end]
        image = _IMAGE_LINE.search(chunk)
        body = chunk[image.end():] if image else chunk
        posts.append({
            "url": mark.group(1).rstrip(".,;"),
            "image": image.group(1).rstrip(".,;") if image else "",
            "lines": [line.strip() for line in body.splitlines() if line.strip()],
        })
    return posts


def _all_dates(text: str) -> list[str]:
    """Все даты поста по порядку: «15 ноября», «21 ноября»."""
    pattern = re.compile(r"(\d{1,2})\s*(" + dts.MONTH_PATTERN + r")\b", re.IGNORECASE)
    seen: list[str] = []
    for match in pattern.finditer(text):
        value = f"{match.group(1)} {match.group(2)}"
        if value.lower() not in (item.lower() for item in seen):
            seen.append(value)
    return seen


def vk_block(post: dict, today: date) -> dict | None:
    """Один пост ВК → поля карточки. Заголовок черновой: первая строка поста."""
    venue, address, rest = _post_header(post["lines"])
    text = "\n".join(rest)
    if not rest:
        return None
    if not address:
        place = _PLACE_LABEL.search(text)
        if place:
            venue = _clean(place.group(1))
            inside = _IN_BRACKETS.search(venue)
            if inside:
                address = _clean(inside.group(1))
                venue = _clean(_IN_BRACKETS.sub("", venue))
    address = address.rstrip(" .")
    # «Музыкальное пространство Нарымская улица, 25» → «Нарымская улица, 25».
    if len(address.split()) > 3:
        tail = _ADDRESS_SHORT.search(address)
        if tail:
            address = tail.group(1) + address[tail.end(1):]
    # Дата: выходные важнее прочих упоминаний («записаться до 13 ноября»),
    # из одинаково подходящих берём ближайшую.
    upcoming, weekend = [], []
    for raw_date in _all_dates(text):
        period = dts.parse_period(raw_date, today)
        if not period.start or period.start < today:
            continue
        upcoming.append((raw_date, period.start))
        if dts.is_weekend_relevant(raw_date, today):
            weekend.append((raw_date, period.start))
    if not upcoming:
        return None
    chosen = sorted(weekend or upcoming, key=lambda item: item[1])
    date_raw = chosen[0][0]
    others = [raw for raw, _ in upcoming if raw != date_raw]
    day, month = date_raw.split()[0], date_raw.split()[1][:4]
    same_date = re.compile(rf"\b{day}\s*{month}", re.IGNORECASE)
    line_for_date = next((line for line in rest if same_date.search(line)), text)
    title_index = 0
    while title_index < len(rest) - 1 and _GREETING.match(_clean(rest[title_index])):
        title_index += 1
    first = _clean(rest[title_index])
    title = first if len(first) <= 80 else first[:80].rsplit(" ", 1)[0] + "…"
    body = [line for position, line in enumerate(rest) if position != title_index]
    if others:
        body.append("Другие даты: " + ", ".join(others) + ".")
    return {
        "title": title,
        "date_raw": date_raw,
        "time_text": _time_from(line_for_date) or _time_from(text),
        "venue": _clean(venue),
        "address": address,
        "price_text": _price_from(text),
        "source_url": post["url"],
        "image_url": post["image"],
        "body": "\n".join(body),
    }


def vk_to_blocks(raw: str, today: date) -> tuple[str, int]:
    """Файл выгрузки → текст в формате «ключ: значение», по блоку на событие."""
    blocks: list[str] = []
    for post in vk_posts(raw):
        block = vk_block(post, today)
        if not block:
            continue
        head = [f"title: {block['title']}", f"date: {block['date_raw']}"]
        for key, field in (("time", "time_text"), ("venue", "venue"),
                           ("address", "address"), ("price", "price_text"),
                           ("url", "source_url"), ("image", "image_url")):
            if block[field]:
                head.append(f"{key}: {block[field]}")
        blocks.append("\n".join(head) + "\n\n" + block["body"].strip() + "\n")
    return "\n---\n".join(blocks), len(blocks)


_OG_IMAGE = re.compile(
    r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', re.IGNORECASE
)


def og_image(url: str) -> str:
    """Афиша со страницы события: og:image. У ВК её нет, там ссылка бесполезна."""
    if not url.startswith("http"):
        return ""
    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return ""
    match = _OG_IMAGE.search(html) or re.search(
        r'content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html, re.IGNORECASE
    )
    return match.group(1).strip() if match else ""


def ensure_image(ev: dict) -> dict:
    """Картинка для карточки: своя из файла или og:image со страницы события.

    Без этого ручные события приходили с заглушкой: в тексте анонса картинки
    нет, а обложку никто не искал.
    """
    image = (ev.get("image_url") or "").strip()
    if not image:
        image = og_image(ev.get("source_url") or "")
    if image and http.image_ok(image):
        ev["image_url"] = image
        ev["image_ok"] = True
    else:
        ev["image_url"] = ""
        ev["image_ok"] = False
    return ev


def split_when(line: str) -> tuple[str, str, str]:
    """«14 ноября, 10:00» → («14 ноября», «10:00», хвост после тире)."""
    match = _WHEN.match(line.strip())
    if not match:
        return line.strip(), "", ""
    day = match.group(1).strip().rstrip(",")
    clock = match.group(2).replace(".", ":")
    return day, clock, (match.group(3) or "").strip()


def parse_header(line: str) -> dict[str, str]:
    """Шапка анонса → площадка, адрес, цена, ссылка.

    «Музей на набережной. Обская, 4. Вход платный. https://vk.ru/…»
    """
    fields: dict[str, str] = {}
    url = _URL.search(line)
    if url:
        fields["source_url"] = url.group(0).rstrip(".,;")
        line = line.replace(url.group(0), " ")
    price = _PRICE_HINT.search(line)
    if price:
        fields["price_text"] = price.group(1)
        line = line.replace(price.group(0), " ")

    parts = [part.strip(" .,;·") for part in re.split(r"[.;·]", line)]
    parts = [part for part in parts if part]
    for part in parts:
        if part.lower() in _DISTRICTS:
            fields["district"] = part
            continue
        if "venue" not in fields:
            fields["venue"] = part
        elif "address" not in fields and re.search(r"\d", part):
            fields["address"] = part
    return fields


def read_announcement(raw: str, source: str) -> list[dict]:
    """Анонс с афиши: шапка + блоки «📅 дата, время → название → описание».

    Над первым блоком можно добавить строки вида «image: https://…» — они
    читаются как обычные поля и применяются ко всем событиям файла.
    """
    parts = raw.split(DATE_MARK)
    header_lines = [line.strip() for line in parts[0].splitlines() if line.strip()]
    header = parse_header(header_lines[0]) if header_lines else {}
    for extra in header_lines[1:]:
        match = re.match(r"^([^:]{1,24}):\s*(.+)$", extra)
        key = ALIASES.get(match.group(1).strip().lower()) if match else None
        if key:
            header[key] = match.group(2).strip()
    events: list[dict] = []
    for part in parts[1:]:
        lines = [line.strip() for line in part.strip().splitlines() if line.strip()]
        if not lines:
            continue
        day, clock, tail = split_when(lines[0])
        rest = lines[1:]
        title = tail or (rest.pop(0) if rest else "")
        if not title:
            continue
        # Отдельная афиша для одного события: строка «image: https://…»
        # внутри блока важнее общей картинки файла.
        image = header.get("image_url", "")
        kept: list[str] = []
        for line in rest:
            match = re.match(r"^(?:image|картинка|афиша)\s*:\s*(\S+)$", line, re.IGNORECASE)
            if match:
                image = match.group(1).strip()
                continue
            kept.append(line)
        rest = kept
        age = _AGE.search(title)
        age_text = age.group(1) if age else ""
        if age:
            title = _AGE.sub("", title).strip()
        events.append(model.make_event(
            source=source,
            title=title,
            date_raw=day,
            time_text=header.get("time_text", "") or clock,
            venue=header.get("venue", ""),
            address=header.get("address", ""),
            district=header.get("district", ""),
            price_text=header.get("price_text", ""),
            source_url=header.get("source_url", ""),
            image_url=image,
            category=header.get("category", ""),
            text_raw="\n".join(rest) or title,
            age_text=age_text,
        ))
        events[-1] = ensure_image(apply_price_guard(events[-1]))
    return events


def split_block(block: str) -> tuple[dict[str, str], str]:
    """Блок текста → (поля из шапки, описание)."""
    lines = block.splitlines()
    fields: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = i + 1
            break
        match = re.match(r"^([^:]{1,24}):\s*(.*)$", stripped)
        key = ALIASES.get(match.group(1).strip().lower()) if match else None
        if not key:
            body_start = i
            break
        fields[key] = match.group(2).strip()
    return fields, "\n".join(lines[body_start:]).strip()


def read_file(path: Path, source: str, today: date | None = None) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    # Выгрузка постов ВК: несколько анонсов подряд. Переводим её в поля
    # на лету — молча разбирать как один анонс нельзя, иначе площадкой
    # становится номер поста, а названием — время.
    if not re.match(r"\s*(?:title|название|событие)\s*:", raw, re.IGNORECASE) \
            and is_vk_dump(raw):
        blocks, count = vk_to_blocks(raw, today or date.today())
        print(f"[i] {path.name}: выгрузка постов ВК, событий {count}; "
              f"заголовки черновые — поправьте их в таблице")
        raw = blocks
    # Файл с явными полями читается построчно, даже если в описании остался
    # значок календаря: иначе строки «📅 21 ноября» из текста поста становятся
    # отдельными событиями с временем вместо названия.
    explicit = re.match(r"\s*(?:title|название|событие)\s*:", raw, re.IGNORECASE)
    if not explicit and DATE_MARK in raw:
        events = read_announcement(raw, source)
        if events:
            return events
    events: list[dict] = []
    for block in re.split(r"(?m)^\s*---\s*$", raw):
        fields, body = split_block(block.strip("\n"))
        if not fields.get("title"):
            continue
        events.append(model.make_event(
            source=source,
            title=fields.get("title", ""),
            date_raw=fields.get("date_raw", ""),
            time_text=fields.get("time_text", ""),
            venue=fields.get("venue", ""),
            address=fields.get("address", ""),
            district=fields.get("district", ""),
            geo_area=fields.get("geo_area", ""),
            price_text=fields.get("price_text", ""),
            source_url=fields.get("source_url", ""),
            image_url=fields.get("image_url", ""),
            text_raw=body or fields.get("title", ""),
            category=fields.get("category", ""),
            age_text=fields.get("age_text", ""),
            kind_hint=fields.get("kind_hint", ""),
        ))
        events[-1] = ensure_image(apply_price_guard(events[-1]))
    return events


def manual_files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            found += sorted(p for p in path.glob("*.txt") if not p.name.startswith("_"))
        elif path.is_file():
            found.append(path)
        else:
            print(f"[!] Нет файла или папки: {path}")
    return found


def describe(ev: dict) -> str:
    when = ev["date_start"] or "без даты"
    if ev["date_end"] and ev["date_end"] != ev["date_start"]:
        when += f"–{ev['date_end']}"
    flags = [name for name in ("is_free", "is_child_ok", "is_dog_ok", "is_nature")
             if ev.get(name)]
    bits = [when, ev["time_text"], ev["venue"] or ev["address"], ev["price_text"],
            "фото есть" if ev.get("image_ok") else "без фото", ", ".join(flags)]
    return " · ".join(bit for bit in bits if bit)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Событие из текста → строка в таблице")
    parser.add_argument("paths", nargs="*", default=[str(MANUAL_DIR)],
                        help="файлы или папки с текстами (по умолчанию content/manual)")
    parser.add_argument("--source", default="Вручную",
                        help="значение колонки source (по умолчанию «Вручную»)")
    parser.add_argument("--date", help="считать эту дату сегодняшней (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что получится, в таблицу не писать")
    parser.add_argument("--from-vk", metavar="FILE",
                        help="перевести выгрузку постов ВК в формат «ключ: значение»")
    parser.add_argument("--out", help="куда положить результат --from-vk")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.date) if args.date else date.today()

    if args.from_vk:
        source = Path(args.from_vk)
        if not source.is_file():
            print(f"[-] Нет файла: {source}")
            return 1
        blocks, count = vk_to_blocks(
            source.read_text(encoding="utf-8-sig"), today
        )
        if not count:
            print("[-] В выгрузке не нашлось ни одного поста с датой")
            return 1
        target = Path(args.out) if args.out else source.with_name(source.stem + "-vk.txt")
        if args.dry_run:
            print(blocks)
            print(f"\n[i] --dry-run: событий {count}, файл не записан")
            return 0
        target.write_text(blocks + "\n", encoding="utf-8")
        print(f"[+] Событий: {count}. Черновик: {target}")
        print("[i] Проверьте заголовки (взяты из первой строки поста) и адрес, "
              "затем запустите разбор как обычно.")
        return 0

    files = manual_files(args.paths)
    if not files:
        print("[-] Не нашёл ни одного текста: положите файл в content/manual/")
        return 1

    events: list[dict] = []
    for path in files:
        batch = read_file(path, args.source, today)
        if not batch:
            print(f"[!] {path.name}: не разобрал ни одного события — пропускаю")
            continue
        print(f"[+] {path.name}: событий {len(batch)}")
        events += batch

    if not events:
        return 1

    # Один анонс с несколькими датами даёт строки с одинаковым id (id считается
    # от источника, ссылки и названия). В таблицу такая пара уходит дублем.
    unique: dict[str, dict] = {ev["id"]: ev for ev in events}
    if len(unique) != len(events):
        print(f"[!] Одинаковых событий в пачке: {len(events) - len(unique)} — "
              f"оставляю по одному")
        events = list(unique.values())

    if not events:
        return 1

    problems = [ev for ev in events if not ev["date_start"] and ev["kind"] != "permanent"]
    for ev in problems:
        print(f"[!] Не разобрал дату: «{ev['title']}» ({ev['date_raw']!r}) — "
              f"в таблицу не пишу, пока дата не исправлена")
    ready = [ev for ev in events if ev not in problems]

    print()
    for ev in ready:
        print(f"  {ev['title'][:60]}")
        print(f"    {describe(ev)}")
        print(f"    id {ev['id']} · {ev['geo_area']} · {ev['category']}")

    if not ready:
        print("\n[-] Писать нечего")
        return 1

    if args.dry_run:
        print("\n[i] --dry-run: в таблицу ничего не записано")
        return 0

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Таблица недоступна, записывать некуда. Проверьте credentials.json "
              "и доступ к Google Sheets.")
        return 1

    stats = store.upsert(ready, today=today)
    print(f"\n[+] Новых: {stats['new']}, обновлено: {stats['updated']}, "
          f"в архив: {stats['archived']}")
    print("[i] Сайт обновится при следующей публикации: понедельник или пятница "
          "в 09:00 либо кнопкой Run workflow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
