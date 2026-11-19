"""Очередь проверки ссылок: показать, подтвердить или отклонить домены.

    python scripts/review_links.py --list
    python scripts/review_links.py --scan              # собрать догадки из таблицы
    python scripts/review_links.py --approve vk.ru     # публиковать
    python scripts/review_links.py --reject bit.ly     # не публиковать никогда

Подтверждение добавляет домен в `data/allowed_hosts.json`, отказ — в
`data/rejected_hosts.json`. Оба списка читает `common/validate.classify`,
поэтому правило одно и для парсеров, и для ручного контура, и для сайта.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import review, validate  # noqa: E402
from common.model import COLUMNS  # noqa: E402

ALLOWED_FILE = ROOT / "data" / "allowed_hosts.json"
REJECTED_FILE = ROOT / "data" / "rejected_hosts.json"


def _read_list(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(item).strip().lower() for item in data if str(item).strip()]


def _write_list(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(values)), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def cmd_list(store: review.ReviewStore) -> int:
    print(store.summary())
    pending = sorted(store.pending(), key=lambda item: int(item.get("hits") or 0), reverse=True)
    if not pending:
        print("Очередь пуста.")
        return 0
    print()
    print(f"{'домен':32} {'встреч':>6}  {'причина':22} пример")
    for item in pending:
        host = item.get("host", "")[:32]
        reason = (item.get("reason") or "")[:22]
        title = (item.get("sample_title") or item.get("sample_url") or "")[:44]
        print(f"{host:32} {item.get('hits', '1'):>6}  {reason:22} {title}")
    print()
    print("Подтвердить: --approve <домен>   Отклонить: --reject <домен>")
    return 0


def cmd_scan(store: review.ReviewStore) -> int:
    """Пройтись по таблице событий и добавить незнакомые домены в очередь."""
    from common import sheets

    try:
        rows = sheets.read_table()
    except Exception as error:  # noqa: BLE001
        print(f"[-] Таблица недоступна: {type(error).__name__}: {error}")
        return 1
    added = 0
    for row in rows:
        url = (row.get("source_url") or "").strip()
        status, reason, host = validate.classify(url)
        if status != "review":
            continue
        before = host in store.rows
        store.add(host, reason, url, row.get("title") or "", row.get("source") or "")
        added += 0 if before else 1
    store.save()
    print(f"[+] Проверено строк {len(rows)}, новых доменов в очереди: {added}")
    print("   " + store.summary())
    return 0


def cmd_approve(store: review.ReviewStore, host: str) -> int:
    host = host.strip().lower().removeprefix("www.")
    status, reason, _ = validate.classify(f"https://{host}/")
    if status == "banned":
        print("[-] Домен в запрещённом списке — подтверждать нечего")
        return 1
    allowed = _read_list(ALLOWED_FILE)
    if host in allowed:
        print("[i] Домен уже подтверждён")
    else:
        _write_list(ALLOWED_FILE, allowed + [host])
        print(f"[+] Домен {host} добавлен в белый список")
    if store.set_status(host, "approved"):
        store.save()
    validate.reload_allowed()
    return 0


def cmd_reject(store: review.ReviewStore, host: str) -> int:
    host = host.strip().lower().removeprefix("www.")
    rejected = _read_list(REJECTED_FILE)
    if host in rejected:
        print("[i] Домен уже отклонён")
    else:
        _write_list(REJECTED_FILE, rejected + [host])
        print(f"[+] Домен {host} отклонён: ссылки на него не публикуются")
    if store.set_status(host, "rejected"):
        store.save()
    validate.reload_allowed()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Очередь проверки ссылок")
    parser.add_argument("--list", action="store_true", help="показать очередь")
    parser.add_argument("--scan", action="store_true", help="собрать из таблицы событий")
    parser.add_argument("--approve", metavar="ДОМЕН", help="подтвердить домен")
    parser.add_argument("--reject", metavar="ДОМЕН", help="отклонить домен")
    parser.add_argument("--csv", action="store_true", help="работать с data/review.csv")
    args = parser.parse_args(argv)

    book = None
    if not args.csv:
        try:
            import gspread

            from common import sheets as sheet_store

            client = gspread.service_account(filename=sheet_store.CREDENTIALS)
            client.set_timeout(sheet_store.SHEETS_TIMEOUT)
            book = client.open(sheet_store.SHEET_NAME)
        except Exception as error:  # noqa: BLE001
            print(f"[!] Google недоступен ({type(error).__name__}), работаю с CSV")
            book = None

    store = review.ReviewStore(book=book)

    if args.approve:
        return cmd_approve(store, args.approve)
    if args.reject:
        return cmd_reject(store, args.reject)
    if args.scan:
        return cmd_scan(store)
    return cmd_list(store)


if __name__ == "__main__":
    raise SystemExit(main())
