"""Запись в Google Sheets: схема v2, upsert, архив прошедших, лог прогонов.

Ключевые решения (исправляют ошибку 429 «Quota exceeded», возникавшую при
чтении каждой строки отдельным запросом):

  * таблица читается ОДИН раз через get_all_values, сравнение идёт в памяти;
  * изменения пишутся пакетами batch_update (до 100 диапазонов за запрос);
  * новые строки добавляются чанками по 200;
  * на 429 и временные ошибки — пауза и повтор (5 → 10 → 20 → 40 секунд);
  * данные и лог прогонов живут на разных листах и не могут перепутаться.
"""

from __future__ import annotations

import csv
import re
import time
from datetime import date, datetime
from pathlib import Path

from .model import COLUMNS
from . import text as textmod

SHEET_NAME = "nsk_weekend"
DATA_TAB = "events"
RUNS_TAB = "runs"
# Неактивные события (archived, cancelled, rescheduled) не держим в рабочем
# листе: они не нужны ни сборке сайта, ни модерации, но остаются в книге
# на отдельном листе, чтобы историю можно было посмотреть.
ARCHIVE_TAB = "archive"
# Снимок того, что парсер предлагал в прошлый раз: по нему видно, какие
# ячейки человек правил руками, и такие правки не затираются.
BASE_TAB = "parsed"
# Id строк прошлого прогона: по нему видно, что человек удалил из таблицы.
# Лежит в книге, а не в файле: файл в контейнере GitHub Actions не переживает
# прогон, из-за чего удаления в облаке не запоминались вовсе.
SNAPSHOT_TAB = "snapshot"
# Служебные колонки пишем всегда от парсера: id — ключ строки, остальное —
# отметки времени. Их правка ломает сопоставление и сравнение.
SERVICE_COLUMNS = ("id", "parsed_at", "checked_at")
# Насколько похожими должны быть названия, чтобы считать строки одним событием:
# «История Башни» и «Еженедельные экскурсии „История Башня“» — дубль.
TWIN_RATIO = 0.82
RUNS_COLUMNS = ["run_at", "source", "collected", "kept", "new", "updated", "errors", "note"]
CREDENTIALS = "credentials.json"
BATCH = 200
UPDATE_BATCH = 100
RETRY_DELAYS = (5, 10, 20, 40)
# Доступ к googleapis.com бывает недоступен или сильно тормозит:
# без явного таймаута запрос висит бесконечно (read timeout=None).
SHEETS_TIMEOUT = 30


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "" if value is None else str(value)


def _cell(value) -> str:
    return _fmt(value)


def _title_key(title: str, start: str) -> str:
    """Ключ «название + дата начала» для сопоставления строк.

    Нужен, когда в таблице поправили ссылку: id и url уже не совпадут,
    а событие осталось тем же.
    """
    norm = re.sub(r"[^0-9a-zа-яё]+", " ", (title or "").lower()).strip()
    if len(norm) < 5 or not start:
        return ""
    return f"{norm}|{start}"


def _retry(func, *args, **kwargs):
    """Повтор на 429 и временных ошибках Google API."""
    last_error = None
    for delay in (0, *RETRY_DELAYS):
        if delay:
            print(f"[i] Лимит Google API, пауза {delay} c и повтор…")
            time.sleep(delay)
        try:
            return func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_error = e
            code = getattr(e, "code", None) or getattr(getattr(e, "response", None), "status_code", None)
            text = str(e)
            if code == 429 or "429" in text or "Quota exceeded" in text or "503" in text:
                continue
            raise
    raise last_error  # type: ignore[misc]


def _source_prio(name: str) -> int:
    """Меньше число — первичнее источник. Незнакомые имена считаем средними."""
    try:
        from config import SOURCES
    except Exception:  # noqa: BLE001
        return 50
    for src in SOURCES:
        if src.name == name:
            return src.prio
    return 50


def _prefix_clash(left: str, right: str, size: int = 3) -> bool:
    """Названия начинаются одинаково: «разговорный клуб английского …»."""
    a, b = left.split(), right.split()
    return len(a) >= size and len(b) >= size and a[:size] == b[:size]


