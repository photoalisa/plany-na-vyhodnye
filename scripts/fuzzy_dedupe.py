"""Поиск дублей между источниками: одно событие, пришедшее от площадки и от агрегатора.

Почему не pandas и rapidfuzz: в проекте нет ни того, ни другого, а в Actions они
добавят минуты установки ради одной задачи. Сравнение названий сделано на difflib
из стандартной библиотеки — для коротких заголовков этого достаточно.

Как считает дубль:
  1) нормализуем адрес до «улица дом», убирая город, регион и слова вроде «ул.»;
  2) внутри одного адреса сравниваем названия: >=82 — дубль; или >=60 при совпадении
     первого абзаца >=75;
  3) не склеиваем выставку с отдельной экскурсией или мастер-классом по ней;
  4) из пары оставляем строку источника с меньшим prio (первоисточник важнее агрегатора),
     недостающие поля добираем из второй.

    python scripts/fuzzy_dedupe.py --csv out/nsk_weekend.csv --dry-run
    python scripts/fuzzy_dedupe.py --dry-run
    python scripts/fuzzy_dedupe.py --apply
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import sheets, text  # noqa: E402
from common.model import COLUMNS  # noqa: E402
from config import SOURCES  # noqa: E402

PRIORITY = {src.name: src.prio for src in SOURCES}

# Известные ошибки парсеров в адресе: id → правильный адрес для сравнения.
ADDRESS_OVERRIDES = {
    "afad346cdd3988a6": "ленина 15",
}

TITLE_STRONG = 82.0
TITLE_WEAK = 60.0
LEAD_MATCH = 75.0
FIELDS_TO_FILL = ("price_text", "time_text", "age", "district", "image_url")

_CITY_RE = re.compile(
    r"новосибирская\s+обл\.?|г\.?\s*новосибирск|рп\.?\s*\w+|г\.?\s*\w+|с\.?\s*\w+",
    re.IGNORECASE,
)
_STREET_WORDS_RE = re.compile(
    r"\b(ул|улица|пр-кт|проспект|пр|пер|переулок|д|дом|зд|здание|корп|к|стр|этаж|офис|каб|лофт)\b\.?",
    re.IGNORECASE,
)
_STREET_SUFFIX_RE = re.compile(r"^(ул\.?|улица|пр-т\.?|проспект|пер\.?|переулок)\s*", re.IGNORECASE)


def _tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-zа-яё]+", value.lower()) if t]


def address_key(row: dict) -> str:
    """«улица дом» без города, региона и служебных слов."""
    row_id = (row.get("id") or "").strip()
    if row_id in ADDRESS_OVERRIDES:
        return ADDRESS_OVERRIDES[row_id]

    addr = (row.get("address") or "").strip().lower()
    if not addr:
        return "venue:" + (row.get("venue") or "").strip().lower()

    addr = _CITY_RE.sub(" ", addr)
    addr = re.sub(r"«[^»]+»|\"[^\"]+\"", " ", addr)
    addr = _STREET_WORDS_RE.sub(" ", addr)
    addr = _STREET_SUFFIX_RE.sub("", addr.strip())
    addr = re.sub(r"[^\w\s/-]", " ", addr)
    tokens = [t for t in addr.split() if t]
    if not tokens:
        return "venue:" + (row.get("venue") or "").lower()
    # Если от адреса остался один номер дома, сравнивать по нему нельзя:
    # к «33» прилипнет половина города. Тогда берём хвост адреса целиком.
    if len(tokens) == 1 and tokens[0].isdigit():
        return "addr:" + re.sub(r"\s+", " ", addr).strip()
    return " ".join(tokens[:2])


def title_key(value: str) -> str:
    return text.normalize_title(value or "")


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio() * 100


def token_set_ratio(left: str, right: str) -> float:
    """Похожесть наборов слов: порядок и повторы не влияют."""
    a, b = set(_tokens(left)), set(_tokens(right))
    if not a or not b:
        return 0.0
    common = sorted(a & b)
    joined = " ".join(common)
    return max(
        _ratio(" ".join(sorted(a)), " ".join(sorted(b))),
        _ratio(joined, " ".join(sorted(a))),
        _ratio(joined, " ".join(sorted(b))),
    )


def categories_conflict(first: dict, second: dict) -> bool:
    """Выставка и экскурсия по ней — разные карточки, а не дубль."""
    cat1 = (first.get("category") or "").strip()
    cat2 = (second.get("category") or "").strip()
    titles = f"{first.get('title','')} {second.get('title','')}".lower()
    if "экскурсия по экспозиции" in titles:
        return False
    return {cat1, cat2} in ({"выставка", "экскурсия"}, {"выставка", "мастер-класс"})


def merge(master: dict, duplicate: dict) -> bool:
    """Добирает в оставленную строку то, чего в ней нет. True — если что-то изменилось."""
    changed = False
    for field in FIELDS_TO_FILL:
        mine = (master.get(field) or "").strip()
        other = (duplicate.get(field) or "").strip()
        if not mine and other:
            master[field] = duplicate[field]
            changed = True
    mine_text = master.get("text_raw") or ""
    other_text = duplicate.get("text_raw") or ""
    if len(other_text) > len(mine_text) * 1.3:
        master["text_raw"] = other_text
        changed = True
    return changed


def find_duplicates(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Возвращает (оставленные строки с добранными полями, лог склеек)."""
    by_place: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_place[address_key(row)].append(row)

    kept: list[dict] = []
    log: list[dict] = []

    for place, group in by_place.items():
        group.sort(key=lambda r: (PRIORITY.get(r.get("source", ""), 50), r.get("id", "")))
        survivors: list[dict] = []
        for row in group:
            dup_of = None
            title = title_key(row.get("title"))
            lead = (row.get("text_short") or "")[:150]
            for candidate in survivors:
                if categories_conflict(candidate, row):
                    continue
                title_sim = token_set_ratio(title, title_key(candidate.get("title")))
                lead_sim = token_set_ratio(lead, (candidate.get("text_short") or "")[:150])
                if title_sim >= TITLE_STRONG or (title_sim >= TITLE_WEAK and lead_sim >= LEAD_MATCH):
                    dup_of = (candidate, title_sim)
                    break
            if dup_of is None:
                survivors.append(row)
                continue
            master, score = dup_of
            merge(master, row)
            log.append({
                "dropped_id": row.get("id", ""),
                "dropped_source": row.get("source", ""),
                "dropped_title": (row.get("title") or "")[:90],
                "kept_id": master.get("id", ""),
                "kept_source": master.get("source", ""),
                "kept_title": (master.get("title") or "")[:90],
                "title_similarity": round(score, 1),
                "place": place,
            })
        kept.extend(survivors)
    return kept, log


