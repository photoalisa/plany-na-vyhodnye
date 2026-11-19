"""Проверка контракта: код, документы и таблица должны сходиться.

Это «валидационный стержень» проекта: одна команда, которая ловит расхождения,
которые иначе всплывают руками — забытый домен в белом списке, статья с
несуществующим фильтром, устаревший контракт, старый путь в триггере деплоя,
заголовок таблицы не по схеме.

    python scripts/check_contract.py                 # только структура и документы
    python scripts/check_contract.py --sheet         # плюс данные из Google-таблицы
    python scripts/check_contract.py --csv out/nsk_weekend.csv
    python scripts/check_contract.py --strict        # предупреждения считаются ошибками

Коды возврата: 0 — ошибок нет, 1 — есть ошибки (предупреждения не мешают).
"""

from __future__ import annotations

import argparse
import csv
import importlib
import importlib.util
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import articles, sheets, validate  # noqa: E402
from common.model import COLUMNS  # noqa: E402
from config import SOURCES  # noqa: E402

ALLOWED_STATUS = {"active", "archived", "cancelled", "rescheduled", "review"}
CONTRACT = ROOT / "Кейс" / "КОНТРАКТ.md"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"

ERRORS: list[str] = []
WARNINGS: list[str] = []
NOTES: list[str] = []


def ok(message: str) -> None:
    print(f"  ok   {message}")


def fail(message: str) -> None:
    ERRORS.append(message)
    print(f"  x    {message}")


def warn(message: str) -> None:
    WARNINGS.append(message)
    print(f"  !    {message}")


def note(message: str) -> None:
    NOTES.append(message)
    print(f"  i    {message}")


def load_build_site():
    path = ROOT / "scripts" / "build_site.py"
    spec = importlib.util.spec_from_file_location("build_site_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_sources() -> None:
    print("Источники")
    seen: Counter[str] = Counter(src.name for src in SOURCES)
    duplicates = [name for name, count in seen.items() if count > 1]
    if duplicates:
        fail(f"повторяются имена источников: {', '.join(duplicates)}")
    else:
        ok(f"источников {len(SOURCES)}, имена уникальны")

    broken_modules, no_fetch, outside = [], [], []
    for src in SOURCES:
        path = ROOT / (src.module.replace(".", "/") + ".py")
        if not path.exists():
            broken_modules.append(src.name)
            continue
        try:
            module = importlib.import_module(src.module)
        except Exception as error:  # noqa: BLE001
            broken_modules.append(f"{src.name} ({type(error).__name__})")
            continue
        if not callable(getattr(module, "fetch", None)):
            no_fetch.append(src.name)
        host = src.domain.lower()
        if not any(host == allowed or host.endswith("." + allowed)
                   for allowed in validate.allowed_hosts()):
            outside.append(f"{src.name} → {src.domain}")

    ok(f"модули парсеров на месте: {len(SOURCES) - len(broken_modules)}") if not broken_modules \
        else fail(f"не импортируются: {', '.join(broken_modules)}")
    ok("у каждого парсера есть fetch()") if not no_fetch \
        else fail(f"нет fetch(): {', '.join(no_fetch)}")
    ok("домены источников в белом списке") if not outside \
        else fail("домены вне списка: " + "; ".join(outside))


def check_articles(build) -> None:
    print("Статьи")
    keys = {item[0] for item in build.FILTERS}
    items = articles.load_articles(ROOT / "content" / "articles")
    if not items:
        note("статей нет")
        return
    bad_filter = [f"{a.slug} → {a.filter}" for a in items if a.filter and a.filter not in keys]
    bad_date = [a.slug for a in items if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.date or "")]
    ok(f"статей {len(items)}, фильтры существуют") if not bad_filter \
        else fail("неизвестный фильтр в шапке: " + ", ".join(bad_filter))
    ok("у статей корректная дата") if not bad_date \
        else fail("дата не в формате ISO: " + ", ".join(bad_date))

    slugs = [item[2] for item in build.FILTERS]
    if len(set(slugs)) != len(slugs):
        fail("адреса подборок повторяются: " + ", ".join(slugs))
    else:
        ok(f"подборок {len(slugs)}, адреса уникальны")


