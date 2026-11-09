"""Пересчитывает булевы фильтры в уже существующих строках таблицы.

Зачем: правила «можно с детьми» и «с животными» менялись, а в таблице у строк,
которые парсер больше не собирает (удалённые вручную, прошедшие, скрытые),
остались старые значения. Скрипт проходит по всем строкам листа и обновляет
только те флаги, которые действительно изменились.

    python scripts/refresh_flags.py --dry-run   # показать, что изменится
    python scripts/refresh_flags.py             # применить
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets as sh  # noqa: E402
from common import text  # noqa: E402

FIELDS = ["is_child_ok", "is_child_friendly", "is_dog_ok"]
CHUNK = 100


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Пересчёт флагов в таблице")
    parser.add_argument("--sheet-name", default="nsk_weekend")
    parser.add_argument("--tab", default="events")
    parser.add_argument("--creds", default="credentials.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    import gspread

    client = gspread.service_account(filename=args.creds)
    book = client.open(args.sheet_name)
    worksheet = book.worksheet(args.tab)

    values = sh._retry(worksheet.get_all_values)
    if not values:
        print("[-] Лист пуст")
        return 1

    header = [h.strip() for h in values[0]]
    index = {name: i for i, name in enumerate(header)}
    missing = [f for f in FIELDS + ["title", "text_raw"] if f not in index]
    if missing:
        print(f"[-] В шапке нет колонок: {missing}")
        return 1

    updates: list[dict] = []
    counts = {field: 0 for field in FIELDS}

    for row_number, row in enumerate(values[1:], start=2):
        # Строки, добавленные руками, часто без id в первой колонке — их тоже
        # нужно пересчитывать, поэтому пропускаем только пустые строки целиком.
        if not row or not any(str(cell).strip() for cell in row):
            continue

        def cell(name: str) -> str:
            i = index[name]
            return row[i] if i < len(row) else ""

        flags = text.infer_filters(cell("title"), cell("text_raw"))
        for field in FIELDS:
            current = cell(field).strip().upper() == "TRUE"
            if current != flags[field]:
                updates.append({
                    "range": f"{sh._col_letter(index[field] + 1)}{row_number}",
                    "values": [["TRUE" if flags[field] else "FALSE"]],
                })
                counts[field] += 1

    print(f"строк проверено: {len(values) - 1}")
    print(f"к изменению: {len(updates)} ячеек "
          f"(с детьми: {counts['is_child_ok']}, для детей: {counts['is_child_friendly']}, "
          f"с животными: {counts['is_dog_ok']})")

    if args.dry_run or not updates:
        return 0

    for start in range(0, len(updates), CHUNK):
        sh._retry(worksheet.batch_update, updates[start:start + CHUNK],
                  value_input_option="USER_ENTERED")
    print("[+] Флаги обновлены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
