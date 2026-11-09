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

from common import model, sheets  # noqa: E402

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
    """Анонс с афиши: шапка + блоки «📅 дата, время → название → описание»."""
    parts = raw.split(DATE_MARK)
    header = parse_header(parts[0])
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
            text_raw="\n".join(rest) or title,
            age_text=age_text,
        ))
        events[-1] = apply_price_guard(events[-1])
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


def read_file(path: Path, source: str) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    if DATE_MARK in raw:
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
        events[-1] = apply_price_guard(events[-1])
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
            ", ".join(flags)]
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
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.date) if args.date else date.today()
    files = manual_files(args.paths)
    if not files:
        print("[-] Не нашёл ни одного текста: положите файл в content/manual/")
        return 1

    events: list[dict] = []
    for path in files:
        batch = read_file(path, args.source)
        if not batch:
            print(f"[!] {path.name}: в шапке нет строки «title:» — пропускаю")
            continue
        print(f"[+] {path.name}: событий {len(batch)}")
        events += batch

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
