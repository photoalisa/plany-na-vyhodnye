"""Единая модель события и схема таблицы nsk_weekend v2."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from . import dates as dts
from . import text as txt

COLUMNS = [
    "id",              # A  стабильный ключ (md5 от source+url+title)
    "source",          # B  короткое имя источника
    "title",           # C
    "text_short",      # D  два предложения для карточки
    "text_raw",        # E  полный текст (для перегенерации карточки)
    "date_raw",        # F  как на сайте-источнике
    "date_start",      # G  ISO YYYY-MM-DD — по нему сайт фильтрует выходные
    "date_end",        # H  ISO YYYY-MM-DD
    "kind",            # I  one_time | long_term | permanent | recurring
    "time_text",       # J  HH:MM
    "venue",           # K  площадка
    "address",         # L
    "district",        # M  район Новосибирска (пусто = неизвестно)
    "geo_area",        # N  Новосибирск / Бердск / Кольцово / Сузун
    "category",        # O  выставка, концерт, лекция, кино, фестиваль, экскурсия
    "tags",            # P  уточняющие темы через запятую
    "price_text",      # Q
    "is_free",         # R  TRUE/FALSE — фильтр «бесплатно»
    "is_child_ok",     # S  фильтр «можно с детьми»: по умолчанию да
    "is_dog_ok",       # T  фильтр «можно с собакой»
    "is_nature",       # U  фильтр «прогулки и природа»
    "is_outside_city", # V  фильтр «за городом»
    "image_url",       # W
    "image_ok",        # X  TRUE, если ссылка реально отдаёт картинку
    "source_url",      # Y
    "status",          # Z  active | archived | cancelled | rescheduled
    "parsed_at",       # AA
    "checked_at",      # AB
    # AC — явно детское событие (0+/6+, «для всей семьи», детский билет).
    # Новый столбец добавлен в конец, чтобы не сдвигать существующие данные.
    "is_child_friendly",
    # AD — возрастная маркировка для карточки: «18+», «16+» и т.д.
    "age",
    # AE — дни недели, когда событие реально доступно: «1,2,3,4,5» (0 — пн).
    # Нужно для площадок с графиком: музей может работать в субботу и быть
    # закрыт в воскресенье, и тогда карточка не должна попадать на оба дня.
    "open_days",
]

DEFAULTS = {
    "source": "", "title": "", "text_short": "", "text_raw": "",
    "date_raw": "", "date_start": "", "date_end": "", "kind": "unknown",
    "time_text": "", "venue": "", "address": "", "district": "",
    "geo_area": "", "category": "", "tags": "", "price_text": "",
    "is_free": False, "is_child_ok": False, "is_dog_ok": False,
    "is_nature": False, "is_outside_city": False,
    "is_child_friendly": False, "age": "", "open_days": "",
    "image_url": "", "image_ok": False, "source_url": "",
    "status": "active", "parsed_at": "", "checked_at": "",
}

# Что писать в карточке, если анонс целиком состоял из дат и цен:
# обогащение с билетного сайта считает такой текст пустым и заменяет.
FALLBACK_TEXT = "Программа и подробности — на странице события."

CATEGORY_BY_TAG = {
    "выставк": "выставка",
    "концерт": "концерт",
    "лекц": "лекция",
    "кино": "кино",
    "фестивал": "фестиваль",
    "экскурс": "экскурсия",
    "мастер-класс": "мастер-класс",
    "спектакл": "спектакль",
    "встреч": "встреча",
}


def make_event(**fields) -> dict:
    """Собирает событие с дефолтами, id и вычисленными полями."""
    ev = dict(DEFAULTS)
    ev.update({k: v for k, v in fields.items() if k in DEFAULTS or k == "id"})
    ev["title"] = txt.clean_title(ev["title"])
    ev["source_url"] = (ev["source_url"] or "").strip()
    ev["image_url"] = (fields.get("image_url") or "").strip()

    period = dts.parse_period(
        ev["date_raw"], kind_hint=fields.get("kind_hint", "") or ""
    )
    ev["date_start"] = period.start_iso
    ev["date_end"] = period.end_iso
    ev["time_text"] = ev["time_text"] or period.time_text
    if period.kind != "unknown":
        ev["kind"] = period.kind
    ev["_period"] = period

    # Источник может знать даты точнее, чем строка на сайте: например, у выставки
    # написано только «до 31 декабря», значит она идёт начиная с сегодняшнего дня.
    start_override = fields.get("start_override")
    end_override = fields.get("end_override")
    if start_override or end_override:
        start_iso = start_override or ev["date_start"]
        end_iso = end_override or ev["date_end"] or start_iso
        ev["date_start"], ev["date_end"] = start_iso, end_iso
        ev["kind"] = fields.get("kind_hint") or "long_term"
        ev["_period"] = dts.Period(
            start=date.fromisoformat(start_iso) if start_iso else None,
            end=date.fromisoformat(end_iso) if end_iso else None,
            kind=ev["kind"],
            raw=ev["date_raw"],
        )

    full_text = ev["text_raw"] or ev["title"]
    ev["text_raw"] = txt.clean(full_text)[:1800]
    if not ev["text_short"]:
        ev["text_short"] = _announcement(ev)

    price_text, is_free = txt.parse_price(
        fields.get("price_source", ""), ev["price_text"], ev["text_raw"][:600]
    )
    ev["price_text"] = txt.price_label(ev["price_text"] or price_text)
    # Если парсер уже написал «Бесплатно» в цену, флаг обязан быть TRUE:
    # иначе фильтр «бесплатно» расходится с тем, что видно в карточке.
    if "бесплат" in (ev["price_text"] or "").lower():
        is_free = True
    flags = txt.infer_filters(ev["title"], ev["text_raw"],
                              {"is_free": is_free or ev["is_free"]},
                              venue=ev["venue"],
                              age_text=fields.get("age_text", ""))
    for key, value in flags.items():
        ev[key] = ev[key] or value

    # Возрастная маркировка для карточки (436-ФЗ): берём старшую метку —
    # если в программе есть «18+», событие помечается как 18+.
    # Проверка именно на None: «0+» — самая частая метка, и ноль не должен
    # считаться «метки нет».
    mark = txt.age_max(ev["title"], ev["text_raw"], fields.get("age_text", ""))
    ev["age"] = f"{mark}+" if mark is not None else ""

    if not ev["district"]:
        ev["district"] = (
            txt.guess_district(ev["venue"], ev["address"])
            or txt.guess_district(ev["text_raw"], strict=True)
        )

    # Площадка и адрес по известным местам: помогает агрегаторам, где в карточке
    # нет ни площадки, ни адреса — только текст анонса.
    if not ev["venue"] or not ev["district"] or not ev["address"]:
        known = txt.recognize_venue(ev["title"], ev["address"], ev["text_raw"])
        if known:
            ev["venue"] = ev["venue"] or known.get("venue", "")
            ev["address"] = ev["address"] or known.get("address", "")
            ev["district"] = ev["district"] or known.get("district", "")

    # Адрес в сравнимый вид: без города и района, «улица X» → «ул. X».
    # Благодаря этому одно место из разных источников даёт одинаковый ключ.
    ev["address"] = txt.normalize_address(ev["address"])
    # Территория: явное значение от парсера главнее угадывания по тексту
    # (например, филиал музея в Сузуне). Город ищем в площадке и адресе, а
    # текст анонса для этого не годится: «бердская студия участвует» иначе
    # делает новосибирскую выставку бердской.
    ev["geo_area"] = (
        ev["geo_area"]
        or txt.guess_area(ev["venue"], ev["address"])
        or txt.guess_area(ev["title"])
        or "Новосибирск"
    )
    # «За городом» — это свойство места, а не упоминания области в тексте:
    # иначе выставка в центре помечается пригородной из-за слова «областная».
    ev["is_outside_city"] = ev["geo_area"] != "Новосибирск"
    # Район — это район города. У филиала музея в Сузуне или в Бердске
    # городского района нет: «Центральный» там означал бы совсем другую
    # территорию и уводил событие в неверный фильтр.
    if ev["is_outside_city"]:
        ev["district"] = ""

    if not ev["category"]:
        ev["category"] = _category_from_tags(ev["tags"], ev["title"])

    ev["id"] = make_id(ev["source"], ev["source_url"], ev["title"])
    ev["_norm_title"] = txt.normalize_title(ev["title"])
    return ev


# «Открытие выставки» уместно только в день открытия. На выходных выставка
# уже идёт, и такая формулировка в карточке читается как ошибка.
_RE_OPENING_TITLE = re.compile(r"^Открытие\s+(?:\w+\s+){0,2}\w*выставки\s+", re.IGNORECASE)
_OPENING_WORDS = ("состоится", "пройдёт", "пройдет", "приглашаем", "церемони", "ждём", "ждем")


def _is_opening_sentence(sentence: str) -> bool:
    low = sentence.lower()
    return "открыти" in low and any(word in low for word in _OPENING_WORDS)


def fix_past_opening(ev: dict, today: date) -> bool:
    """Убирает «открытие» у выставки, которая открылась до выходных.

    Меняет заголовок («Открытие комплексной выставки X» → «Выставка X») и
    выбрасывает из описания фразы про торжественное открытие. id не трогаем:
    он остаётся ключом строки в таблице.
    """
    if ev.get("kind") != "long_term":
        return False
    start = ev.get("date_start") or ""
    if not start or start >= today.isoformat():
        return False

    changed = False
    title = ev.get("title", "")
    new_title = _RE_OPENING_TITLE.sub("Выставка ", title).strip()
    if new_title != title:
        ev["title"] = new_title
        ev["_norm_title"] = txt.normalize_title(new_title)
        changed = True

    text = ev.get("text_raw", "")
    parts = txt.split_sentences(text)
    kept = [p for p in parts if not _is_opening_sentence(p)]
    if kept and len(kept) != len(parts):
        ev["text_raw"] = " ".join(kept)
        ev["text_short"] = _announcement(ev)
        changed = True
    return changed


def _announcement(ev: dict) -> str:
    """Текст для карточки: без дат, времени, цен и «для кого».

    Если после чистки ничего не осталось — берём абзац со страницы события,
    а если и там пусто, оставляем исходный анонс: пустая карточка хуже.
    """
    short = txt.shorten(txt.clean_announcement(ev["text_raw"]), 2, 300)
    if len(short) >= 60:
        return short
    extra = txt.page_description(ev.get("source_url", ""))
    if extra:
        fresh = txt.shorten(txt.clean_announcement(extra), 2, 300)
        if len(fresh) > len(short):
            return fresh
    # Ни в карточке, ни на странице не осталось текста без дат и цен —
    # лучше нейтральная строка, чем анонс про стоимость билетов.
    return short or FALLBACK_TEXT


def _category_from_tags(tags: str, title: str) -> str:
    blob = f"{tags} {title}".lower()
    for key, value in CATEGORY_BY_TAG.items():
        if key in blob:
            return value
    return ""


def make_id(source: str, url: str, title: str) -> str:
    return hashlib.md5(f"{source}|{url}|{title}".encode("utf-8")).hexdigest()[:16]


def to_row(ev: dict, now: str | None = None) -> list:
    """Строка для Google Sheets в порядке COLUMNS."""
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = []
    for col in COLUMNS:
        value = ev.get(col, "")
        if col == "parsed_at" and not value:
            value = now
        if isinstance(value, bool):
            value = "TRUE" if value else "FALSE"
        row.append(value if value is not None else "")
    return row


def is_relevant(ev: dict, today: date | None = None) -> bool:
    period = ev.get("_period")
    if period is None:
        return False
    return period.is_relevant(today)
