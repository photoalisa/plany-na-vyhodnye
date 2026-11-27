"""Оффлайн-проверка upsert на фейковом листе (без сети и без Google).

    python tests/test_sheets_upsert.py

Проверяем именно то, что сломалось на реальной таблице:
  * таблица читается один раз, а не по строке (иначе 429 Quota exceeded);
  * повторный прогон даёт «новых 0»;
  * изменённое поле обновляет строку;
  * смена названия при той же ссылке обновляет строку, а не добавляет дубль;
  * данные пишутся в лист данных, лог — в отдельный лист.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import model, sheets  # noqa: E402


class FakeSheet:
    def __init__(self, title: str = "events"):
        self.title = title
        self.data: list[list[str]] = []
        self.row_values_calls = 0
        self.get_all_values_calls = 0
        self.append_calls = 0
        self.batch_calls = 0

    # --- чтение
    def row_values(self, row: int) -> list[str]:
        self.row_values_calls += 1
        return self.data[row - 1] if 0 < row <= len(self.data) else []

    def get_all_values(self) -> list[list[str]]:
        self.get_all_values_calls += 1
        return [list(r) for r in self.data]

    def update(self, rng: str, values):
        self.data = [list(values[0])]
        return {}

    # --- запись
    def append_rows(self, rows, value_input_option="RAW"):
        self.append_calls += 1
        if not self.data:
            self.data.append(list(sheets.COLUMNS))
        self.data.extend([list(r) for r in rows])
        return {}

    def batch_update(self, body, value_input_option="RAW"):
        self.batch_calls += 1
        for item in body:
            rng, values = item["range"], item["values"]
            main_part = rng.split(":")[0]
            col_letter = "".join(ch for ch in main_part if ch.isalpha())
            row_num = int("".join(ch for ch in main_part if ch.isdigit()))
            col_idx = _letter_to_index(col_letter)
            while len(self.data) < row_num:
                self.data.append([])
            row = self.data[row_num - 1]
            while len(row) <= col_idx:
                row.append("")
            if ":" in rng:
                row[col_idx:] = list(values[0])
            else:
                row[col_idx] = values[0][0]
        return {}


class FakeBase:
    """Лист `parsed`: снимок того, что парсер предлагал в прошлый раз."""

    def __init__(self, title: str = "parsed"):
        self.title = title
        self.rows: list[list[str]] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(r) for r in self.rows]

    def clear(self):
        self.rows = []
        return {}

    def update(self, values, rng="A1"):
        # gspread принимает и update(values, "A1"), и update("A1", values).
        if isinstance(values, str):
            values, rng = rng, values
        self.rows = [list(v) for v in values]
        return {}

    def append_row(self, row, value_input_option="RAW"):
        self.rows.append(list(row))
        return {}


class FakeBook:
    def __init__(self, events: FakeSheet):
        self.events = events
        self.other: dict[str, FakeBase] = {}

    def worksheet(self, title: str):
        if title == "events":
            return self.events
        if title in self.other:
            return self.other[title]
        raise KeyError(title)

    def add_worksheet(self, title: str, rows: int = 1000, cols: int = 20):
        ws = FakeBase(title)
        self.other[title] = ws
        return ws

    def worksheets(self):
        return [self.events] + list(self.other.values())


def _letter_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def make_store(fake: FakeSheet) -> sheets.SheetStore:
    store = object.__new__(sheets.SheetStore)
    store.name = "nsk_weekend"
    store.csv_fallback = Path("out/_test.csv")
    store.sheet = fake
    store.book = FakeBook(fake)
    store.existing = {}
    store.by_url = {}
    store.rows = []
    store.ws_rows = 0
    store._ensure_header()
    store._load_index()
    return store


def event(**kw):
    base = dict(
        source="ЦК19", title="Выставка «Тест»", date_raw="29.10_29.11",
        source_url="https://cc19.ru/product/test/", text_raw="Описание события для проверки записи.",
        price_text="300 руб.", district="Центральный", kind_hint="long_term",
    )
    base.update(kw)
    return model.make_event(**base)


def main() -> int:
    fake = FakeSheet()
    store = make_store(fake)

    events = [event(), event(title="Лекция «Другая»", source_url="https://cc19.ru/product/lect/")]
    first = store.upsert(events)
    assert first["new"] == 2 and first["updated"] == 0, first
    assert len(fake.data) == 3, f"ожидали шапку + 2 строки, получили {len(fake.data)}"
    assert fake.row_values_calls == 1, f"чтений по строке: {fake.row_values_calls} (должно быть 1 — шапка)"
    print(f"[ok] первая запись: новых {first['new']}, строк в листе {len(fake.data)}, "
          f"чтений по строке {fake.row_values_calls}")

    # повторный прогон без изменений
    fake.row_values_calls = 0
    second = store.upsert(events)
    assert second["new"] == 0 and second["updated"] == 0, second
    assert fake.row_values_calls == 0, "на повторном прогоне не должно быть чтений по строке"
    assert len(fake.data) == 3, f"появились дубли: {len(fake.data)} строк"
    print(f"[ok] повторный прогон: новых {second['new']}, обновлено {second['updated']}, дублей нет")

    # изменили цену — строка обновилась
    changed = [event(price_text="500 руб."), events[1]]
    third = store.upsert(changed)
    assert third["new"] == 0 and third["updated"] == 1, third
    price_col = sheets.COLUMNS.index("price_text")
    assert "500" in fake.data[1][price_col], fake.data[1][price_col]
    print(f"[ok] изменение поля: обновлено {third['updated']}, цена в строке {fake.data[1][price_col]}")

    # название поменялось на сайте, ссылка та же — обновляем, а не добавляем
    renamed = [event(title="Выставка «Тест (новое название)»", price_text="500 руб."), events[1]]
    stats = store.upsert(renamed)
    title_col = sheets.COLUMNS.index("title")
    assert stats["new"] == 0, stats
    assert len(fake.data) == 3, f"дубль по ссылке: {len(fake.data)} строк"
    assert "новое название" in fake.data[1][title_col], fake.data[1][title_col]
    print(f"[ok] смена названия: новых {stats['new']}, строк {len(fake.data)}, заголовок обновлён")

    # прошедшее событие уходит в архив
    past = [event(title="Прошедшее", source_url="https://cc19.ru/product/past/",
                  date_raw="01.10_02.10")]
    store.upsert(past)
    status_col = sheets.COLUMNS.index("status")
    assert fake.data[-1][status_col] == "archived", fake.data[-1][status_col]
    print(f"[ok] прошедшее событие: status={fake.data[-1][status_col]}")

    # старые строки, не участвующие в прогоне, тоже уходят в архив
    fake.data.append([""] * len(sheets.COLUMNS))
    stale_row = fake.data[-1]
    stale_row[0] = "stale00000000001"
    stale_row[sheets.COLUMNS.index("title")] = "Прошлогодняя выставка"
    stale_row[sheets.COLUMNS.index("date_end")] = "2025-11-29"
    stale_row[status_col] = "active"
    store._load_index()
    archived = store.archive_stale(today=__import__("datetime").date(2026, 10, 29))
    assert archived == 1, f"архивировано {archived}, ожидали 1"
    assert fake.data[-1][status_col] == "archived", fake.data[-1][status_col]
    print(f"[ok] просроченные строки: в архив переведено {archived}")

    # одна страница — несколько событий: два источника с той же ссылкой
    # должны занять две строки, а не перезаписать друг друга
    page_url = "https://nornd.ru/afisha-rossijsko-nemetskogo-doma/"
    page = [
        event(source="РНД", title="Первый Адвент", source_url=page_url,
              date_raw="29 ноября 2026, 12:00–16:00"),
        event(source="НаДвиж", title="Тёплое рождество — ярмарка",
              source_url=page_url, date_raw="29 ноября 2026, 12:00–16:00"),
    ]
    before = len(fake.data)
    same_page = store.upsert(page)
    assert same_page["new"] == 2, same_page
    assert len(fake.data) == before + 2, f"страниц со ссылкой: {len(fake.data) - before}"
    print(f"[ok] общая страница у двух источников: добавлено {same_page['new']} строки")

    print("\nВсе проверки пройдены: чтение одно на прогон, дублей нет, обновления работают.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
