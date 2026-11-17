"""Обновляет снимок id (data/last_ids.json) по текущей таблице.

Нужен перед прогоном, если снимок отстал: иначе сверка примет исчезнувшие
из таблицы строки за ручные удаления и скроет их.

Защита: пустой или резко уменьшившийся лист не перезаписывает снимок —
это почти всегда сбой доступа, а не удаление событий человеком.

    python scripts/refresh_snapshot.py
    python scripts/refresh_snapshot.py --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets  # noqa: E402

SNAPSHOT = ROOT / "data" / "last_ids.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Обновить снимок id таблицы")
    parser.add_argument("--force", action="store_true", help="записать, даже если строк стало резко меньше")
    args = parser.parse_args(argv)

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен, снимок не трогаю")
        return 1

    current = set(store.existing)
    previous: set[str] = set()
    if SNAPSHOT.exists():
        previous = set(json.loads(SNAPSHOT.read_text(encoding="utf-8")))

    print(f"строк в таблице: {len(current)} | в снимке: {len(previous)}")
    if not current:
        print("[-] Лист пуст — снимок не перезаписываю")
        return 1
    if previous and len(current) < len(previous) / 2 and not args.force:
        print("[-] Строк стало меньше половины. Похоже на сбой, а не на уборку.\n"
              "    Если это правда: python scripts/refresh_snapshot.py --force")
        return 1

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(sorted(current), ensure_ascii=False), encoding="utf-8")
    print(f"[+] Снимок обновлён: {len(current)} id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