def load_sheet_rows(store) -> list[dict]:
    rows = []
    for pos, raw in enumerate(store.rows[1:], start=2):
        if not raw or not any(str(c).strip() for c in raw):
            continue
        row = dict(zip(COLUMNS, list(raw) + [""] * (len(COLUMNS) - len(raw))))
        row["_row"] = pos
        rows.append(row)
    return rows


def load_csv_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_log(path: Path, log: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["dropped_id", "dropped_source", "dropped_title", "kept_id",
               "kept_source", "kept_title", "title_similarity", "place"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(log)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Нечёткие дубли между источниками")
    parser.add_argument("--csv", metavar="ПУТЬ", help="разобрать выгрузку вместо таблицы")
    parser.add_argument("--dry-run", action="store_true", help="только показать")
    parser.add_argument("--apply", action="store_true", help="удалить дубли из таблицы")
    parser.add_argument("--log", default="out/duplicates-report.csv")
    args = parser.parse_args(argv)

    if args.csv:
        rows = load_csv_rows(args.csv)
        kept, log = find_duplicates(rows)
        write_log(Path(args.log), log)
        print(f"строк: {len(rows)} | останется: {len(kept)} | дублей: {len(log)}")
        for item in log[:15]:
            print(f"  {item['dropped_source'][:16]:16} | {item['dropped_title'][:38]:38} "
                  f"→ {item['kept_source'][:16]:16} | {item['title_similarity']}%")
        print(f"лог: {args.log}")
        return 0

    store = sheets.SheetStore(use_google=True)
    if store.sheet is None:
        print("[-] Google Sheets недоступен: повторите позже или укажите --csv")
        return 1

    rows = load_sheet_rows(store)
    kept, log = find_duplicates(rows)
    write_log(Path(args.log), log)
    print(f"строк: {len(rows)} | останется: {len(kept)} | дублей: {len(log)}")
    for item in log[:20]:
        print(f"  {item['dropped_source'][:16]:16} | {item['dropped_title'][:38]:38} "
              f"→ {item['kept_source'][:16]:16} | {item['title_similarity']}%")

    if not args.apply or not log:
        print("ничего не меняю" if not args.apply else "дублей нет")
        return 0

    dropped_rows = {int(r["_row"]) for r in rows
                    if any(r.get("id") == item["dropped_id"] for item in log)}
    for pos in sorted(dropped_rows, reverse=True):
        sheets._retry(store.sheet.delete_rows, pos)
    print(f"[+] удалено строк: {len(dropped_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
