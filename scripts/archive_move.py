"""Разовая уборка: неактивные события из листа events в лист archive.

Обычно это делает parser_main.py после каждого прогона. Скрипт нужен, чтобы
разложить то, что накопилось до появления архива, и чтобы посмотреть объём
без изменений: --dry-run.

    python scripts/archive_move.py --dry-run
    python scripts/archive_move.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Перенос неактивных событий в архив")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, сколько строк уйдёт")
    args = parser.parse_args(argv)

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен: переносить некуда")
        return 1

    total = len([r for r in store.rows[1:] if r and r[0].strip()])
    moved = store.move_inactive(dry_run=args.dry_run)
    if args.dry_run:
        print(f"[i] в рабочем листе сейчас строк: {total}")
        return 0
    if not moved:
        print("[i] неактивных строк нет, переносить нечего")
        return 0
    left = len([r for r in store.rows[1:] if r and r[0].strip()])
    print(f"[+] Перенесено в archive: {moved}; в events осталось строк: {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
