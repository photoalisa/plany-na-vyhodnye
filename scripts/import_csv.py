"""Переносит готовую выгрузку в лист events, не ломая ручные правки.

Зачем отдельный скрипт, а не `push_csv.py`: тот заливает только `out/nsk_weekend.csv`
после прогона и регистрирует строки в снимке `parsed`. Строка, попавшая в `parsed`,
на следующем прогоне считается «своей»: если парсер её больше не отдаёт,
`reconcile` снимает её с публикации, а `move_inactive` уносит в архив.

Для выгрузки, собранной руками, это как раз не нужно. Здесь строки пишутся
в `events` напрямую, снимок `parsed` не трогается, поэтому:

* парсер не удалит их, даже если его источник в этом прогоне ответил;
* правки в таблице остаются — первая же запись парсером такой строки
  сохраняет то, что стоит в таблице (см. `_merge_row`, ветка «снимка нет»);
* прошедшие события (date_end раньше сегодня) помечаются archived
  и сразу уезжают в лист `archive`.

Запуск:

    python scripts/import_csv.py "path/to/events.csv" --dry-run
    python scripts/import_csv.py "path/to/events.csv"

Строки сопоставляются по id, затем по ссылке на источник, затем по
названию и дате начала — так правка ссылки руками не создаёт дубль.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import review as review_store  # noqa: E402
from common import sheets  # noqa: E402
from common import validate  # noqa: E402
from common.model import COLUMNS  # noqa: E402

SERVICE = set(sheets.SERVICE_COLUMNS)


def _clean(value: str) -> str:
    return (value or "").strip()


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            rows.append([_clean(raw.get(col, "")) for col in COLUMNS])
    return rows


def _key(row: list[str]) -> str:
    return sheets._title_key(row[COLUMNS.index("title")], row[COLUMNS.index("date_start")])


def _index(store: sheets.SheetStore) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Сопоставление по id, ссылке и паре «название + дата» — из текущей таблицы."""
    by_id = dict(store.existing)
    # Ссылка хранится со списком строк и их источниками: одна страница может
    # описывать несколько событий. Однозначной она считается, когда строка
    # ровно одна.
    by_url = {url: hits[0][0] for url, hits in store.by_url.items() if len(hits) == 1}
    by_title = {key: idx for key, idx in store.by_title_date.items() if idx}
    return by_id, by_url, by_title


def _find(row: list[str], maps) -> int | None:
    by_id, by_url, by_title = maps
    idx = by_id.get(row[COLUMNS.index("id")])
    if idx:
        return idx
    url = row[COLUMNS.index("source_url")]
    if url and by_url.get(url):
        return by_url[url]
    key = _key(row)
    if key and by_title.get(key):
        return by_title[key]
    return None


def _merge(current: list[str], incoming: list[str], strict: bool) -> list[str]:
    """Правка человека побеждает: значения из файла важнее таблицы.

    Пустая ячейка в файле означает «не трогать»: иначе один пустой столбец
    стирал бы данные, которых в выгрузке просто не было.
    """
    width = len(COLUMNS)
    base = current + [""] * (width - len(current))
    merged = list(base)
    for i, col in enumerate(COLUMNS):
        if col in SERVICE:
            continue
        value = incoming[i]
        if not value and not strict:
            continue
        merged[i] = value
    return merged


