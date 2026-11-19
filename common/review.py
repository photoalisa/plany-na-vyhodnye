"""Очередь проверки ссылок: незнакомые домены не теряются, а ждут решения.

Проверка ссылки (`common/validate.classify`) возвращает четыре статуса.
Два из них требуют человека: `review` — незнакомый, сокращатель, IP, файл
или подделка под известный домен; `invalid` — не ссылка вовсе, такие просто
отбрасываются. Сюда попадают адреса со статусом `review`, чтобы однажды
подтвердить домен и больше не спрашивать.

Хранение: лист `review` в Google-таблице, при недоступности — `data/review.csv`.
Строка — домен, а не ссылка: решают про домен целиком, иначе очередь растёт
быстрее, чем её смотрят.

    python scripts/review_links.py --list
    python scripts/review_links.py --scan          # добавить догадки из таблицы
    python scripts/review_links.py --approve vk.ru # подтвердить домен
    python scripts/review_links.py --reject bit.ly # запретить домен
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "review.csv"
FIELDS = ["host", "reason", "sample_url", "sample_title", "source",
          "first_seen", "hits", "status"]


class ReviewStore:
    """Очередь доменов. Работает с листом `review` или с локальным CSV."""

    def __init__(self, book=None) -> None:
        self.book = book
        self.sheet = None
        self.rows: dict[str, dict] = {}
        if book is not None:
            try:
                titles = {ws.title: ws for ws in book.worksheets()}
                self.sheet = titles.get("review") or book.add_worksheet(
                    title="review", rows=1000, cols=len(FIELDS))
                values = self.sheet.get_all_values()
                if values and values[0] and values[0][0] == "host":
                    for row in values[1:]:
                        if row and row[0].strip():
                            item = dict(zip(FIELDS, row + [""] * len(FIELDS)))
                            self.rows[item["host"].strip()] = item
                else:
                    self.sheet.update("A1", [FIELDS])
            except Exception as error:  # noqa: BLE001
                print(f"[!] Лист review недоступен ({type(error).__name__}), работаю с CSV")
                self.sheet = None
        if self.sheet is None:
            self._load_csv()

    def _load_csv(self) -> None:
        if not CSV_PATH.exists():
            return
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                host = (item.get("host") or "").strip()
                if host:
                    self.rows[host] = item

    def _save(self) -> None:
        if self.sheet is not None:
            values = [FIELDS] + [[item.get(field, "") for field in FIELDS]
                                 for item in self.rows.values()]
            self.sheet.clear()
            self.sheet.update("A1", values)
            return
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for item in self.rows.values():
                writer.writerow({field: item.get(field, "") for field in FIELDS})

    def add(self, host: str, reason: str, url: str = "", title: str = "",
            source: str = "") -> None:
        """Запомнить домен. Повторная встреча увеличивает счётчик."""
        host = (host or "").strip().lower()
        if not host or not reason:
            return
        item = self.rows.get(host)
        if item is None:
            self.rows[host] = {
                "host": host,
                "reason": reason,
                "sample_url": url[:300],
                "sample_title": title[:120],
                "source": source,
                "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "hits": "1",
                "status": "new",
            }
            return
        try:
            item["hits"] = str(int(item.get("hits") or 0) + 1)
        except ValueError:
            item["hits"] = "1"

    def pending(self) -> list[dict]:
        return [item for item in self.rows.values() if item.get("status") != "approved"
                and item.get("status") != "rejected"]

    def set_status(self, host: str, status: str) -> bool:
        host = (host or "").strip().lower().removeprefix("www.")
        item = self.rows.get(host)
        if item is None:
            return False
        item["status"] = status
        return True

    def save(self) -> None:
        self._save()

    def summary(self) -> str:
        pending = self.pending()
        rejected = sum(1 for item in self.rows.values() if item.get("status") == "rejected")
        approved = sum(1 for item in self.rows.values() if item.get("status") == "approved")
        return (f"В очереди {len(pending)}, подтверждено {approved}, "
                f"отклонено {rejected}, всего {len(self.rows)}")
