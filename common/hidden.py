"""Скрытые события: то, что вы удалили вручную, больше не возвращается.

Зачем: парсер каждый прогон собирает сайты заново, поэтому вечеринки и клубы,
которые вы убрали из таблицы, назавтра появлялись снова. Здесь хранится
список «не показывать»: лист `hidden` в Google-таблице, а без доступа к
Google — файл `data/hidden.csv`.

Как попадают в hidden:
  * автоматически — если событие было в таблице в прошлом прогоне, вы его
    удалили, а парсер снова его нашёл (см. parser_main.sync_hidden);
  * вручную — `python parser_main.py --hide <ссылка или id>`.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

HIDDEN_TAB = "hidden"
COLUMNS = ["key", "url", "title", "source", "added_at", "note"]
CSV_PATH = Path("data/hidden.csv")


class HiddenStore:
    def __init__(self, book=None, path: Path = CSV_PATH):
        self.book = book
        self.path = path
        self.keys: set[str] = set()
        self.urls: set[str] = set()
        self.rows: list[list[str]] = []
        self._next_row = 1
        self._load()

    # ------------------------------------------------------------------ чтение
    def _load(self) -> None:
        if self.book is not None:
            try:
                ws = self.book.worksheet(HIDDEN_TAB)
                values = ws.get_all_values()
            except Exception:  # noqa: BLE001
                values = []
        else:
            values = self._read_csv()

        self.rows = values
        self._next_row = len(values) + 1
        for row in values[1:] if values and values[0] and values[0][0] == "key" else values:
            if not row:
                continue
            key = (row[0] if len(row) > 0 else "").strip()
            url = (row[1] if len(row) > 1 else "").strip()
            if key:
                self.keys.add(key)
            if url:
                self.urls.add(url)

    def _read_csv(self) -> list[list[str]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return [row for row in csv.reader(f)]

    # ------------------------------------------------------------------ запись
    def is_hidden(self, event: dict) -> bool:
        return event.get("id") in self.keys or (event.get("source_url") or "") in self.urls

    def add(self, event: dict, note: str = "удалено вручную") -> bool:
        key = event.get("id") or ""
        url = event.get("source_url") or ""
        if (key and key in self.keys) or (url and url in self.urls):
            return False
        row = [
            key, url, (event.get("title") or "")[:120],
            event.get("source") or "", datetime.now().strftime("%Y-%m-%d %H:%M"), note,
        ]
        self.rows.append(row)
        if key:
            self.keys.add(key)
        if url:
            self.urls.add(url)
        self._append(row)
        return True

    def _append(self, row: list[str]) -> None:
        if self.book is not None:
            try:
                ws = self._worksheet()
                last_col = _col_letter(len(COLUMNS))
                rng = f"A{self._next_row}:{last_col}{self._next_row}"
                ws.update(rng, [row], value_input_option="USER_ENTERED")
                self._next_row += 1
            except Exception as e:  # noqa: BLE001
                print(f"[!] Не удалось записать в лист {HIDDEN_TAB}: {e}")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists()
        with self.path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(COLUMNS)
            writer.writerow(row)

    def _worksheet(self):
        try:
            return self.book.worksheet(HIDDEN_TAB)
        except Exception:  # noqa: BLE001
            ws = self.book.add_worksheet(title=HIDDEN_TAB, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            return ws

    # ------------------------------------------------------------------ отчёт
    def summary(self) -> str:
        return f"скрыто событий: {len(self.keys | self.urls)}"

    def recent(self, limit: int = 10) -> list[str]:
        lines = []
        for row in self.rows[-limit:]:
            if len(row) < 3:
                continue
            lines.append(f"  {row[2][:60]} [{row[3] if len(row) > 3 else ''}] {row[4] if len(row) > 4 else ''}")
        return lines


def _col_letter(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