def check_deploy() -> None:
    print("Публикация")
    if not DEPLOY.exists():
        fail("нет .github/workflows/deploy.yml")
        return
    text = DEPLOY.read_text(encoding="utf-8")
    if "content/articles/**" in text:
        ok("триггер смотрит на content/articles/**")
    else:
        fail("в deploy.yml путь articles/** — статьи лежат в content/articles/**")
    if "scripts/**" in text and "common/**" in text:
        ok("изменения кода запускают сборку")
    else:
        warn("в deploy.yml нет scripts/** или common/** в путях")
    if "upload-pages-artifact" in text and "deploy-pages" in text:
        ok("публикация идёт из артефакта сборки")
    else:
        warn("в deploy.yml не видно публикации через artifact")


def check_contract_doc() -> None:
    print("Документы")
    if not CONTRACT.exists():
        # Контракт — рабочий документ проекта: в репозиторий он не попадает,
        # поэтому в CI его отсутствие это норма, а не ошибка.
        note("КОНТРАКТ.md нет рядом — проверка документов пропущена (так в CI)")
        return
    text = CONTRACT.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text)

    declared_sources = re.search(r"## 4\. Источники \((\d+)\)", text)
    if not declared_sources:
        warn("в контракте нет раздела с числом источников")
    elif int(declared_sources.group(1)) != len(SOURCES):
        fail(f"контракт: источников {declared_sources.group(1)}, в коде {len(SOURCES)}")
    else:
        ok(f"контракт: источников {len(SOURCES)}")

    declared_columns = re.search(r"## 5\. Схема листа `events` \((\d+) колонка?\)", text)
    if not declared_columns:
        warn("в контракте нет раздела схемы")
    elif int(declared_columns.group(1)) != len(COLUMNS):
        fail(f"контракт: колонок {declared_columns.group(1)}, в модели {len(COLUMNS)}")
    else:
        ok(f"контракт: колонок {len(COLUMNS)}")

    missing = [name for name in COLUMNS if f"`{name}`" not in flat]
    ok("все колонки описаны") if not missing \
        else fail("в контракте нет колонок: " + ", ".join(missing))

    if "Инварианты" in text:
        ok("инварианты на месте")
    else:
        warn("в контракте нет раздела инвариантов")


def check_review_queue() -> None:
    """Очередь проверки ссылок: сколько доменов ждёт решения."""
    print("Ссылки")
    hosts = validate.allowed_hosts()
    rejected = validate.rejected_hosts()
    ok(f"белый список: {len(hosts)} доменов, отклонённых: {len(rejected)}")

    csv_path = ROOT / "data" / "review.csv"
    if not csv_path.exists():
        note("очередь проверки пуста (data/review.csv нет)")
        return
    import csv as csv_module

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv_module.DictReader(handle))
    pending = [row for row in rows if row.get("status") not in ("approved", "rejected")]
    if pending:
        warn(f"в очереди проверки {len(pending)} доменов "
             f"(python scripts/review_links.py --list)")
    else:
        ok("очередь проверки разобрана")


def check_model() -> None:
    print("Модель")
    extra = [name for name in sheets.SERVICE_COLUMNS if name not in COLUMNS]
    ok("служебные колонки есть в схеме") if not extra \
        else fail("служебные колонки вне схемы: " + ", ".join(extra))
    if len(set(COLUMNS)) == len(COLUMNS):
        ok("имена колонок уникальны")
    else:
        fail("в схеме повторяются имена колонок")


def read_sheet() -> tuple[list[str], list[dict]] | None:
    import gspread

    client = gspread.service_account(filename=sheets.CREDENTIALS)
    client.set_timeout(sheets.SHEETS_TIMEOUT)
    book = client.open(sheets.SHEET_NAME)
    titles = {ws.title: ws for ws in book.worksheets()}
    worksheet = titles.get(sheets.DATA_TAB) or book.sheet1
    values = worksheet.get_all_values()
    if not values:
        return [], []
    header = [str(cell).strip() for cell in values[0]]
    rows = [dict(zip(header, row)) for row in values[1:] if any(cell.strip() for cell in row)]
    return header, rows


def read_csv(path: Path) -> tuple[list[str], list[dict]] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [{key: (value or "") for key, value in row.items()} for row in reader]
        return list(reader.fieldnames or []), rows


