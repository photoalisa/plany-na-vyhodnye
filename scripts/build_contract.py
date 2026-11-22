"""Собирает «Контракт проекта» — короткую спецификацию для работы с кодом.

Зачем: обзор продукта рассказывает HANDOVER, состояние — «Продолжение работы»,
а перед правками нужно другое: схема таблицы, контракт парсера и правила,
нарушение которых портит данные. Часть разделов генерируется из кода, чтобы
документ не разошёлся с ним: источники из config.py, колонки из common/model.py,
страницы из scripts/build_site.py, статьи из content/articles.

    python scripts/build_contract.py
    python scripts/build_contract.py --out "Кейс/КОНТРАКТ.md"
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import articles, model  # noqa: E402
from config import SOURCES  # noqa: E402

DEFAULT_OUT = ROOT / "Кейс" / "КОНТРАКТ.md"

# Пояснения к источникам, которых не видно в config.py.
SOURCE_NOTES = {
    "Культура.РФ": "JS-проверка: обычный GET отдаёт 425, страницу открывает Chromium",
    "ЦБС Центрального округа": "страницы событий отвечают 500, но приходят целиком — читаем в обход",
    "Бердский музей": "дат у экспозиций нет → постоянные; дни открытия из графика работы",
    "Искитимский музей": "график сезонный: зимой закрыт по воскресеньям и понедельникам",
    "НГОНБ": "дополнительно читает афишу Дома Да Винчи",
    "Парки": "даты только на картинках → ручной контур",
}

COLUMN_NOTES = {
    "id": "хеш источника, ссылки и названия; служебная",
    "source": "имя источника из config.py или «Вручную»",
    "title": "заголовок после clean_title",
    "text_short": "два предложения для карточки",
    "text_raw": "полный текст, обрезается до 1800 знаков",
    "date_raw": "как написано у источника; «постоянно» → kind=permanent",
    "date_start": "ISO; у постоянных пусто",
    "date_end": "ISO; пусто, если конца нет",
    "kind": "one_time, long_term, recurring, permanent",
    "time_text": "«13:00» или «13:00–17:00»",
    "venue": "площадка; у загородных — из адреса",
    "address": "улица и дом без региона",
    "district": "район Новосибирска; у области пусто",
    "geo_area": "Новосибирск или населённый пункт области",
    "category": "выставка, лекция, встреча, мастер-класс…",
    "tags": "через запятую, для подборок",
    "price_text": "«Бесплатно», «от 300 ₽» или пусто",
    "is_free": "TRUE/FALSE; фильтр «Бесплатно»",
    "is_child_ok": "TRUE, если можно с ребёнком",
    "is_dog_ok": "TRUE, если можно с животным",
    "is_nature": "TRUE для прогулок и природы",
    "is_outside_city": "TRUE, если не Новосибирск",
    "image_url": "ссылка на афишу, не копия",
    "image_ok": "TRUE, если ссылка реально отдаёт картинку",
    "source_url": "страница события у источника",
    "status": "active, archived, cancelled, rescheduled",
    "parsed_at": "когда записано; служебная",
    "checked_at": "ручная проверка; служебная",
    "is_child_friendly": "TRUE, если событие рассчитано на детей",
    "age": "0+, 6+, 12+, 16+, 18+ по данным организатора",
    "open_days": "дни недели работы площадки: 0 — понедельник; пусто — все дни",
}

INVARIANTS = [
    ("Строку, которой нет в снимке `parsed`, парсер не перезаписывает",
     "common/sheets.py, `_merge_row`: заполняются только пустые ячейки. Так живут "
     "строки из выгрузки и ручного контура."),
    ("Такую же строку не снимает сверка",
     "common/sheets.py, `reconcile`: id вне снимка пропускается."),
    ("Сверка касается только источников, ответивших в прогоне",
     "`reconcile(events, sources=...)`: упавший сайт не стирает свои события."),
    ("Пустая ячейка в таблице означает сброс",
     "`_merge_row`: пустое значение возвращает вариант парсера."),
    ("В CSV-режиме снимок `id` не обновляется",
     "`parser_main.remember_ids`: иначе ручные удаления не запомнятся."),
    ("Удалять события можно только перед полным прогоном",
     "перед одиночным `--source` удаление не запомнится и событие вернётся."),
    ("Локальный парсинг и прогон Actions не идут одновременно",
     "оба читают таблицу до записи другого — появляются дубли `id`."),
    ("На сайте только `status=active`",
     "`scripts/build_site.py` фильтрует по статусу; остальное — история в `archive`."),
    ("Импорт выгрузки пишет мимо снимка `parsed`",
     "`scripts/import_csv.py`: строки остаются «не нашими» и не снимаются."),
    ("Ссылка со статусом `review` на сайт не попадает",
     "`build_site.events_from_rows` и `validate.classify`: строка живёт в таблице, "
     "публикация ждёт подтверждения домена."),
    ("Одна ссылка может описывать несколько событий",
     "`common/sheets.py._load_index`: ссылка, встретившаяся больше одного раза, "
     "в индекс не попадает — иначе страница-афиша с 15 событиями схлопнула бы "
     "их в одну строку. Такие строки сопоставляются по `id` и «название + дата»."),
    ("В архиве нет повторных копий",
     "`common/sheets.move_inactive`: строка, чей `id` уже лежит в `archive`, "
     "второй раз не копируется, но из `events` удаляется. Прошедшие ручные "
     "события переносит и `scripts/manual_add.py` сразу после записи."),
    ("Карточка агрегатора не дублирует карточку площадки",
     "`common/sheets._find_twin`: при записи ищется активная строка с пересекающимися "
     "датами, похожестью названия не ниже 0.82 и совместимой площадкой. Сравнение "
     "названий — `text.normalize_title` без служебных слов и числа, похожесть — "
     "`text.similar`. Побеждает источник с меньшим `prio` в `config.py`; если агрегатор "
     "записан раньше, его строка занимается первоисточником, иначе новая не добавляется. "
     "Накопленные пары убирает `scripts/dedupe_twins.py` (предпросмотр и `--apply`)."),
    ("Время в карточках несёт смещение `+07:00`",
     "`scripts/build_site.py`, `event_bounds`: без смещения браузер посетителя считает "
     "прошедшее по своей зоне и скрывает событие на несколько часов раньше. JSON-LD "
     "берёт те же `starts_at`/`ends_at`, иначе Rich Results получает дату без времени "
     "или диапазон вроде «13:00–14:30»."),
    ("Порядок карточек задаёт сервер",
     "сортировка живёт в сборке; `apply()` в браузере только скрывает и показывает "
     "карточки, не переставляя узлы. Перестановка сбивала фокус и дёргала сетку. "
     "Прошедшие карточки остаются на своих местах по времени."),
]

FILES = [
    ("config.py", "реестр источников: имя, модуль, домен, площадка, район, приоритет"),
    ("parser_main.py", "запуск сбора, запись в таблицу, сверка, архив"),
    ("common/collect.py", "параллельный обход источников, дедупликация, проверка картинок"),
    ("common/model.py", "модель события, схема `events`, `id`, фильтры по тексту"),
    ("common/dates.py", "разбор периодов, выходные, актуальность"),
    ("common/text.py", "очистка текста, цена, адреса, районы, возраст"),
    ("common/sheets.py", "upsert, сверка, архив, снимок `parsed`, лог прогонов"),
    ("common/validate.py", "обязательные поля, белый список доменов"),
    ("common/hidden.py", "скрытые события: `id` и название"),
    ("common/closures.py", "закрытия площадок: разовые даты и дни недели"),
    ("common/articles.py", "статьи: шапка, разметка, живой блок"),
    ("common/kassy.py", "обогащение с билетного сайта"),
    ("scripts/build_site.py", "генерация сайта: страницы, фильтры, разметка"),
    ("scripts/import_csv.py", "перенос готовой выгрузки в таблицу"),
    ("scripts/dedupe.py", "уборка дублей по `id`, заполнение пустых `id`"),
    ("scripts/dedupe_twins.py", "дубли агрегаторов рядом с карточками площадок: предпросмотр и `--apply`"),
    ("scripts/refresh_images.py", "перепроверка афиш: после прогонов с `--no-images` восстанавливает `image_ok`"),
    ("scripts/fuzzy_dedupe.py", "похожие названия: проверка, без записи по умолчанию"),
    ("scripts/archive_move.py", "неактивные строки в лист `archive`"),
    ("scripts/refresh_snapshot.py", "обновление снимка `id` перед прогоном"),
    ("scripts/push_csv.py", "заливка `out/nsk_weekend.csv` в таблицу"),
    ("scripts/manual_add.py", "ручные события из текстов `content/manual/`"),
    ("scripts/readme_card.py", "карточка для README без даты"),
    ("scripts/build_contract.py", "сборка этого документа из кода"),
    ("scripts/check_contract.py", "проверка: код, документы и таблица сходятся"),
    ("common/review.py", "очередь проверки доменов: лист `review` или CSV"),
    ("scripts/review_links.py", "очередь ссылок: список, подтверждение, отказ"),
    ("scripts/make_og.py", "превью ссылки под текущие выходные"),
]


def load_build_site():
    """Импорт build_site по пути: модуль лежит в scripts, пакета нет."""
    path = ROOT / "scripts" / "build_site.py"
    spec = importlib.util.spec_from_file_location("build_site_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sources_table() -> list[str]:
    rows = ["| Источник | Приоритет | Домен | Площадка по умолчанию | Модуль | Особенность |",
            "|---|---|---|---|---|---|"]
    for src in sorted(SOURCES, key=lambda s: s.prio):
        note = SOURCE_NOTES.get(src.name, "")
        venue = src.venue or "—"
        district = f" ({src.district})" if src.district else ""
        rows.append(f"| {src.name} | {src.prio} | {src.domain} | {venue}{district} | "
                    f"`{src.module}` | {note} |")
    return rows


def columns_table() -> list[str]:
    rows = ["| № | Колонка | Правило |", "|---|---|---|"]
    for number, name in enumerate(model.COLUMNS, 1):
        mark = " *(служебная)*" if name in ("id", "parsed_at", "checked_at") else ""
        rows.append(f"| {number} | `{name}`{mark} | {COLUMN_NOTES.get(name, '')} |")
    return rows


def pages_table(build) -> list[str]:
    rows = ["| Адрес | Что внутри |", "|---|---|"]
    rows.append("| `/` | каталог на ближайшие выходные, все фильтры |")
    for key, label, slug, title, _desc in build.FILTERS:
        rows.append(f"| `/{slug}/` | подборка «{label}», фильтр `{key}` |")
    rows.append("| `/statyi/` | индекс статей |")
    rows.append("| `/404.html` | возврат в каталог |")
    return rows


def articles_table() -> list[str]:
    items = articles.load_articles(ROOT / "content" / "articles")
    if not items:
        return ["Статей пока нет."]
    rows = ["| Статья | Дата | Фильтр живого блока |", "|---|---|---|"]
    for item in items:
        rows.append(f"| {item.h1} — content/articles/{item.slug}.md | {item.date} | "
                    f"`{item.filter or '—'}` |")
    return rows


def parser_contract() -> list[str]:
    return [
        "Новый источник — файл `parsers/<имя>.py` с функцией `fetch() -> list[dict]`.",
        "Парсер собирает сырые события и не разбирает даты сам: этим занимается `common/model.py`.",
        "",
        "Обязательные поля события: `source`, `title`, `date_raw`, `source_url`, `text_raw`.",
        "Необязательные: `kind_hint` (`permanent` для постоянных экспозиций), `venue`,",
        "`address`, `geo_area`, `district`, `category`, `tags`, `price_source`, `age_text`,",
        "`image_url`, `open_days`, `start_override`, `end_override`.",
        "",
        "Требования к данным:",
        "",
        "- `text_raw` — от 40 знаков, очищенный от меню, телефонов и кнопок «поделиться»;",
        "- `source_url` — страница события у источника, домен из белого списка `common/validate.py`;",
        "- `date_raw` — строка как на сайте; если даты нет, ставится «постоянно» и `kind_hint=permanent`;",
        "- `open_days` — только если площадка работает не каждый день: «1,2,3,4,5» (0 — понедельник);",
        "- запросы — через `common/http.py` (кэш, повторы, параллельный обход), а не `requests` напрямую,",
        "  кроме случаев, когда нужен обход проверки статуса.",
        "",
        "После регистрации источника в `config.py` проверка такая:",
        "",
        "```powershell",
        "python parser_main.py --source \"Имя источника\" --csv",
        "```",
        "",
        "Парсер готов, если события проходят `validate` без ошибок и попадают на выходные.",
    ]


def links_section() -> list[str]:
    return [
        "| Статус | Что значит | Что с событием |",
        "|---|---|---|",
        "| `allowed` | домен в белом списке `common/validate.py` или в "
        "`data/allowed_hosts.json` | публикуем |",
        "| `banned` | платформа под запретом (устав, раздел 4) | не пишем в таблицу |",
        "| `review` | незнакомый домен, сокращатель, IP, файл, подделка под известный | "
        "строка в таблице со статусом `review`, на сайт не идёт, домен — в очередь |",
        "| `invalid` | не ссылка: `javascript:`, `mailto:`, пусто | отбрасываем |",
        "",
        "Порядок проверок в `classify`: схема, запрещённые, IP, сокращатели, файлы,",
        "белый список, отклонённые, подделки, незнакомое. Сокращатели и файлы идут",
        "раньше белого списка: на известной площадке `/file.apk` — всё равно не страница.",
        "",
        "Очередь проверки живёт в листе `review` (или `data/review.csv`): домен,",
        "причина, пример ссылки и названия, источник, когда впервые встретился,",
        "сколько раз, решение. Команды:",
        "",
        "```powershell",
        "python scripts/review_links.py --list        # что ждёт решения",
        "python scripts/review_links.py --scan        # добрать домены из таблицы",
        "python scripts/review_links.py --approve vk.ru",
        "python scripts/review_links.py --reject bit.ly",
        "```",
        "",
        "Подтверждение пишет домен в `data/allowed_hosts.json`, отказ — в",
        "`data/rejected_hosts.json`. Оба списка читает та же `classify`, поэтому",
        "парсер, ручной контур, импорт выгрузки и сборка сайта работают по одному правилу.",
    ]


def record_rules() -> list[str]:
    return [
        "| Действие | Что делает | Когда запускается |",
        "|---|---|---|",
        "| `upsert` | добавляет новые строки, обновляет изменившиеся | каждый прогон |",
        "| `reconcile` | снимает с публикации то, чего нет в прогоне, по ответившим источникам | каждый прогон |",
        "| `move_inactive` | переносит `archived` в лист `archive` | каждый прогон |",
        "| `archive_stale` | помечает прошедшие события | еженедельно |",
        "| импорт выгрузки | пишет мимо снимка `parsed` | вручную |",
        "",
        "Статусы: `active` — на сайте, `review` — ссылка не подтверждена (в таблице есть,"
        "на сайт не идёт), `archived` — прошедшее или снятое, `cancelled` и",
        "`rescheduled` — отмена и перенос, тоже уезжают в `archive`.",
    ]


def commands() -> list[str]:
    return [
        "```powershell",
        "python parser_main.py                              # полный сбор и запись в таблицу",
        "python parser_main.py --csv                        # сбор в файл, Google не трогаем",
        "python parser_main.py --source \"ЦК19\" --verbose    # один источник",
        "python parser_main.py --hide <url|id>              # скрыть событие навсегда",
        "python parser_main.py --close \"Музей\" 2026-11-22   # площадка закрыта в этот день",
        "",
        "python scripts/refresh_snapshot.py                 # перед полным прогоном, если меняли таблицу",
        "python scripts/import_csv.py \"путь.csv\" --dry-run  # что изменится при импорте",
        "python scripts/import_csv.py \"путь.csv\" --archive-missing",
        "python scripts/dedupe.py --fill-ids                # дубли и пустые id",
        "python scripts/dedupe_twins.py                     # дубли агрегаторов, предпросмотр",
        "python scripts/dedupe_twins.py --apply             # убрать найденные",
        "python scripts/fuzzy_dedupe.py --dry-run           # похожие названия",
        "python scripts/refresh_images.py --apply          # перепроверить афиши",
        "python parser_main.py --record-deletions          # запомнить удаления из таблицы",
        "python scripts/archive_move.py                     # неактивные в archive",
        "",
        "python scripts/build_site.py --sheet --base /plany-na-vyhodnye --out dist_pages",
        "python scripts/build_site.py --csv out/nsk_weekend.csv   # без Google",
        "python scripts/manual_add.py --dry-run             # разбор content/manual без записи",
        "```",
    ]


def checklists() -> list[str]:
    return [
        "**Перед полным прогоном**",
        "",
        "1. В таблице нет незавершённой ручной правки — прогон перезапишет поля,",
        "   которые совпадают со снимком `parsed`.",
        "2. Снимок `id` свежий: `python scripts/refresh_snapshot.py`.",
        "3. Локальный парсинг не запущен параллельно с Actions.",
        "",
        "0. Очередь ссылок: `python scripts/review_links.py --list` — подтвердить или",
        "   отклонить накопившиеся домены.",
        "1. Проверка контракта: `python scripts/check_contract.py --sheet`. Ошибки",
        "   останавливают работу, предупреждения показывают долги.",
        "",
        "**Перед публикацией**",
        "",
        "1. Выгрузка на нужные выходные: `python parser_main.py --csv`.",
        "2. Дубли и пропуски: `python scripts/fuzzy_dedupe.py --dry-run`, `scripts/dedupe.py`.",
        "3. Сборка без предупреждений, страницы отвечают 200.",
        "4. Превью под текущие выходные: `python scripts/make_og.py`.",
        "5. После выкладки — счётчик и карточки на живом сайте.",
    ]


def pitfalls() -> list[str]:
    return [
        "- **Google API обрывается** из домашней сети: таймаут 30 с, откат в CSV, снимок",
        "  `id` в CSV-режиме не перезаписывается.",
        "- **Планировщик GitHub** — «не раньше», а не «ровно»: задержки 4–7 часов обычны,",
        "  публикацию проверяют и при необходимости запускают кнопкой.",
        "- **`content/articles/**`** — путь в триггере `deploy.yml`; с `articles/**`",
        "  загрузка статьи не запускает сборку.",
        "- **Домены без белого списка** проходят на сайт, но события от парсера с таким",
        "  доменом `validate` отклонит.",
        "- **Повторяющиеся ссылки** — страница-афиша с несколькими событиями",
        "  (`parkigoroda-nsk.ru/november_parki`, `bibliosib.ru/directions/childrens`):",
        "  в индекс `by_url` такая ссылка не попадает, строки живут по `id`.",
        "- **Кириллица в путях** только через `-LiteralPath`, heredoc в PowerShell ломается.",
    ]


def build_document() -> str:
    build = load_build_site()
    today = date.today().strftime("%d.%m.%Y")
    parts: list[str] = []
    parts.append("# Контракт проекта «Планы на выходные | Новосибирск»")
    parts.append("")
    parts.append(f"Собрано {today} из `config.py`, `common/model.py`, `scripts/build_site.py`.")
    parts.append("Читать перед правками данных и в начале нового чата. Обзор продукта —")
    parts.append("в `HANDOVER.md`, текущее состояние — в `Продолжение работы.md`.")
    parts.append("")
    parts.append("## 1. Модули")
    parts.append("")
    parts.append("| Файл | Назначение |")
    parts.append("|---|---|")
    for path, purpose in FILES:
        parts.append(f"| `{path}` | {purpose} |")
    parts.append("")
    parts.append("## 2. Поток данных")
    parts.append("")
    parts.append("```")
    parts.append("config.SOURCES → parsers/*.fetch() → common/collect.py")
    parts.append("  → common/model.py (даты, фильтры, id) → common/validate.py")
    parts.append("  → common/sheets.py (upsert, reconcile, archive)")
    parts.append("  → scripts/build_site.py → GitHub Pages")
    parts.append("```")
    parts.append("")
    parts.append("## 3. Контракт парсера")
    parts.append("")
    parts.extend(parser_contract())
    parts.append("")
    parts.append(f"## 4. Источники ({len(SOURCES)})")
    parts.append("")
    parts.extend(sources_table())
    parts.append("")
    parts.append(f"## 5. Схема листа `events` ({len(model.COLUMNS)} колонка)")
    parts.append("")
    parts.extend(columns_table())
    parts.append("")
    parts.append("## 6. Ссылки и домены")
    parts.append("")
    parts.extend(links_section())
    parts.append("")
    parts.append("## 7. Правила записи")
    parts.append("")
    parts.extend(record_rules())
    parts.append("")
    parts.append("## 8. Инварианты")
    parts.append("")
    for index, (title, detail) in enumerate(INVARIANTS, 1):
        parts.append(f"{index}. **{title}.** {detail}")
    parts.append("")
    parts.append("## 9. Страницы сайта")
    parts.append("")
    parts.extend(pages_table(build))
    parts.append("")
    parts.append("## 10. Статьи")
    parts.append("")
    parts.extend(articles_table())
    parts.append("")
    parts.append("## 11. Команды")
    parts.append("")
    parts.extend(commands())
    parts.append("")
    parts.append("## 12. Чек-листы")
    parts.append("")
    parts.extend(checklists())
    parts.append("")
    parts.append("## 13. Грабли")
    parts.append("")
    parts.extend(pitfalls())
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать контракт проекта")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    out = Path(args.out)
    text = build_document()
    out.write_text(text, encoding="utf-8")
    print(f"[+] {out} — {len(text.splitlines())} строк, {len(text)} знаков")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
