"""Единый разбор дат и периодов для всех парсеров.

Все 12 старых парсеров имели свою копию analyze_date_and_type с разной
семантикой (где-то +45 дней, где-то break на архиве). Здесь один контракт:

    p = parse_period("15 октября - 15 ноября")
    p.start, p.end, p.kind, p.is_relevant(today)

Поддерживаемые форматы:
    29.10_29.11            (ЦК19, выставка)
    30.10_18:30            (ЦК19, событие)
    31.10.2026 - 12:00     (планетарий)
    1 октября - 31 декабря
    31 октября 2026
    17 сентября – 15 ноября
    29–31 октября          (диапазон в одном месяце)
    1 окт 2026 — 1 ноя 2026 (ТИЦ)
    12.06 – 31.12
    Постоянная экспозиция
    Каждую субботу в 13:00 (ближайшее: 31.10)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MONTH_PATTERN = (
    r"(?:январ[ья]|феврал[ья]|марта|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|августа|"
    r"сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]|"
    r"янв|фев|мар|апр|июн|июл|авг|сен|сент|окт|ноя|дек)"
)

_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "сент": 9,
    "окт": 10, "ноя": 11, "дек": 12,
}

_RE_CC19_RANGE = re.compile(
    r"^\s*(\d{1,2})\.(\d{1,2})\.?_\s*(\d{1,2})\.(\d{1,2})\.?\s*$"
)
_RE_CC19_DT = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.?_\s*(\d{1,2}):(\d{2})\s*$")
_RE_SAME_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+({MONTH_PATTERN})", re.IGNORECASE
)
_RE_TOKEN = re.compile(
    rf"(?:(?P<num_d>\d{{1,2}})\s*[./]\s*(?P<num_m>\d{{1,2}})"
    rf"(?:\s*[./]\s*(?P<num_y>\d{{2,4}}))?)"
    rf"|(?:(?P<wd_d>\d{{1,2}})\s+(?P<wd_m>{MONTH_PATTERN})(?:\s+(?P<wd_y>\d{{4}}))?)",
    re.IGNORECASE,
)
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_RE_NEAR = re.compile(r"ближайшее:\s*(\d{1,2})\.(\d{1,2})", re.IGNORECASE)
_RE_WEEKDAY = re.compile(r"кажд\w*\s+(?:по\s+)?(суббот|воскресень|пятниц|понедельник|вторник|сред)", re.IGNORECASE)
_RE_PERMANENT = re.compile(r"постоянн", re.IGNORECASE)
_RE_RECURRING = re.compile(
    r"(кажд\w+|по\s+(субботам|воскресеньям|выходным)|ежедневно|еженедельно|ежегодно)",
    re.IGNORECASE,
)
_BREADCRUMB_PREFIX = re.compile(
    r"^(?:открытие|закрытие|выставка работает|с|по|до|c)\s+", re.IGNORECASE
)

_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскресень": 6,
}


@dataclass
class Period:
    start: date | None = None
    end: date | None = None
    kind: str = "unknown"          # one_time | long_term | permanent | recurring | unknown
    time_text: str = ""
    raw: str = ""

    def is_past(self, today: date | None = None) -> bool:
        today = today or date.today()
        end = self.end or self.start
        if self.kind == "permanent":
            return False
        return bool(end and end < today)

    def is_relevant(self, today: date | None = None) -> bool:
        """Пересекается ли период с ближайшими выходными (и не в прошлом)."""
        today = today or date.today()
        if self.kind == "permanent":
            return True
        start = self.start
        end = self.end or self.start
        if not start or not end:
            return False
        if end < today:
            return False
        sat, sun = weekend(today)
        window_start = max(sat, today)
        return start <= sun and end >= window_start

    @property
    def start_iso(self) -> str:
        return self.start.isoformat() if self.start else ""

    @property
    def end_iso(self) -> str:
        return self.end.isoformat() if self.end else ""


def weekend(today: date | None = None) -> tuple[date, date]:
    """Суббота и воскресенье текущей недели (в выходные — начиная с сегодня)."""
    today = today or date.today()
    wd = today.weekday()
    if wd == 5:
        sat = today
    elif wd == 6:
        sat = today - timedelta(days=1)
    else:
        sat = today + timedelta(days=5 - wd)
    return sat, sat + timedelta(days=1)


def _month_from_word(word: str) -> int | None:
    key = word.lower().strip(".")
    if key in _MONTHS:
        return _MONTHS[key]
    return _MONTHS.get(key[:3])


def _mk(day: int, month: int, year: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _tokens(text: str) -> list[tuple[int, int, int | None]]:
    """Все даты в строке в порядке появления: (день, месяц, год|None)."""
    out: list[tuple[int, int, int | None]] = []
    for m in _RE_TOKEN.finditer(text):
        gd = m.group("num_d") or m.group("wd_d")
        gm = m.group("num_m") or m.group("wd_m")
        gy = m.group("num_y") or m.group("wd_y")
        if not gd or not gm:
            continue
        try:
            day = int(gd)
        except ValueError:
            continue
        if gm.isdigit():
            month = int(gm)
        else:
            month = _month_from_word(gm)
        if not month or not (1 <= month <= 12) or not (1 <= day <= 31):
            continue
        year = int(gy) if gy and gy.isdigit() else None
        if year and year < 100:
            year += 2000
        out.append((day, month, year))
    return out


def _year_for_single(day: int, month: int, year: int | None, today: date) -> date | None:
    if year:
        return _mk(day, month, year)
    dt = _mk(day, month, today.year)
    # Дата без года глубоко в прошлом — скорее всего, речь о следующем годе
    # («15 января» в декабре). Но недавняя прошедшая дата — это прошедшее
    # событие, а не будущее, поэтому порог большой.
    if dt and (today - dt).days > 180:
        dt = _mk(day, month, today.year + 1)
    return dt


def _year_for_range(
    d1: int, m1: int, y1: int | None,
    d2: int, m2: int, y2: int | None,
    today: date,
) -> tuple[date | None, date | None]:
    start = _mk(d1, m1, y1 or today.year)
    end = _mk(d2, m2, y2 or today.year)
    if start and end and end < start:
        end = _mk(d2, m2, end.year + 1)
    return start, end


def _extract_time(text: str) -> str:
    m = _RE_TIME.search(text)
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return f"{h:02d}:{mi:02d}"
    return ""


def parse_period(raw: str, today: date | None = None, kind_hint: str = "") -> Period:
    """Главная функция: строка с датой -> Period."""
    today = today or date.today()
    raw = (raw or "").strip()
    p = Period(raw=raw)
    if not raw:
        return p

    text = re.sub(r"\s+", " ", raw).replace("\u00a0", " ")

    # 1. Постоянная экспозиция
    if _RE_PERMANENT.search(text):
        p.kind = "permanent"
        return p

    # 2. ЦК19: 30.10_18:30 и 29.10_29.11
    m = _RE_CC19_DT.match(text)
    if m:
        d, mo, h, mi = (int(x) for x in m.groups())
        start = _year_for_single(d, mo, None, today)
        if start:
            p.start = p.end = start
            p.kind = kind_hint or "one_time"
            p.time_text = f"{h:02d}:{mi:02d}"
        return p

    m = _RE_CC19_RANGE.match(text)
    if m:
        d1, m1, d2, m2 = (int(x) for x in m.groups())
        start, end = _year_for_range(d1, m1, None, d2, m2, None, today)
        if start and end:
            p.start, p.end = start, end
            p.kind = "long_term" if (end - start).days >= 3 else "one_time"
        return p

    p.time_text = _extract_time(text)

    # 3. Повторяющиеся события
    if _RE_RECURRING.search(text) or _RE_WEEKDAY.search(text):
        p.kind = "recurring"
        near = _RE_NEAR.search(text)
        if near:
            p.start = p.end = _year_for_single(
                int(near.group(1)), int(near.group(2)), None, today
            )
        else:
            wd_m = _RE_WEEKDAY.search(text)
            target = 5
            if wd_m:
                word = wd_m.group(1).lower()
                for k, v in _WEEKDAYS.items():
                    if word.startswith(k[:6]):
                        target = v
                        break
            days = (target - today.weekday()) % 7
            p.start = p.end = today + timedelta(days=days)
        return p

    # 4. "29–31 октября" -> разворачиваем в "29 октября - 31 октября"
    text = _RE_SAME_MONTH.sub(r"\1 \3 - \2 \3", text)

    toks = _tokens(text)
    if not toks:
        return p

    if len(toks) == 1:
        d, mo, y = toks[0]
        start = _year_for_single(d, mo, y, today)
        p.start = p.end = start
        if start:
            p.kind = kind_hint or "one_time"
        return p

    # два и более токена -> берём первый и последний как границы периода
    d1, m1, y1 = toks[0]
    d2, m2, y2 = toks[-1]
    start, end = _year_for_range(d1, m1, y1, d2, m2, y2, today)
    p.start, p.end = start, end
    if start and end:
        # Длительность важнее подсказки раздела: событие на 3+ дней — это
        # длинный период, даже если раздел называется «события».
        if (end - start).days >= 3:
            p.kind = "long_term"
        elif kind_hint:
            p.kind = kind_hint
        else:
            p.kind = "one_time"
    return p


def parse_datetime(raw: str, today: date | None = None) -> datetime | None:
    """Дата + время одной величиной (для сортировки и слотов сеансов)."""
    p = parse_period(raw, today=today)
    if not p.start:
        return None
    hh, mm = 0, 0
    if p.time_text:
        hh, mm = (int(x) for x in p.time_text.split(":"))
    return datetime(p.start.year, p.start.month, p.start.day, hh, mm)


def is_weekend_relevant(raw: str, today: date | None = None) -> bool:
    """Быстрый фильтр для парсеров до глубокого чтения страницы."""
    return parse_period(raw, today=today).is_relevant(today)


_RE_FIND_RANGE = re.compile(
    rf"(\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?)"
    rf"\s*(?:-|–|—|по)\s*"
    rf"(\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?)",
    re.IGNORECASE,
)
_RE_FIND_WORD = re.compile(rf"(?<!\d)(\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?)", re.IGNORECASE)
_RE_FIND_NUM = re.compile(r"(?<![\d.])(\d{1,2}[./]\d{1,2}(?:[./]\d{4})?)(?![\d.])")
_RE_EXPLICIT_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def find_period_text(text: str) -> str:
    """Ищет в свободном тексте страницы первую похожую на период дату.

    Нужно, когда дата лежит не в карточке листинга, а на детальной странице.
    """
    if not text:
        return ""
    m = _RE_FIND_RANGE.search(text)
    if m:
        return f"{m.group(1)} - {m.group(2)}"
    m = _RE_FIND_NUM.search(text)
    if m:
        return m.group(1)
    m = _RE_FIND_WORD.search(text)
    if m:
        return m.group(1)
    return ""


def assign_years_descending(raw_list: list[str], today: date | None = None) -> list[Period]:
    """Разбор лент, отсортированных от новых к старым, где год указан не везде.

    Проблема: ЦК19 показывает «01.11_15:00» без года, и прошлогоднее событие
    выглядит как сегодняшнее. Решение: порядок ленты убывающий, поэтому год
    определяется позицией — пока даты убывают, год тот же; на «перескоке»
    вверх (15.01 → 20.12) год уменьшается на 1. Явный год в строке
    («3.09_5.09.2020») имеет приоритет и задаёт год для следующих элементов.

    Возвращает список Period в том же порядке, что и входной список.
    """
    today = today or date.today()
    periods: list[Period] = []
    current_year = today.year
    prev_key: tuple[int, int] | None = None

    for raw in raw_list:
        text = (raw or "").replace("\u00ad", "").strip()
        explicit = _RE_EXPLICIT_YEAR.search(text)
        tokens = _tokens(text)
        if not tokens:
            periods.append(Period(raw=raw, kind="unknown"))
            continue

        day, month, year_in_token = tokens[0]
        key = (month, day)

        if explicit:
            year = int(explicit.group(1))
        else:
            if prev_key is not None and key > prev_key:
                current_year -= 1
            year = year_in_token or current_year

        current_year = year
        prev_key = key

        start = _mk(day, month, year)
        end = start
        if len(tokens) >= 2:
            d2, m2, y2 = tokens[-1]
            end = _mk(d2, m2, y2 or year)
            if start and end and end < start:
                end = _mk(d2, m2, end.year + 1)

        period = Period(raw=raw, start=start, end=end)
        if start and end:
            period.kind = "long_term" if (end - start).days >= 3 else "one_time"
        periods.append(period)

    return periods
