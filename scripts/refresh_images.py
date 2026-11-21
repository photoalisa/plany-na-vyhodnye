"""Перепроверяет афиши в таблице и обновляет image_ok.

Нужен после прогонов с --no-images: проверка ссылок пропускается, признак
остаётся FALSE, и на сайте вместо афиши подставляется заглушка.

Запуск:
    python scripts/refresh_images.py            # только показать
    python scripts/refresh_images.py --apply    # записать в таблицу
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import collect, http, sheets  # noqa: E402
from common.model import COLUMNS  # noqa: E402


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Нет доступа к таблице")
        return 2

    rows = store.rows
    idx = {c: i for i, c in enumerate(rows[0])}
    url_col, ok_col, status_col = (
        COLUMNS.index("image_url"), COLUMNS.index("image_ok"), COLUMNS.index("status"))

    def cell(row, i):
        return row[i] if len(row) > i else ""

    todo = []
    for line_no, row in enumerate(rows[1:], start=2):
        if cell(row, status_col) != "active":
            continue
        url = cell(row, url_col).strip()
        if not url.startswith("http") or cell(row, ok_col).strip().upper() == "TRUE":
            continue
        todo.append((line_no, url))

    print(f"афиш на проверку: {len(todo)}")
    updates = []
    fixed = broken = 0
    for line_no, url in todo:
        host = (urlparse(url).hostname or "").lower()
        trusted = host in collect.TRUSTED_IMAGE_HOSTS or any(
            host.endswith("." + h) for h in collect.TRUSTED_IMAGE_HOSTS)
        ok = True if trusted else http.image_ok(url)
        if ok:
            fixed += 1
            updates.append([f"{sheets._col_letter(ok_col + 1)}{line_no}", ["TRUE"]])
        else:
            broken += 1
            updates.append([f"{sheets._col_letter(ok_col + 1)}{line_no}", ["FALSE"]])
            updates.append([f"{sheets._col_letter(url_col + 1)}{line_no}", [""]])

    print(f"  подтвердятся: {fixed}, битых: {broken}")
    if not apply:
        print("\n[i] Предпросмотр. Повтори с --apply, чтобы записать.")
        return 0
    if not updates:
        return 0

    for start in range(0, len(updates), 40):
        chunk = updates[start:start + 40]
        store.sheet.batch_update([{"range": r, "values": [v]} for r, v in chunk],
                                 value_input_option="USER_ENTERED")
    print(f"[+] обновлено ячеек: {len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
