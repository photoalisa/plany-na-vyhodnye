"""Закрытые дни площадок: санитарные дни, ремонт, еженедельные выходные.

Зачем: длительная выставка идёт неделями, и парсер каждый раз ставит её в
субботу и воскресенье. Но у площадки может быть выходной — постоянный
(«музей не работает по воскресеньям») или разовый (санитарный день, ремонт).
Тогда событие в каталоге на этот день — ошибка.

Список живёт в листе `closures` Google-таблицы, а без доступа к Google — в
файле `data/closures.csv`. Колонки:

    venue    — название площадки, как в каталоге («Музей природы»)
    date     — конкретный день, если закрытие разовое: 2026-11-08
    weekday  — день недели, если закрытие постоянное: вс (или sun)
    note     — примечание для себя
    added_at — заполняется автоматически

Достаточно заполнить либо `date`, либо `weekday`.

Как добавить:
  * `python parser_main.py --close "Музей природы" 2026-11-08` — разово;
  * `python parser_main.py --close "Музей «На Каменском тракте»" вс` — каждое
    воскресенье;
  * `python parser_main.py --closed` — посмотреть список;
  * или просто вписать строку в лист `closures` руками.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

TAB = "closures"
COLUMNS = ["venue", "date", "weekday", "note", "added_at"]
CSV_PATH = Path("data/closures.csv")

# Дни недели в том виде, в каком их удобно писать: по-русски, по-английски
# или номером (0 — понедельник, как в datetime.weekday()).
WEEKDAYS: dict[str, int] = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _norm(value: str) -> str:
    """Название площадки в сравнимый вид: регистр, ё, лишние пробелы."""
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .,;:—-")


def parse_weekday(value: str) -> int | None:
    """«вс», «воскресенье», «sun» или число 0–6 → номер дня недели."""
    key = (value or "").strip().lower().replace("ё", "е")
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    if key.isdigit() and 0 <= int(key) <= 6:
        return int(key)
    return None


class ClosureStore:
    def __init__(self, book=None, path: Path = CSV_PATH):
        self.book = book
        self.path = path
        # площадка → {дата ISO: примечание}
        self.days: dict[str, dict[str, str]] = {}
        # площадка → {номер дня недели: примечание}
        self.weekdays: dict[str, dict[int, str]] = {}
        self.rows: list[list[str]] = []
        self._next_row = 1
        self._load()

    @classmethod
    def open(cls, name: str, creds: str, path: Path = CSV_PATH) -> "ClosureStore":
        """Читает закрытия из таблицы; без доступа — из локального CSV."""
        book = None
        try:
            import gspread

            client = gspread.service_account(filename=creds)
            book = client.open(name)
        except Exception as e:  # noqa: BLE001
            print(f"[i] Лист закрытий недоступен ({type(e).__name__}), беру {path}")
        return cls(book=book, path=path)

    # ------------------------------------------------------------------ чтение
    def _load(self) -> None:
        if self.book is not None:
            try:
                ws = self.book.worksheet(TAB)
                values = ws.get_all_values()
            except Exception:  # noqa: BLE001
                values = []
        else:
            values = self._read_csv()

        self.rows = [row for row in values if any(str(cell).strip() for cell in row)]
        self._next_row = len(values) + 1

        header: dict[str, int] = {}
        body = values
        if values and values[0]:
            first = [str(cell).strip().lower() for cell in values[0]]
            if "venue" in first or "date" in first:
                header = {name: i for i, name in enumerate(first)}
                body = values[1:]

        for row in body:
            if not row or not any(str(cell).strip() for cell in row):
                continue
            if header:
                venue = self._cell(row, header.get("venue"))
                day = self._cell(row, header.get("date"))[:10]
                weekday_raw = self._cell(row, header.get("weekday"))
                note = self._cell(row, header.get("note"))
            else:
                venue, day, weekday_raw, note = self._legacy(row)
            if not venue:
                continue
            if day:
                self.days.setdefault(_norm(venue), {})[day] = note
            number = parse_weekday(weekday_raw) if weekday_raw else None
            if number is not None:
                self.weekdays.setdefault(_norm(venue), {})[number] = note

    @staticmethod
    def _cell(row: list, index: int | None) -> str:
        if index is None or index >= len(row):
            return ""
        return str(row[index]).strip()

    @staticmethod
    def _legacy(row: list) -> tuple[str, str, str, str]:
        """Строка без заголовка. Старый порядок: date, venue, note, added_at;
        новый: venue, date, weekday, note, added_at."""
        first = str(row[0]).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", first[:10]):
            venue = str(row[1]).strip() if len(row) > 1 else ""
            note = str(row[2]).strip() if len(row) > 2 else ""
            return venue, first[:10], "", note
        venue = first
        day = str(row[1]).strip()[:10] if len(row) > 1 else ""
        weekday_raw = str(row[2]).strip() if len(row) > 2 else ""
        note = str(row[3]).strip() if len(row) > 3 else ""
        return venue, day, weekday_raw, note

    def _read_csv(self) -> list[list[str]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return [row for row in csv.reader(f)]

    # ------------------------------------------------------------------ запросы
    def is_closed(self, venue: str, day: date) -> bool:
        """Закрыта ли площадка в этот день.

        Названия сравниваем нестрого: «Музей природы» должно ловить
        «Музей природы» и «Новосибирский краеведческий музей, Музей природы».
        """
        target = _norm(venue)
        if not target:
            return False
        iso = day.isoformat()
        for closed_venue, days in self.days.items():
            if closed_venue and iso in days and self._same(closed_venue, target):
                return True
        for closed_venue, weekdays in self.weekdays.items():
            if closed_venue and day.weekday() in weekdays and self._same(closed_venue, target):
                return True
        return False

    @staticmethod
    def _same(closed_venue: str, target: str) -> bool:
        return closed_venue in target or target in closed_venue

    # ------------------------------------------------------------------ запись
    def add(self, venue: str, when: str, note: str = "") -> bool:
        """Закрытие разовое (`when` — дата) или постоянное (`when` — день недели)."""
        venue = (venue or "").strip()
        when = (when or "").strip()
        if not venue or not when:
            return False

        day = ""
        number: int | None = None
        try:
            day = date.fromisoformat(when[:10]).isoformat()
        except ValueError:
            number = parse_weekday(when)
        if not day and number is None:
            print(f"[!] Не понял «{when}»: нужна дата ГГГГ-ММ-ДД или день недели (вс, пн)")
            return False

        key = _norm(venue)
        if day and day in self.days.get(key, {}):
            return False
        if number is not None and number in self.weekdays.get(key, {}):
            return False

        weekday_text = ""
        if number is not None:
            weekday_text = _weekday_short(number)
        row = [venue, day, weekday_text, note, datetime.now().strftime("%Y-%m-%d %H:%M")]
        self.rows.append(row)
        if day:
            self.days.setdefault(key, {})[day] = note
        if number is not None:
            self.weekdays.setdefault(key, {})[number] = note
        self._append(row)
        return True

    def _append(self, row: list[str]) -> None:
        if self.book is not None:
            try:
                ws = self._worksheet()
                last_col = _col_letter(len(COLUMNS))
                ws.update(f"A{self._next_row}:{last_col}{self._next_row}", [row],
                          value_input_option="USER_ENTERED")
                self._next_row += 1
            except Exception as e:  # noqa: BLE001
                print(f"[!] Не удалось записать в лист {TAB}: {e}")
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
            return self.book.worksheet(TAB)
        except Exception:  # noqa: BLE001
            ws = self.book.add_worksheet(title=TAB, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            return ws

    # ------------------------------------------------------------------ отчёт
    def summary(self) -> str:
        days = sum(len(items) for items in self.days.values())
        weeks = sum(len(items) for items in self.weekdays.values())
        return f"закрытий по датам: {days}, постоянных закрытий по дням недели: {weeks}"

    def recent(self, limit: int = 30) -> list[str]:
        lines = []
        for row in self.rows[-limit:]:
            if len(row) < 2:
                continue
            venue = row[0][:44] if row else ""
            when = ""
            note = ""
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row[1])[:10]):
                when = str(row[1])[:10]
                note = str(row[2])[:40] if len(row) > 2 else ""
            else:
                when = f"каждый {row[2]}" if len(row) > 2 and row[2] else "—"
                note = str(row[3])[:40] if len(row) > 3 else ""
            lines.append(f"  {when:12} {venue:44} {note}")
        return lines


def _weekday_short(number: int) -> str:
    return ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[number]


def _col_letter(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
