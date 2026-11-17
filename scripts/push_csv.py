"""Заливка готовой выгрузки в Google-таблицу без повторного парсинга.

Нужен, когда прогон ушёл в CSV-режим (Google был недоступен), а данные
собраны и проверены: берём out/nsk_weekend.csv, обновляем строки по id,
сверяем пропавшее и уносим неактивное в архив.

    python scripts/push_csv.py --dry-run
    python scripts/push_csv.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets  # noqa: E402
from common.model import COLUMNS  # noqa: E402

CSV_PATH = ROOT / "out" / "nsk_weekend.csv"
SUMMARY = ROOT / "out" / "summary.json"


def load_events() -> tuple[list[dict], set[str]]:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig", newline="")))
    events = []
    for row in rows:
        events.append({col: row.get(col, "") for col in COLUMNS})
    sources: set[str] = set()
    if SUMMARY.exists():
        data = json.loads(SUMMARY.read_text(encoding="utf-8"))
        sources = {s["source"] for s in data.get("sources", []) if s.get("raw", 0) > 0}
    if not sources:
        sources = {e.get("source", "") for e in events}
    return events, sources


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Заливка CSV в таблицу")
    parser.add_argument("--dry-run", action="store_true", help="только показать объём")
    args = parser.parse_args(argv)

    if not CSV_PATH.exists():
        print(f"[-] Нет файла {CSV_PATH}")
        return 1
    events, sources = load_events()
    print(f"[i] В выгрузке строк: {len(events)}, источников с ответом: {len(sources)}")
    if args.dry_run:
        return 0

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен, повторите позже")
        return 1

    stats = store.upsert(events, today=date.today())
    print(f"[+] Записано: новых {stats['new']}, обновлено {stats['updated']}, "
          f"архив {stats['archived']}")

    dropped = store.reconcile(events, sources=sources)
    if dropped:
        print(f"[+] Снято с публикации: {dropped}")
    moved = store.move_inactive()
    if moved:
        print(f"[+] В лист archive перенесено: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