class SheetStore:
    """Обёртка над gspread с upsert. Или CSV-фолбэк, если нет доступа."""

    def __init__(self, name: str = SHEET_NAME, creds: str = CREDENTIALS,
                 csv_fallback: str = "out/nsk_weekend.csv", use_google: bool = True):
        self.name = name
        self.csv_fallback = Path(csv_fallback)
        self.sheet = None
        self.book = None
        self.existing: dict[str, int] = {}
        self.by_url: dict[str, int] = {}
        self.by_title_date: dict[str, int | None] = {}
        self.twins: list[tuple[int, str, str, str, str, str]] = []
        self.rows: list[list[str]] = []
        # Снимок прошлого прогона: по нему отличаем ручные правки от данных.
        self.base_rows: dict[str, list] = {}
        self.base_by_url: dict[str, list] = {}
        self.ws_rows = 0
        if not use_google:
            print("[i] Режим CSV: Google Sheets не используется")
            return
        try:
            import gspread

            client = gspread.service_account(filename=creds)
            client.set_timeout(SHEETS_TIMEOUT)
            self.book = client.open(name)
            self.sheet = self._open_data_sheet()
            self._ensure_header()
            self._load_index()
            print(f"[+] Google Sheets: лист «{self.sheet.title}», строк {self.ws_rows}")
        except Exception as e:  # noqa: BLE001
            print(f"[!] Google Sheets недоступен ({type(e).__name__}: {e}). Работаю в CSV-режиме.")
            self.sheet = None
            self.book = None

    # ------------------------------------------------------------ служебное
    def _open_data_sheet(self):
        """Лист с данными: events → первый лист (если это не лог) → новый лист."""
        book = self.book
        titles = {ws.title: ws for ws in book.worksheets()}

        if DATA_TAB in titles:
            return titles[DATA_TAB]

        first = book.sheet1
        header = _retry(first.row_values, 1)
        looks_like_data = bool(header) and header[0].strip() == COLUMNS[0]
        if looks_like_data:
            if first.title != DATA_TAB:
                print(f"[!] Данные лежат в листе «{first.title}». Пишу в него; "
                      f"чтобы было понятно — переименуйте лист в «{DATA_TAB}».")
            return first

        ws = _retry(book.add_worksheet, title=DATA_TAB, rows=5000, cols=len(COLUMNS))
        print(f"[+] Создан лист данных «{DATA_TAB}»")
        return ws

    def _ensure_header(self) -> None:
        header = _retry(self.sheet.row_values, 1)
        if header[: len(COLUMNS)] == COLUMNS:
            return
        if not any(v.strip() for v in header):
            _retry(self.sheet.update, "A1", [COLUMNS])
            print("[+] Записана шапка схемы v2")
            return
        last_col = _col_letter(len(COLUMNS))
        print(f"[!] Шапка отличается от схемы v2 (было: {header[:5]}…). Обновляю A1:{last_col}1.")
        _retry(self.sheet.update, f"A1:{last_col}1", [COLUMNS])

    def _load_index(self) -> None:
        """Одно чтение всей таблицы — вместо чтения каждой строки."""
        values = _retry(self.sheet.get_all_values)
        self.rows = values
        self.ws_rows = len(values)
        self.existing = {}
        self.by_url = {}
        self.by_title_date = {}
        url_col = COLUMNS.index("source_url")
        title_col = COLUMNS.index("title")
        start_col = COLUMNS.index("date_start")
        end_col = COLUMNS.index("date_end")
        venue_col = COLUMNS.index("venue")
        time_col = COLUMNS.index("time_text")
        status_col = COLUMNS.index("status")
        # Для поиска межуточных дублей: агрегатор и площадка называют одно
        # событие по-разному, но дата начала и площадка у них совпадают.
        self.twins: list[tuple[int, str, str, str, str, str]] = []
        ambiguous_urls: set[str] = set()
        for idx, row in enumerate(values[1:], start=2):
            if row and row[0].strip():
                self.existing[row[0].strip()] = idx
                if len(row) > url_col and row[url_col].strip():
                    url = row[url_col].strip()
                    # Одна страница может описывать несколько событий (у музея
                    # на одной странице четыре экспозиции). Такую ссылку в
                    # индекс не берём: иначе все события схлопнутся в одну
                    # строку, и в таблице останется последнее.
                    if url in self.by_url:
                        ambiguous_urls.add(url)
                    else:
                        self.by_url[url] = idx
                # Третья попытка сопоставления — на случай, если в таблице
                # поправили ссылку: ищем по названию и дате начала.
                key = _title_key(
                    row[title_col] if len(row) > title_col else "",
                    row[start_col] if len(row) > start_col else "",
                )
                if key:
                    self.by_title_date[key] = idx if key not in self.by_title_date else None
                status = row[status_col] if len(row) > status_col else ""
                if status == "active":
                    norm = textmod.normalize_title(
                        row[title_col] if len(row) > title_col else "")
                    start = row[start_col] if len(row) > start_col else ""
                    # Строки без даты тоже участвуют в сверке: у выставок
                    # «постоянно» её нет, и агрегатор иначе дублировал площадку.
                    if norm:
                        self.twins.append((
                            idx, norm, start,
                            row[end_col] if len(row) > end_col else "",
                            (row[time_col] if len(row) > time_col else "")[:5],
                            row[venue_col] if len(row) > venue_col else "",
                        ))
        for url in ambiguous_urls:
            self.by_url.pop(url, None)

    def _find_twin(self, ev: dict) -> int | None:
        """Активная строка о том же событии: пересекающиеся даты, похожее название и площадка."""
        norm = textmod.normalize_title(ev.get("title", ""))
        start = ev.get("date_start", "") or ""
        if not norm:
            return None
        end = ev.get("date_end") or start
        venue = (ev.get("venue") or "").lower()
        time_text = (ev.get("time_text") or "")[:5]
        for idx, other_norm, other_start, other_end, other_time, other_venue in self.twins:
            # Агрегатор и площадка часто ставят разные рамки у одной выставки,
            # поэтому сравниваем не начало, а пересечение периодов. Пустая дата
            # у одной из сторон не повод считать события разными: у выставок
            # «постоянно» её нет, и карточка агрегатора иначе не опознавалась.
            if start and other_start:
                if (other_end or other_start) < start or end < other_start:
                    continue
            # Точное совпадение названия — дубль даже при разных названиях
            # площадки: агрегатор пишет её по-своему.
            if norm == other_norm:
                return idx
            other_venue = (other_venue or "").lower()
            same_venue = not (venue and other_venue) or (
                venue in other_venue
                or other_venue in venue
                or textmod.similar(venue, other_venue) >= 0.6
            )
            if same_venue and textmod.similar(norm, other_norm) >= TWIN_RATIO:
                return idx
            # Площадка и агрегатор по-разному договаривают одно название:
            # «Разговорный клуб английского языка для начинающих» и «… Speakify».
            # Общие первые слова при той же площадке, дате и времени — дубль.
            if (
                same_venue
                and time_text
                and time_text == other_time
                and _prefix_clash(norm, other_norm)
            ):
                return idx
        return None

    # ------------------------------------------------- снимок прогона
    def _snapshot_sheet(self):
        try:
            return self.book.worksheet(SNAPSHOT_TAB)
        except Exception:  # noqa: BLE001
            ws = self.book.add_worksheet(title=SNAPSHOT_TAB, rows=3000, cols=2)
            ws.update(values=[["id", "saved_at"]], range_name="A1:B1")
            return ws

    def snapshot_load(self) -> set[str]:
        """Id строк, которые были в таблице после прошлого прогона."""
        if self.book is None:
            return set()
        try:
            values = self._snapshot_sheet().get_all_values()
        except Exception as e:  # noqa: BLE001
            print(f"[!] Снимок не прочитан: {e}")
            return set()
        return {row[0].strip() for row in values[1:] if row and row[0].strip()}

    def snapshot_save(self, ids: set[str]) -> None:
        """Перезаписывает снимок целиком: список id после текущего прогона."""
        if self.book is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [[key, stamp] for key in sorted(ids)]
        try:
            ws = self._snapshot_sheet()
            _retry(ws.batch_clear, ["A2:B"])
            for start in range(0, len(rows), 2000):
                chunk = rows[start:start + 2000]
                _retry(ws.update, values=chunk, range_name=f"A{start + 2}",
                       value_input_option="RAW")
        except Exception as e:  # noqa: BLE001
            print(f"[!] Снимок не записан: {e}")

    def _log_sheet(self):
        """Лист прогонов; никогда не совпадает с листом данных."""
        name = RUNS_TAB if self.sheet.title != RUNS_TAB else f"{RUNS_TAB}_log"
        try:
            return self.book.worksheet(name)
        except Exception:  # noqa: BLE001
            ws = self.book.add_worksheet(title=name, rows=2000, cols=len(RUNS_COLUMNS))
            ws.append_row(RUNS_COLUMNS)
            return ws

    # ------------------------------------------------- ручные правки
    def _base_worksheet(self):
        """Лист-снимок `parsed`: что парсер предлагал в прошлый раз."""
        try:
            return self.book.worksheet(BASE_TAB)
        except Exception:  # noqa: BLE001
            ws = self.book.add_worksheet(title=BASE_TAB, rows=2000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)
            return ws

    def _load_base(self) -> None:
        """Читаем снимок и раскладываем по id и по ссылке."""
        self.base_rows: dict[str, list] = {}
        self.base_by_url: dict[str, list] = {}
        if self.sheet is None:
            return
        try:
            values = _retry(self._base_worksheet().get_all_values)
        except Exception as e:  # noqa: BLE001
            print(f"[!] Снимок для ручных правок недоступен ({e}) — "
                  f"правки в этом прогоне могут затереться.")
            return
        url_col = COLUMNS.index("source_url")
        for row in values[1:]:
            key = (row[0] if row else "").strip()
            if not key:
                continue
            self.base_rows[key] = row
            url = (row[url_col] if len(row) > url_col else "").strip()
            if url:
                self.base_by_url[url] = row

    def _base_for(self, event: dict, url: str) -> list | None:
        base = self.base_rows.get(event.get("id", ""))
        if base is not None:
            return base
        return self.base_by_url.get(url) if url else None

    def _merge_row(self, new_row: list, sheet_row: list, base_row: list | None) -> list:
        """Правило: что человек поправил руками — не затираем.

        Сравниваем таблицу со снимком прошлого прогона. Если значение
        отличается от того, что писал парсер, — это ручная правка, её и
        оставляем. Пустая ячейка считается сбросом: вернётся значение парсера.
        """
        if base_row is None:
            # Снимка нет: строку писал не парсер — её внесли руками или
            # перенесли выгрузкой. Такие не перезаписываем, только заполняем
            # пустые ячейки: иначе следующий прогон затрёт готовую карточку.
            merged = list(new_row)
            for i, col in enumerate(COLUMNS):
                if col in SERVICE_COLUMNS or i >= len(sheet_row):
                    continue
                if (sheet_row[i] or "").strip():
                    merged[i] = sheet_row[i]
            return merged
        merged = list(new_row)
        for i, col in enumerate(COLUMNS):
            if col in SERVICE_COLUMNS or i >= len(sheet_row) or i >= len(base_row):
                continue
            current = (sheet_row[i] or "").strip()
            before = (base_row[i] or "").strip()
            if current and current != before:
                merged[i] = sheet_row[i]
        return merged

    def _remember_base(self, row: list) -> None:
        key = (row[0] if row else "").strip()
        if not key:
            return
        self.base_rows[key] = row
        url_col = COLUMNS.index("source_url")
        url = (row[url_col] if len(row) > url_col else "").strip()
        if url:
            self.base_by_url[url] = row

    def _save_base(self) -> None:
        """Снимок — это то, что парсер предлагал, а не то, что стоит в таблице."""
        if self.sheet is None or not self.base_rows:
            return
        width = len(COLUMNS)
        values = [COLUMNS] + [
            (row[:width] + [""] * (width - len(row[:width]))) for row in self.base_rows.values()
        ]
        try:
            ws = self._base_worksheet()
            _retry(ws.clear)
            _retry(ws.update, values, "A1")
        except Exception as e:  # noqa: BLE001
            print(f"[!] Снимок для ручных правок не сохранён: {e}")

    # ------------------------------------------------------------ запись
    def upsert(self, events: list[dict], today: date | None = None) -> dict:
        """Добавляет новые и обновляет изменившиеся. Возвращает статистику."""
        today = today or date.today()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats = {"new": 0, "updated": 0, "archived": 0}

        if self.sheet is None:
            self._write_csv(events)
            stats["new"] = len(events)
            return stats

        to_append: list[list] = []
        updates: list[tuple[str, list]] = []
        last_col = _col_letter(len(COLUMNS))
        self._load_base()
        title_col = COLUMNS.index("title")
        start_col = COLUMNS.index("date_start")

        for ev in events:
            row = [ev.get(col, "") for col in COLUMNS]
            row[COLUMNS.index("parsed_at")] = ev.get("parsed_at") or now
            if ev.get("date_end") and ev["date_end"] < today.isoformat():
                row[COLUMNS.index("status")] = "archived"
            row = [_fmt(v) for v in row]
            url = (ev.get("source_url") or "").strip()
            # Снимок читаем ДО перезаписи: иначе базой станет эта же новая
            # строка, и любое старое значение в таблице выглядело бы как
            # ручная правка, которую нельзя трогать.
            base = self._base_for(ev, url)
            self._remember_base(row)

            idx = self.existing.get(ev["id"])
            if idx is None and url:
                idx = self.by_url.get(url)
            if idx is None:
                # Ссылку могли поправить руками — тогда ищем по названию и дате.
                key = _title_key(row[title_col], row[start_col])
                idx = self.by_title_date.get(key) if key else None
            if idx is None:
                twin = self._find_twin(ev)
                if twin is not None:
                    source_col = COLUMNS.index("source")
                    old_row = self.rows[twin - 1]
                    old_source = old_row[source_col] if len(old_row) > source_col else ""
                    if _source_prio(ev.get("source", "")) < _source_prio(old_source):
                        # Новый источник первичнее: занимаем строку агрегатора.
                        idx = twin
                    else:
                        # Карточка от площадки уже есть, агрегатор её не дублирует.
                        stats["skipped"] = stats.get("skipped", 0) + 1
                        continue
            if idx is None:
                to_append.append(row)
                stats["new"] += 1
                continue

            old = self.rows[idx - 1] if 0 <= idx - 1 < len(self.rows) else []
            if ev.get("_image_unchecked"):
                # Прогон с --no-images: подтверждённую афишу не сбрасываем в FALSE.
                ok_col = COLUMNS.index("image_ok")
                if len(old) > ok_col and (old[ok_col] or "").strip().upper() == "TRUE":
                    row[ok_col] = old[ok_col]
            merged = self._merge_row(row, old, base)
            if _changed(old, merged):
                updates.append((f"A{idx}:{last_col}{idx}", [merged]))
                stats["updated"] += 1

        for start in range(0, len(to_append), BATCH):
            chunk = to_append[start:start + BATCH]
            _retry(self.sheet.append_rows, chunk, value_input_option="USER_ENTERED")

        for start in range(0, len(updates), UPDATE_BATCH):
            chunk = updates[start:start + UPDATE_BATCH]
            body = [{"range": rng, "values": vals} for rng, vals in chunk]
            _retry(self.sheet.batch_update, body, value_input_option="USER_ENTERED")

        stats["archived"] = sum(
            1 for ev in events if ev.get("date_end") and ev["date_end"] < today.isoformat()
        )
        self._save_base()
        if to_append or updates:
            self._load_index()
        return stats

    def reconcile(self, events: list[dict], sources: set[str] | None = None) -> int:
        """Снимает с публикации то, чего больше нет в текущем прогоне.

        upsert только добавляет и обновляет, поэтому событие, которое источник
        перестал отдавать или которое отсеял фильтр (например, спорт), висело
        в таблице и на сайте бесконечно.

        Источники, не ответившие в этом прогоне, не трогаем: иначе упавший
        сайт стирал бы все свои события разом.
        """
        if self.sheet is None:
            return 0

        current = {ev["id"] for ev in events}
        status_idx = COLUMNS.index("status")
        source_idx = COLUMNS.index("source")
        letter = _col_letter(status_idx + 1)

        updates: list[tuple[str, list]] = []
        for idx, row in enumerate(self.rows[1:], start=2):
            if not row or not row[0].strip():
                continue
            if row[0].strip() in current:
                continue
            src = (row[source_idx] if len(row) > source_idx else "").strip()
            if sources is not None and src not in sources:
                continue
            status = (row[status_idx] if len(row) > status_idx else "").strip()
            if status in ("archived", "cancelled", "rescheduled"):
                continue
            # Строку, которой нет в снимке, парсер не писал — значит, её
            # добавили руками. Такие не снимаем: человек знает, что делает.
            if self.base_rows and row[0].strip() not in self.base_rows:
                continue
            updates.append((f"{letter}{idx}", [["archived"]]))
            # Это наша правка, а не ручная: переносим её в снимок, иначе на
            # следующем прогоне она выглядела бы как правка человека и
            # закрепилась бы навсегда.
            base = self.base_rows.get(row[0].strip())
            if base is not None and len(base) > status_idx:
                base[status_idx] = "archived"

        for start in range(0, len(updates), UPDATE_BATCH):
            chunk = updates[start:start + UPDATE_BATCH]
            body = [{"range": rng, "values": vals} for rng, vals in chunk]
            _retry(self.sheet.batch_update, body, value_input_option="USER_ENTERED")

        if updates:
            self._save_base()
            self._load_index()
        return len(updates)

    # ------------------------------------------------------------- архив
    def _archive_worksheet(self):
        """Лист archive: создаётся при первом переносе, шапка как у events."""
        titles = {ws.title for ws in self.book.worksheets()}
        if ARCHIVE_TAB in titles:
            return self.book.worksheet(ARCHIVE_TAB)
        ws = _retry(self.book.add_worksheet, title=ARCHIVE_TAB,
                    rows=5000, cols=len(COLUMNS))
        _retry(ws.append_row, COLUMNS, value_input_option="USER_ENTERED")
        return ws

    def _archived_ids(self) -> set[str]:
        """`id`, которые уже есть в листе archive."""
        if self.book is None:
            return set()
        titles = {ws.title for ws in self.book.worksheets()}
        if ARCHIVE_TAB not in titles:
            return set()
        values = _retry(self.book.worksheet(ARCHIVE_TAB).get_all_values)
        return {(row[0] or "").strip() for row in values[1:] if row}

    def move_inactive(self, dry_run: bool = False) -> int:
        """Переносит неактивные строки из events в лист archive.

        Сначала копируем в архив, только потом удаляем из рабочего листа:
        если запись в архив не удалась, данные остаются на месте.
        Сборке и модерации неактивные строки не нужны, а история сохраняется.
        """
        if self.sheet is None:
            return 0
        status_idx = COLUMNS.index("status")
        inactive = {"archived", "cancelled", "rescheduled"}
        targets: list[tuple[int, list]] = []
        for idx, row in enumerate(self.rows[1:], start=2):
            if not row or not row[0].strip():
                continue
            status = (row[status_idx] if len(row) > status_idx else "active").strip()
            if status in inactive:
                targets.append((idx, row))
        if not targets:
            return 0

        # Событие могло вернуться в events из файла ручных событий и снова
        # уйти в архив: копию второй раз не пишем, но из events убираем.
        archived_ids = self._archived_ids()
        to_copy = [(idx, row) for idx, row in targets if row[0].strip() not in archived_ids]
        already = len(targets) - len(to_copy)

        if dry_run:
            print(f"[dry-run] в лист {ARCHIVE_TAB} уйдёт строк: {len(to_copy)}, "
                  f"уже в архиве и будут только удалены: {already}")
            return len(targets)

        width = len(COLUMNS)
        payload = [
            [_fmt(cell) for cell in (row + [""] * width)[:width]]
            for _, row in to_copy
        ]
        if payload:
            ws = self._archive_worksheet()
            for start in range(0, len(payload), BATCH):
                _retry(ws.append_rows, payload[start:start + BATCH],
                       value_input_option="USER_ENTERED")
        # Удаляем одним batchUpdate по непрерывным диапазонам: построчный
        # delete_rows — это запрос на каждую строку, а квота 60 записей в минуту
        # на пользователя. Диапазоны идут сверху вниз по убыванию, чтобы
        # удаление верхних не сдвигало номера нижних.
        ranges: list[list[int]] = []
        for idx in sorted(row_idx for row_idx, _ in targets):
            if ranges and idx == ranges[-1][1] + 1:
                ranges[-1][1] = idx
            else:
                ranges.append([idx, idx])
        requests_body = [
            {"deleteDimension": {"range": {
                "sheetId": self.sheet.id,
                "dimension": "ROWS",
                "startIndex": start - 1,
                "endIndex": end,
            }}}
            for start, end in sorted(ranges, key=lambda pair: pair[0], reverse=True)
        ]
        if requests_body:
            _retry(self.sheet.spreadsheet.batch_update, {"requests": requests_body})
        self._load_index()
        return len(targets)

    def log_run(self, source: str, collected: int, kept: int, new: int,
                updated: int, errors: int, note: str = "") -> None:
        if self.sheet is None:
            return
        try:
            _retry(self._log_sheet().append_row, [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), source, collected,
                kept, new, updated, errors, note[:200],
            ])
        except Exception as e:  # noqa: BLE001
            print(f"[!] Лог прогона не записан: {e}")

    def archive_stale(self, today: date | None = None) -> int:
        """Помечает archived всё, что уже закончилось, включая старые строки.

        Нужно из-за прошлогодних записей, попавших в таблицу раньше (ЦК19 без
        годов): они не участвуют в текущем прогоне, но иначе висят active.
        """
        today = today or date.today()
        if self.sheet is None:
            return 0

        iso = today.isoformat()
        end_col = COLUMNS.index("date_end")
        status_col = COLUMNS.index("status")
        letter = _col_letter(status_col + 1)

        updates: list[tuple[str, list]] = []
        for idx, row in enumerate(self.rows[1:], start=2):
            if len(row) <= end_col:
                continue
            end = (row[end_col] or "").strip()
            status = (row[status_col] if len(row) > status_col else "").strip()
            if end and end < iso and status not in ("archived", "cancelled", "rescheduled"):
                updates.append((f"{letter}{idx}", [["archived"]]))
                base = self.base_rows.get(row[0].strip()) if row else None
                if base is not None and len(base) > status_col:
                    base[status_col] = "archived"

        for start in range(0, len(updates), UPDATE_BATCH):
            chunk = updates[start:start + UPDATE_BATCH]
            body = [{"range": rng, "values": vals} for rng, vals in chunk]
            _retry(self.sheet.batch_update, body, value_input_option="USER_ENTERED")

        if updates:
            self._save_base()
            self._load_index()
        return len(updates)

    # ------------------------------------------------------------ CSV
    def _write_csv(self, events: list[dict]) -> None:
        self.csv_fallback.parent.mkdir(parents=True, exist_ok=True)
        rows = [[_cell(ev.get(col, "")) for col in COLUMNS] for ev in events]
        with self.csv_fallback.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
        print(f"[+] CSV сохранён: {self.csv_fallback} ({len(rows)} строк)")


