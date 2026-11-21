"""Убирает дубли, которые агрегатор создал рядом с карточкой площадки.

Ищет активные строки с пересекающимися датами, похожим названием и совместимой
площадкой и оставляет карточку источника с меньшим prio. Остальные помечает
archived; перенос в лист archive делает SheetStore.move_inactive().

Запуск:
    python scripts/dedupe_twins.py            # только показать
    python scripts/dedupe_twins.py --apply    # убрать из выдачи
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import sheets, text  # noqa: E402

RATIO = sheets.TWIN_RATIO


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Нет доступа к таблице")
        return 2

    rows = store.rows
    idx = {c: i for i, c in enumerate(rows[0])}
    sources = {r[idx["source"]] for r in rows[1:] if len(r) > idx["source"]}
    prio = {name: sheets._source_prio(name) for name in sources}

    def cell(row_idx: int, column: str) -> str:
        row = rows[row_idx - 1]
        return row[idx[column]] if len(row) > idx[column] else ""

    losers: list[tuple[int, str, str, str]] = []
    for row_idx, norm, start, end, venue in store.twins:
        for other_idx, o_norm, o_start, o_end, o_venue in store.twins:
            if other_idx == row_idx:
                continue
            if (o_end or o_start) < start or (end or start) < o_start:
                continue
            if text.similar(norm, o_norm) < RATIO:
                continue
            v1, v2 = venue.lower(), o_venue.lower()
            if v1 and v2 and not (v1 in v2 or v2 in v1 or text.similar(v1, v2) >= 0.6):
                continue
            if prio.get(cell(other_idx, "source"), 50) < prio.get(cell(row_idx, "source"), 50):
                losers.append((row_idx, cell(row_idx, "source"),
                               cell(row_idx, "title")[:44], cell(other_idx, "source")))
                break

    print(f"дублей-агрегаторов: {len(losers)}")
    for row_idx, name, title, winner in losers:
        print(f"  строка {row_idx:3} {name:22} | {title} -> оставляем {winner}")

    if not losers:
        return 0
    if not apply:
        print("\n[i] Это предпросмотр. Повтори с --apply, чтобы убрать.")
        return 0

    status_col = idx["status"]
    letter = sheets._col_letter(status_col + 1)
    store.sheet.batch_update([
        {"range": f"{letter}{row_idx}", "values": [["archived"]]}
        for row_idx, *_ in losers
    ])
    moved = store.move_inactive()
    print(f"[+] помечено archived: {len(losers)}, перенесено в archive: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
