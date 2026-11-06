"""Оркестратор парсера «Планы на выходные | Новосибирск».

Запуск:
    python parser_main.py                 # собрать и записать в Google Sheets
    python parser_main.py --csv           # без Google, только out/nsk_weekend.csv
    python parser_main.py --source ЦК19   # один источник
    python parser_main.py --no-images     # не проверять ссылки на афиши
    python parser_main.py --hide <url|id> # больше никогда не показывать это событие
    python parser_main.py --hidden        # показать, что скрыто

Что делает автоматически:
  * собирает 15 источников и убирает дубли (по названию, ссылке и площадке);
  * отбрасывает подборки, акции, вечеринки и события других городов;
  * помнит, что вы удалили руками: такие события попадают в лист `hidden`
    и больше не возвращаются (см. common/hidden.py).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from common import collect, sheets
from common.closures import ClosureStore
from common.hidden import HiddenStore
from config import CHECK_IMAGES, PARALLEL_SOURCES, SOURCES

LOG_FILE = "run.log"
LAST_IDS = Path("data/last_ids.json")


def setup_logging(verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def pick_sources(names: list[str] | None):
    if not names:
        return SOURCES
    wanted = [n.lower() for n in names]
    picked = [
        s for s in SOURCES
        if any(w in s.name.lower() or w in s.module.lower() or w in s.domain.lower() for w in wanted)
    ]
    if not picked:
        raise SystemExit(f"[-] Источники не найдены: {', '.join(names)}")
    return picked


def load_last_ids() -> set[str]:
    if not LAST_IDS.exists():
        return set()
    try:
        return set(json.loads(LAST_IDS.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return set()


def save_last_ids(ids: set[str]) -> None:
    LAST_IDS.parent.mkdir(parents=True, exist_ok=True)
    LAST_IDS.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")


def prepare_hidden(hidden: HiddenStore, store: sheets.SheetStore) -> int:
    """До сбора помечаем то, что вы удалили из таблицы руками.

    Сравниваем список id прошлого прогона с тем, что сейчас в таблице.
    Пропавшие id попадают в hidden сразу, поэтому событие не вернётся
    даже в этом прогоне.
    """
    if store.sheet is None:
        return 0

    previous = load_last_ids()
    current = set(store.existing)
    if not previous:
        save_last_ids(current)
        return 0

    # Пустой лист — это почти всегда пересозданная или очищенная таблица,
    # а не «пользователь удалил всё руками». Скрывать в этом случае нельзя:
    # события пропадут навсегда и молча.
    if not current:
        print(f"[!] Лист событий пуст, а прошлый прогон помнил {len(previous)} событий. "
              f"Похоже, таблицу пересоздали или очистили — ничего не скрываю.")
        return 0

    missing = previous - current
    limit = max(5, len(previous) // 3)
    if len(missing) > limit:
        print(f"[!] Пропало {len(missing)} из {len(previous)} событий — это больше похоже "
              f"на очистку таблицы, чем на ручные удаления. Ничего не скрываю. "
              f"Если удаления были настоящими: python parser_main.py --hide <id>.")
        return 0

    marked = 0
    for rid in missing:
        if hidden.add(
            {"id": rid, "source_url": "", "title": "(удалено вручную из таблицы)", "source": ""},
            "удалено вручную из таблицы",
        ):
            marked += 1
    if marked:
        print(f"[+] Запомнил удалённые вручную события: {marked} — больше не появятся")
    return marked


def remember_ids(store: sheets.SheetStore, events: list[dict]) -> None:
    """Сохраняем id текущего состояния таблицы для следующего сравнения."""
    if store.sheet is not None:
        save_last_ids(set(store.existing))
    else:
        save_last_ids({ev["id"] for ev in events})


def write_summary(build, today: date, path: Path = Path("out/summary.json")) -> None:
    """Машиночитаемый итог прогона: по нему видно, какой источник отвалился.

    Нужен для запусков в облаке: там нельзя посмотреть таблицу глазами, зато
    workflow может прочитать JSON и предупредить, если источник ответил пусто.
    """
    payload = {
        "date": today.isoformat(),
        "total_raw": build.raw_total,
        "total_valid": len(build.events),
        "sources": [
            {
                "source": stat.name,
                "raw": stat.raw,
                "relevant": stat.relevant,
                "valid": stat.valid,
                "duplicates": stat.duplicates,
                "errors": stat.error_count,
                "status": stat.status,
                "message": stat.message,
                "seconds": round(stat.seconds, 1),
            }
            for stat in build.stats
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Итог прогона: {path}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Сборщик событий Новосибирска на выходные")
    parser.add_argument("--csv", action="store_true", help="не писать в Google Sheets, только CSV")
    parser.add_argument("--source", action="append", metavar="ИМЯ",
                        help="ограничить источники (можно несколько раз)")
    parser.add_argument("--no-images", action="store_true", help="не проверять ссылки на афиши")
    parser.add_argument("--no-enrich", action="store_true",
                        help="не добирать описание и афишу с билетного сайта")
    parser.add_argument("--refresh", action="store_true", help="игнорировать кэш страниц")
    parser.add_argument("--limit", type=int, default=0, help="ограничить число событий (тест)")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="считать эту дату «сегодня» (тест)")
    parser.add_argument("--hide", metavar="URL|ID", help="скрыть событие навсегда")
    parser.add_argument("--hidden", action="store_true", help="показать скрытые события")
    parser.add_argument("--close", nargs=2, metavar=("ПЛОЩАДКА", "YYYY-MM-DD"),
                        help="площадка закрыта в этот день (санитарный день, ремонт)")
    parser.add_argument("--closed", action="store_true", help="показать закрытые дни площадок")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    today = date.fromisoformat(args.date) if args.date else date.today()
    sources = pick_sources(args.source)

    store = sheets.SheetStore(use_google=not args.csv)
    hidden = HiddenStore(book=store.book)
    closures = ClosureStore(book=store.book)

    if args.closed:
        print(closures.summary())
        for line in closures.recent(30):
            print(line)
        return 0

    if args.close:
        venue, day = args.close
        added = closures.add(venue, day, "ручное закрытие")
        print("[+] Закрыто" if added else "[i] Уже было закрыто")
        return 0

    if args.hidden:
        print(hidden.summary())
        for line in hidden.recent(20):
            print(line)
        return 0

    if args.hide:
        target = args.hide.strip()
        added = hidden.add(
            {"id": target, "source_url": target, "title": "скрыто вручную", "source": "manual"},
            "скрыто вручную",
        )
        print("[+] Скрыто" if added else "[i] Уже было скрыто")
        return 0

    if args.refresh:
        shutil.rmtree(".cache/http", ignore_errors=True)
        logging.info("кэш страниц очищен")

    started = datetime.now()
    logging.info("старт: %s, источников %d, скрыто %d",
                 started.strftime("%Y-%m-%d %H:%M"), len(sources), len(hidden.keys | hidden.urls))

    prepare_hidden(hidden, store)

    build = collect.collect(
        sources,
        today=today,
        check_images=CHECK_IMAGES and not args.no_images,
        workers=PARALLEL_SOURCES,
        hidden=hidden.keys | hidden.urls,
        enrich=not args.no_enrich,
    )

    events = build.events
    if args.limit:
        events = events[: args.limit]

    print()
    print(collect.summary(build))
    print()
    write_summary(build, today)

    stats = store.upsert(events, today=today)

    # Сверка: событий, которых нет в этом прогоне, больше не должно быть в
    # таблице. Источники без ответа пропускаем, иначе сбой сайта снёс бы
    # все его события.
    live_sources = {s.name for s in build.stats if s.raw > 0}
    dropped = store.reconcile(events, sources=live_sources)
    if dropped:
        print(f"[+] Снято с публикации: {dropped} — событий больше нет в источниках")

    if store.sheet is not None:
        for stat in build.stats:
            store.log_run(
                stat.name, stat.raw, stat.valid, 0, 0, stat.error_count, stat.message
            )

    remember_ids(store, events)

    elapsed = (datetime.now() - started).total_seconds()
    logging.info(
        "готово за %.1f c: годных %d, новых %d, обновлено %d, архив %d",
        elapsed, len(events), stats["new"], stats["updated"], stats["archived"],
    )

    if build.error_total and not events:
        logging.error("ни одного события не собрано — смотри %s", LOG_FILE)
        return 1

    empty = [s.name for s in build.stats if s.status in ("empty", "error")]
    if empty:
        logging.warning("источники без результата: %s", ", ".join(empty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
