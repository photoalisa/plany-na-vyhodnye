"""Разовая чистка адресов в таблице событий.

«Новосибирская обл, ул. Свердлова, д. 13» → «ул. Свердлова, 13», у событий
области снимается повтор названия населённого пункта. Пишется только
колонка address.

Снимок parsed намеренно не трогается: после импорта выгрузки он и так
расходится с таблицей, а расхождение говорит парсеру, что ячейку правил
человек, и защищает её от перезаписи сырым адресом источника.

    python scripts/normalize_addresses.py            # показать, что изменится
    python scripts/normalize_addresses.py --apply    # записать в таблицу
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets as sheets_store  # noqa: E402
from common.address import normalize  # noqa: E402
from common.model import COLUMNS  # noqa: E402

CHUNK = 100


def collect(store) -> list[tuple[int, str, str, str]]:
    """(номер строки в таблице, было, стало, населённый пункт)."""
    address_col = COLUMNS.index("address")
    area_col = COLUMNS.index("geo_area")
    changes = []
    for index, row in enumerate(store.rows[1:], start=2):
        if not row or not row[0].strip():
            continue
        old = (row[address_col] if len(row) > address_col else "").strip()
        if not old:
            continue
        area = (row[area_col] if len(row) > area_col else "").strip()
        new = normalize(old, area)
        if new and new != old:
            changes.append((index, old, new, area))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Нормализация адресов в таблице")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()

    store = sheets_store.SheetStore(use_google=True)
    if store.sheet is None:
        print("[!] Таблица недоступна, работа остановлена")
        return 1

    changes = collect(store)
    print(f"адресов к изменению: {len(changes)}")

    for index, old, new, area in changes[:15]:
        print(f"  стр.{index} [{area or 'Новосибирск'}]")
        print(f"    было:  {old}")
        print(f"    станет: {new}")

    if not changes:
        return 0

    backup_dir = ROOT / "out" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = backup_dir / f"addresses-{stamp}.csv"
    with backup.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["row", "area", "address_before", "address_after"])
        for index, old, new, area in changes:
            writer.writerow([index, area, old, new])
    print(f"резервная копия: {backup}")

    if not args.apply:
        print("режим проверки: в таблицу ничего не записано (--apply чтобы записать)")
        return 0

    letter = sheets_store._col_letter(COLUMNS.index("address") + 1)
    for start in range(0, len(changes), CHUNK):
        chunk = changes[start:start + CHUNK]
        body = [
            {"range": f"{letter}{index}", "values": [[new]]}
            for index, _old, new, _area in chunk
        ]
        sheets_store._retry(store.sheet.batch_update, body, value_input_option="USER_ENTERED")
        print(f"  записано {min(start + CHUNK, len(changes))} из {len(changes)}")

    print(f"готово, обновлено строк: {len(changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