def _backup(store: sheets.SheetStore, today: date) -> Path:
    """Копия листа events до правки: откатить можно и вручную, и из неё."""
    folder = ROOT / "out" / "backup"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"events-{today.isoformat()}-{datetime.now():%H%M%S}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in store.rows[1:]:
            writer.writerow(row + [""] * (len(COLUMNS) - len(row)))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Импорт выгрузки в лист events")
    parser.add_argument("path", help="CSV с колонками events")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать, не писать")
    parser.add_argument("--strict", action="store_true",
                        help="брать из файла и пустые ячейки тоже (полная перезапись строки)")
    parser.add_argument("--archive-missing", action="store_true",
                        help="строки таблицы, которых нет в файле, пометить archived")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="считать эту дату сегодняшней")
    parser.add_argument("--apply-review", action="store_true",
                        help="сложить домены вне белого списка в очередь проверки")
    args = parser.parse_args(argv)

    source = Path(args.path)
    if not source.is_file():
        print(f"[-] Нет файла: {source}")
        return 1

    today = date.fromisoformat(args.date) if args.date else date.today()
    incoming = _read_csv(source)
    print(f"[i] В файле строк: {len(incoming)}")

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен, повторите позже")
        return 1

    maps = _index(store)
    updates: list[tuple[str, list]] = []
    appends: list[list] = []
    past: list[int] = []

    end_col = COLUMNS.index("date_end")
    status_col = COLUMNS.index("status")
    kind_col = COLUMNS.index("kind")

    review_queue: list[tuple[str, str, str, str]] = []

    for row in incoming:
        row = list(row)
        # Ссылка не из белого списка: строку сохраняем, но не публикуем.
        url = row[COLUMNS.index("source_url")]
        status, reason, host = validate.classify(url)
        if status in ("banned", "review", "invalid"):
            row[status_col] = "review"
            review_queue.append((host, reason, url, row[COLUMNS.index("title")]))
        end = row[end_col]
        finished = bool(end) and end < today.isoformat() and row[kind_col] != "permanent"
        if finished:
            row[status_col] = "archived"
            past.append(1)

        idx = _find(row, maps)
        if idx is None:
            appends.append(row)
            continue
        current = store.rows[idx - 1] if 0 <= idx - 1 < len(store.rows) else []
        merged = _merge(current, row, args.strict)
        if merged != (current + [""] * (len(COLUMNS) - len(current))):
            updates.append((f"A{idx}:{sheets._col_letter(len(COLUMNS))}{idx}", merged))

    matched = {idx for idx in (_find(row, maps) for row in incoming) if idx}
    missing = max(0, len(store.rows) - 1 - len(matched))
    print(f"[i] Совпало со строками таблицы: {len(matched)}")
    print(f"[i] Из них меняется: {len(updates)}")
    print(f"[i] Новых строк: {len(appends)}")
    print(f"[i] Прошедших событий: {len(past)} — уйдут в архив")
    print(f"[i] Строк в таблице нет в файле: {missing} (их не трогаем)")
    if review_queue:
        hosts = sorted({item[0] for item in review_queue})
        print(f"[!] Со ссылками вне белого списка: {len(review_queue)} строк, "
              f"доменов {len(hosts)} — статус review, на сайт не попадут")
        for host, reason, url, title in review_queue[:5]:
            print(f"    {host or '—':28} {reason:14} {title[:44]}")

    if args.archive_missing and missing:
        print(f"[i] --archive-missing: {missing} строк будут помечены archived")

    if args.dry_run:
        for row in appends[:5]:
            print(f"    + {row[COLUMNS.index('title')][:60]} | {row[COLUMNS.index('date_start')]}")
        for rng, merged in updates[:5]:
            print(f"    ~ {merged[COLUMNS.index('title')][:60]} | строка {rng.split(':')[0][1:]}")
        print("\n[i] --dry-run: в таблицу ничего не записано")
        return 0

    status_col = COLUMNS.index("status")
    letter = sheets._col_letter(status_col + 1)
    archived: list[tuple[str, list]] = []
    if args.archive_missing:
        for idx, row in enumerate(store.rows[1:], start=2):
            if not row or not row[0].strip() or idx in matched:
                continue
            status = (row[status_col] if len(row) > status_col else "").strip()
            if status and status != "active":
                continue
            archived.append((f"{letter}{idx}", [["archived"]]))

    backup = _backup(store, today)
    print(f"[i] Копия таблицы до правки: {backup}")

    last_col = sheets._col_letter(len(COLUMNS))
    for start in range(0, len(appends), sheets.BATCH):
        chunk = appends[start:start + sheets.BATCH]
        sheets._retry(store.sheet.append_rows, chunk, value_input_option="USER_ENTERED")
    for start in range(0, len(updates), sheets.UPDATE_BATCH):
        chunk = updates[start:start + sheets.UPDATE_BATCH]
        body = [{"range": rng, "values": [values]} for rng, values in chunk]
        sheets._retry(store.sheet.batch_update, body, value_input_option="USER_ENTERED")
    for start in range(0, len(archived), sheets.UPDATE_BATCH):
        chunk = archived[start:start + sheets.UPDATE_BATCH]
        body = [{"range": rng, "values": values} for rng, values in chunk]
        sheets._retry(store.sheet.batch_update, body, value_input_option="USER_ENTERED")

    if review_queue and args.apply_review:
        store_review = review_store.ReviewStore(book=store.book)
        for host, reason, url, title in review_queue:
            store_review.add(host, reason, url, title, "")
        store_review.save()
        print(f"[i] Очередь проверки: {store_review.summary()}")

    print(f"[+] Записано: новых {len(appends)}, обновлено {len(updates)}, "
          f"помечено archived {len(archived)}")

    # Проверка после записи: Google изредка теряет пачку добавлений. Читаем
    # лист заново и дописываем то, чего не хватает.
    store._load_index()
    present = {row[0].strip() for row in store.rows[1:] if row and row[0].strip()}
    missing_rows = [row for row in appends if row[COLUMNS.index("id")] not in present]
    if missing_rows:
        print(f"[!] Не записались с первого раза: {len(missing_rows)} — дописываю")
        sheets._retry(store.sheet.append_rows, missing_rows,
                      value_input_option="USER_ENTERED")
        store._load_index()
        present = {row[0].strip() for row in store.rows[1:] if row and row[0].strip()}
        still = [row for row in appends if row[COLUMNS.index("id")] not in present]
        if still:
            print(f"[-] Не удалось записать: {len(still)} строк — повторите запуск")

    moved = store.move_inactive()
    if moved:
        print(f"[+] В лист archive перенесено: {moved}")
    print("[i] Снимок parsed не тронут: эти строки парсер считает не своими.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
