"""Поиск и удаление дублей в листе events.

Дубли появляются, когда два прогона идут одновременно: оба читают таблицу
до записи и оба добавляют одно и то же событие. Ключ строки — id; если id
пустой (старые строки «Парков», добавленные до появления колонки), ключ
считается заново из источника, ссылки и названия.

    python scripts/dedupe.py --csv out/nsk_weekend.csv --dry-run
    python scripts/dedupe.py --dry-run
    python scripts/dedupe.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets  # noqa: E402
from common.model import COLUMNS, make_id  # noqa: E402


def row_key(row: dict) -> str:
    """Ключ события. Для старых строк без id собираем его из полей.

    Считать id заново по source+url+title нельзя: у «Парков» одна страница
    описывает разные мастер-классы в разных парках, и все они схлопнулись бы
    в одну строку. Поэтому без id в ключ идут ещё площадка и дата.
    """
    key = (row.get("id") or "").strip()
    if key:
        return key
    parts = [
        row.get("source", ""), row.get("source_url", ""), row.get("title", ""),
        row.get("venue", ""), (row.get("date_start") or "")[:10],
        (row.get("time_text") or ""),
    ]
    return "noid:" + hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def plan(rows: list[dict]) -> tuple[dict[str, list[dict]], dict[str, int]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row_key(row)].append(row)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    no_id = sum(1 for row in rows if not (row.get("id") or "").strip())
    return dups, {"rows": len(rows), "keys": len(groups), "no_id": no_id}


def report(dups: dict[str, list[dict]], stats: dict[str, int]) -> None:
    extra = sum(len(v) - 1 for v in dups.values())
    print(f"строк: {stats['rows']}, уникальных событий: {stats['keys']}, "
          f"без id: {stats['no_id']}")
    print(f"групп с дублями: {len(dups)}, лишних строк: {extra}")
    for key, group in list(dups.items())[:10]:
        print(f"--- {key[:10]} × {len(group)}")
        for row in group:
            print("   ", row.get("source", "")[:16], "|",
                  (row.get("title") or "")[:44], "|",
                  (row.get("parsed_at") or ""), "|",
                  (row.get("date_start") or "")[:10])


def fix_ids(rows: list[dict], known: set[str]) -> list[tuple[str, str]]:
    """Готовит id для строк, где колонка пустая.

    Базовый ключ проекта — source|url|title. У одной страницы («Творческие
    мастер-классы в парках») под него попадают разные площадки и даты, поэтому
    при совпадении ключ расширяется площадкой, датой и временем. Полностью
    одинаковые строки получают одинаковый id — их потом уберёт дедупликация.
    """
    counters: dict[str, int] = {}
    for row in rows:
        triple = (row.get("source", ""), row.get("source_url", ""), row.get("title", ""))
        base = make_id(*triple)
        counters[base] = counters.get(base, 0) + 1

    used = set(known)
    plan: list[tuple[str, str]] = []
    for row in rows:
        if (row.get("id") or "").strip():
            continue
        base = make_id(row.get("source", ""), row.get("source_url", ""), row.get("title", ""))
        if counters.get(base, 0) > 1 or base in used:
            extra = "|".join([base, row.get("venue", ""), (row.get("date_start") or "")[:10],
                              row.get("time_text", "")])
            base = hashlib.md5(extra.encode("utf-8")).hexdigest()[:16]
        used.add(base)
        plan.append((row.get("_row", ""), base))
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Удаление дублей в листе events")
    parser.add_argument("--dry-run", action="store_true", help="только показать")
    parser.add_argument("--csv", metavar="ПУТЬ", help="разобрать выгрузку CSV вместо таблицы")
    parser.add_argument("--fill-ids", action="store_true",
                        help="проставить id строкам, где колонка пустая")
    args = parser.parse_args(argv)

    if args.csv:
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        known = {(r.get("id") or "").strip() for r in rows if (r.get("id") or "").strip()}
        fixes = fix_ids(rows, known)
        if args.fill_ids:
            print(f"id нужно проставить: {len(fixes)} (в CSV-режиме только подсчёт)")
        dups, stats = plan(rows)
        report(dups, stats)
        return 0

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен: повторите позже или укажите --csv")
        return 1

    rows = []
    for pos, raw in enumerate(store.rows[1:], start=2):
        if not raw or not raw[0].strip():
            continue
        row = dict(zip(COLUMNS, raw + [""] * (len(COLUMNS) - len(raw))))
        row["_row"] = pos
        rows.append(row)

    known = {(r.get("id") or "").strip() for r in rows if (r.get("id") or "").strip()}
    fixes = fix_ids(rows, known)
    if fixes:
        print(f"id нужно проставить: {len(fixes)}")
        if not args.dry_run:
            for pos, value in fixes:
                if pos:
                    sheets._retry(store.sheet.update, f"A{pos}", [[value]])
            print(f"[+] проставлено id: {len(fixes)}")
            store._load_index()
            rows = []
            for pos, raw in enumerate(store.rows[1:], start=2):
                if not raw or not raw[0].strip():
                    continue
                row = dict(zip(COLUMNS, raw + [""] * (len(COLUMNS) - len(raw))))
                row["_row"] = pos
                rows.append(row)

    dups, stats = plan(rows)
    report(dups, stats)
    if args.dry_run or not dups:
        return 0

    id_col = COLUMNS.index("id")
    index = {str(r.get("id", "")).strip(): i for i, r in enumerate(store.rows[1:], start=2) if r and r[0].strip()}
    to_delete: list[int] = []
    to_fill: list[tuple[str, str]] = []
    for key, group in dups.items():
        # Оставляем самую раннюю строку: она уже могла быть отредактирована руками.
        group_sorted = sorted(group, key=lambda r: (r.get("parsed_at") or ""))
        for row in group_sorted[1:]:
            row_id = (row.get("id") or "").strip()
            pos = index.get(row_id) if row_id else None
            if pos:
                to_delete.append(pos)
        first = group_sorted[0]
        if not (first.get("id") or "").strip():
            pos = index.get("")
            if pos:
                to_fill.append((f"A{pos}", key))

    for cell, value in to_fill:
        sheets._retry(store.sheet.update, cell, [[value]])
    for pos in sorted(set(to_delete), reverse=True):
        sheets._retry(store.sheet.delete_rows, pos)
    store._load_index()
    print(f"[+] удалено строк: {len(set(to_delete))}, заполнено id: {len(to_fill)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