def check_data(header: list[str], rows: list[dict], label: str) -> None:
    print(f"Данные: {label}")
    if header[: len(COLUMNS)] != COLUMNS:
        fail("шапка не совпадает со схемой: " + ", ".join(header[:6]) + "…")
    else:
        ok(f"шапка по схеме, строк {len(rows)}")

    if not rows:
        fail("таблица пуста")
        return

    ids = [row.get("id", "").strip() for row in rows]
    empty = sum(1 for value in ids if not value)
    duplicates = [value for value, count in Counter(ids).items() if value and count > 1]
    if empty:
        fail(f"строк без id: {empty}")
    elif duplicates:
        fail(f"повторяются id: {', '.join(duplicates[:5])}")
    else:
        ok("id на месте и уникальны")

    statuses = Counter((row.get("status") or "").strip() for row in rows)
    unknown = [name for name in statuses if name not in ALLOWED_STATUS]
    if unknown:
        fail("неизвестные статусы: " + ", ".join(unknown))
    else:
        ok("статусы допустимые: " + ", ".join(f"{k} {v}" for k, v in statuses.items()))

    today = date.today().isoformat()
    overdue = [row.get("title", "")[:40] for row in rows
               if (row.get("status") or "").strip() == "active"
               and (row.get("date_end") or "").strip()
               and row["date_end"].strip() < today
               and (row.get("kind") or "") != "permanent"]
    if overdue:
        warn(f"прошли, но активны: {len(overdue)} — например «{overdue[0]}»")

    outside = Counter()
    for row in rows:
        host = (urlparse((row.get("source_url") or "").strip()).hostname or "").lower()
        if host and not any(host == allowed or host.endswith("." + allowed)
                            for allowed in validate.allowed_hosts()):
            outside[host] += 1
    if outside:
        summary = ", ".join(f"{host} {count}" for host, count in outside.most_common(5))
        warn(f"ссылки вне белого списка: {sum(outside.values())} ({summary})")
    else:
        ok("все ссылки на разрешённых доменах")

    shared = [(url, count) for url, count in
              Counter((row.get("source_url") or "").strip() for row in rows
                      if (row.get("source_url") or "").strip()).items() if count > 1]
    if shared:
        warn(f"одна ссылка у нескольких строк: {len(shared)} — "
             f"например {shared[0][0][:60]} × {shared[0][1]}")
    else:
        ok("ссылки ведут на разные страницы")

    raw_addresses = sum(1 for row in rows
                        if re.search(r"\bобл\b|,?\sд\.\s", (row.get("address") or "")))
    if raw_addresses:
        warn(f"адреса в исходном виде: {raw_addresses} (регион или «д.»)")

    no_district = sum(1 for row in rows
                      if not (row.get("district") or "").strip()
                      and (row.get("geo_area") or "Новосибирск").strip() == "Новосибирск")
    if no_district:
        note(f"без района: {no_district} — фильтр по районам их не находит")

    no_image = sum(1 for row in rows if (row.get("image_ok") or "").strip().upper() != "TRUE")
    if no_image:
        note(f"без афиши: {no_image}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка контракта проекта")
    parser.add_argument("--sheet", action="store_true", help="читать таблицу из Google")
    parser.add_argument("--csv", help="читать выгрузку из файла")
    parser.add_argument("--strict", action="store_true", help="предупреждения считать ошибками")
    args = parser.parse_args()

    build = load_build_site()
    check_sources()
    check_model()
    check_articles(build)
    check_review_queue()
    check_deploy()
    check_contract_doc()

    data = None
    if args.csv:
        data = read_csv(Path(args.csv))
        if data is None:
            fail(f"нет файла {args.csv}")
    elif args.sheet:
        try:
            data = read_sheet()
        except Exception as error:  # noqa: BLE001
            fail(f"Google-таблица недоступна: {type(error).__name__}: {error}")

    if data:
        header, rows = data
        check_data(header, rows, "Google-таблица" if args.sheet else args.csv)

    print()
    total_warnings = len(WARNINGS) + (len(WARNINGS) if args.strict else 0)
    if ERRORS:
        print(f"Итог: ошибок {len(ERRORS)}, предупреждений {len(WARNINGS)}")
        for item in ERRORS:
            print("  x", item)
        return 1
    if args.strict and WARNINGS:
        print(f"Итог: предупреждений {len(WARNINGS)} (режим --strict — это ошибка)")
        return 1
    print(f"Итог: ошибок нет, предупреждений {len(WARNINGS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