def read_table(name: str = SHEET_NAME, creds: str = CREDENTIALS,
               tab: str | None = None) -> list[dict]:
    """Читает лист событий в список словарей — для сборки сайта без CSV.

    Возвращает строки как есть, ключи — заголовки колонок. Ошибки доступа
    пробрасываются наверх: вызывающий код решает, что делать (обычно — взять CSV).
    """
    import gspread

    client = gspread.service_account(filename=creds)
    client.set_timeout(SHEETS_TIMEOUT)
    book = client.open(name)

    if tab:
        worksheet = book.worksheet(tab)
    else:
        titles = {ws.title: ws for ws in book.worksheets()}
        worksheet = titles.get(DATA_TAB) or book.sheet1

    values = _retry(worksheet.get_all_values)
    if not values:
        return []

    header = [str(h).strip() for h in values[0]]
    rows: list[dict] = []
    for row in values[1:]:
        # Пропускаем только полностью пустые строки. Раньше здесь проверялась
        # первая ячейка (id), и события, добавленные руками без id, молча
        # выпадали из сборки: на сайте их не было, хотя в таблице они лежали.
        if not row or not any(str(cell).strip() for cell in row):
            continue
        rows.append({
            header[i]: (row[i] if i < len(row) else "")
            for i in range(len(header))
        })
    return rows


def _changed(old: list[str], new: list[str]) -> bool:
    """Сравниваем только содержательные поля, служебные не считаем изменением."""
    ignore = {COLUMNS.index("parsed_at"), COLUMNS.index("checked_at")}
    for i, value in enumerate(new):
        if i in ignore:
            continue
        prev = old[i].strip() if i < len(old) else ""
        if prev != str(value).strip():
            return True
    return False


def _col_letter(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
