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

    losers: list[tuple[int, int, str, str, str]] = []
    for row_idx, norm, start, end, time_text, venue in store.twins:
        for other_idx, o_norm, o_start, o_end, o_time, o_venue in store.twins:
            if other_idx == row_idx:
                continue
            # Пустая дата у одной из сторон — не несовпадение: у выставок
            # «постоянно» её нет, и агрегатор иначе дублировал карточку площадки.
            if start and o_start:
                if (o_end or o_start) < start or (end or start) < o_start:
                    continue
            v1, v2 = venue.lower(), o_venue.lower()
            same_venue = not (v1 and v2) or (
                v1 in v2 or v2 in v1 or text.similar(v1, v2) >= 0.6
            )
            # Точное совпадение названия — дубль даже при разных названиях
            # площадки: агрегатор пишет её по-своему.
            close = text.similar(norm, o_norm) >= RATIO
            prefix = bool(time_text) and time_text == o_time and sheets._prefix_clash(norm, o_norm)
            if not (same_venue and (close or prefix)):
                continue
            if prio.get(cell(other_idx, "source"), 50) < prio.get(cell(row_idx, "source"), 50):
                losers.append((row_idx, other_idx, cell(row_idx, "source"),
                               cell(row_idx, "title")[:44], cell(other_idx, "source")))
                break

    print(f"дублей-агрегаторов: {len(losers)}")
    for row_idx, _winner, name, title, winner in losers:
        print(f"  строка {row_idx:3} {name:22} | {title} -> оставляем {winner}")

    if not losers:
        return 0
    if not apply:
        print("\n[i] Это предпросмотр. Повтори с --apply, чтобы убрать.")
        return 0

    status_col = idx["status"]
    status_letter = sheets._col_letter(status_col + 1)
    body = [
        {"range": f"{status_letter}{row_idx}", "values": [["archived"]]}
        for row_idx, *_ in losers
    ]

    # Перед архивацией добираем в победителя то, чего у него нет: у площадки
    # часто нет цены или афиши, а у агрегатора есть.
    fill_fields = ("price_text", "is_free", "time_text", "image_url", "image_ok",
                   "age", "is_child_friendly")
    # «Уточняйте на сайте» — заглушка, а не цена: её можно заменить настоящей.
    placeholders = {"", "уточняйте", "уточняйте на сайте", "цена на сайте", "цена на сайте источника"}
    for row_idx, winner_idx, *_ in losers:
        for name in fill_fields:
            current = cell(winner_idx, name).strip()
            value = cell(row_idx, name).strip()
            if name == "price_text":
                if current.lower() not in placeholders or value.lower() in placeholders:
                    continue
            elif name == "is_free":
                # Бесплатность — факт: если агрегатор её знает, берём.
                if current.upper() == "TRUE" or value.upper() != "TRUE":
                    continue
            elif current or not value:
                continue
            column = sheets._col_letter(idx[name] + 1)
            body.append({"range": f"{column}{winner_idx}", "values": [[value]]})

    for start in range(0, len(body), 50):
        store.sheet.batch_update(body[start:start + 50], value_input_option="USER_ENTERED")
    moved = store.move_inactive()
    print(f"[+] помечено archived: {len(losers)}, перенесено в archive: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
