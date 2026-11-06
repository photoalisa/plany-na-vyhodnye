"""Сборка: параллельный запуск парсеров, нормализация, фильтр выходных, дедуп, проверка картинок."""

from __future__ import annotations

import importlib
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta

from config import (
    DUPLICATE_DAY_WINDOW,
    DUPLICATE_TITLE_RATIO,
    DUPLICATE_VENUE_RATIO,
    ENRICH_KASSY,
    EXCLUDE_WORDS,
)

from . import dates, http, kassy, model, text, validate


@dataclass
class SourceStat:
    name: str
    raw: int = 0
    relevant: int = 0
    valid: int = 0
    seconds: float = 0.0
    status: str = "ok"
    message: str = ""
    duplicates: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def add_error(self, error: str) -> None:
        key = error.split(":")[0]
        self.errors[key] = self.errors.get(key, 0) + 1

    @property
    def error_count(self) -> int:
        return sum(self.errors.values())


@dataclass
class Build:
    events: list[dict]
    stats: list[SourceStat]
    duplicates: list[tuple[dict, dict]] = field(default_factory=list)
    enriched: dict = field(default_factory=dict)

    @property
    def raw_total(self) -> int:
        return sum(s.raw for s in self.stats)

    @property
    def error_total(self) -> int:
        return sum(s.error_count for s in self.stats)


def _run_source(src, today: date, hidden: set[str] | None = None) -> tuple[SourceStat, list[dict]]:
    hidden = hidden or set()
    stat = SourceStat(name=src.name)
    started = time.time()
    try:
        module = importlib.import_module(src.module)
    except Exception as e:  # noqa: BLE001
        stat.status = "error"
        stat.message = f"import: {type(e).__name__}: {e}"
        stat.seconds = round(time.time() - started, 1)
        return stat, []

    try:
        raw_events = module.fetch() or []
    except Exception as e:  # noqa: BLE001
        stat.status = "error"
        stat.message = f"{type(e).__name__}: {e}"
        stat.seconds = round(time.time() - started, 1)
        print(traceback.format_exc(limit=2))
        return stat, []

    stat.raw = len(raw_events)
    collected: list[dict] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        merged = dict(raw)
        merged["source"] = src.name           # имя источника — всегда из реестра
        merged["venue"] = merged.get("venue") or src.venue
        merged["district"] = merged.get("district") or src.district
        merged["category"] = merged.get("category") or src.category
        event = model.make_event(**merged)
        model.fix_past_opening(event, today)
        if event.get("id") in hidden or (event.get("source_url") or "") in hidden:
            stat.add_error("hidden")
            continue
        if not model.is_relevant(event, today):
            continue
        stat.relevant += 1
        title_low = (event.get("title") or "").lower()
        if any(word in title_low for word in EXCLUDE_WORDS):
            stat.add_error("stop-word")
            continue
        if text.is_digest(event["title"]):
            stat.add_error("digest")
            continue
        if text.is_far_city(event["title"], event["venue"], event["address"]):
            stat.add_error("other-city")
            continue
        if text.is_promo(event["title"]):
            stat.add_error("promo")
            continue
        if text.is_sport(event["title"]):
            stat.add_error("sport")
            continue
        errors = validate.validate(event, check_image=False)
        if errors:
            for err in errors:
                stat.add_error(err)
            continue
        stat.valid += 1
        collected.append(event)

    if stat.raw == 0:
        stat.status = "empty"
    stat.seconds = round(time.time() - started, 1)
    return stat, collected


def collect(sources, today: date | None = None, check_images: bool = True,
            workers: int = 4, hidden: set[str] | None = None,
            enrich: bool = True) -> Build:
    """Запускает источники параллельно и возвращает готовые к записи события."""
    today = today or date.today()
    hidden = hidden or set()
    sat, sun = dates.weekend(today)
    print(f"[i] Сегодня {today:%d.%m.%Y}, целевые выходные: "
          f"{sat:%d.%m}–{sun:%d.%m}")

    # 1. источники параллельно
    results = http.parallel(
        lambda src: _run_source(src, today, hidden), sources, workers=workers
    )
    stats = [r[0] for r in results]
    events = [ev for r in results for ev in r[1]]

    # 2. дедуп внутри и между источниками (приоритет — у первоисточника)
    events, duplicates = dedupe(events, sources)
    by_source: dict[str, int] = {}
    for dropped, _kept in duplicates:
        by_source[dropped["source"]] = by_source.get(dropped["source"], 0) + 1
    for stat in stats:
        stat.duplicates = by_source.get(stat.name, 0)

    # 2.5 обогащение с билетного сайта: события не добавляет, только добирает
    # описание, афишу и цену тем, у кого их нет.
    enriched = kassy.enrich(events, today) if enrich and ENRICH_KASSY else {}
    line = kassy.summary(enriched)
    if line:
        print(line)

    # 3. проверка картинок параллельно (только для выживших)
    if check_images:
        _verify_images(events)

    order = {s.name: i for i, s in enumerate(sources)}
    stats.sort(key=lambda s: order.get(s.name, 99))
    return Build(events=events, stats=stats, duplicates=duplicates, enriched=enriched)


def _intervals_overlap(left: dict, right: dict, window: int = DUPLICATE_DAY_WINDOW) -> bool:
    def bounds(ev):
        start = ev.get("date_start") or ""
        end = ev.get("date_end") or start
        if not start:
            return None
        return (date.fromisoformat(start), date.fromisoformat(end or start))

    left_b, right_b = bounds(left), bounds(right)
    if not left_b or not right_b:
        return False
    delta = timedelta(days=window)
    return left_b[0] <= right_b[1] + delta and right_b[0] <= left_b[1] + delta


_GENERIC_WORDS = {
    "выставка", "выставки", "концерт", "лекция", "встреча", "фестиваль", "экскурсия",
    "мастер", "класс", "проект", "программа", "открытие", "премьера", "показ",
    "музей", "центр", "галерея", "дом", "день", "ночь", "вечер", "город", "новый",
}


def _place_key(ev: dict) -> str:
    """Ключ площадки: название или адрес, нормализованные."""
    for field in ("venue", "address"):
        value = (ev.get(field) or "").strip().lower()
        if len(value) >= 5:
            return text.normalize_title(value)
    return ""


def _title_overlap(left: dict, right: dict) -> float:
    """Совпадение значимых слов в названиях, без общих слов и слов площадки."""
    venue_words = set(_place_key(left).split())
    for word in list(venue_words):
        venue_words.add(word[:4])

    def tokens(ev: dict) -> set[str]:
        words = set((ev.get("_norm_title") or text.normalize_title(ev.get("title", ""))).split())
        return {
            w for w in words
            if len(w) >= 4 and w not in _GENERIC_WORDS
            and not any(w.startswith(v) for v in venue_words if len(v) >= 4)
        }

    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_DESC_TWIN_RATIO = 0.45


def _desc_similar(left: dict, right: dict) -> float:
    """Похожесть описаний: у одного события они почти одинаковые.

    Берём максимум из двух направлений: сравнение текстов не должно зависеть
    от того, какой источник обработан первым.
    """
    a = " ".join((left.get("text_raw") or "").lower().split())[:300]
    b = " ".join((right.get("text_raw") or "").lower().split())[:300]
    return max(text.similar(a, b), text.similar(b, a))


def _is_twin(candidate: dict, kept: dict) -> bool:
    """Одно и то же событие? Совпадение ссылки или похожее название + даты."""
    # Одна и та же ссылка из разных источников — это точно одно событие.
    # Внутри одного источника так быть не может: у музея на одной странице
    # перечислены четыре экспозиции, и ссылка у всех общая.
    same_url = bool(candidate.get("source_url")) and \
        candidate["source_url"] == kept.get("source_url")
    if same_url and candidate.get("source") != kept.get("source"):
        return True

    left = candidate.get("_norm_title") or text.normalize_title(candidate.get("title", ""))
    right = kept.get("_norm_title") or text.normalize_title(kept.get("title", ""))
    if not left or not right:
        return False
    if not _intervals_overlap(candidate, kept):
        return False

    # Разовые события на одной площадке в разные дни — это разные события,
    # как бы похоже они ни назывались: «Французский разговорный клуб» и
    # «Немецкий разговорный клуб» в библиотеке шли в субботу и воскресенье
    # и склеивались из-за общего «разговорный клуб» и окна в 14 дней.
    if (
        candidate.get("kind") == "one_time"
        and kept.get("kind") == "one_time"
        and candidate.get("date_start") != kept.get("date_start")
        and _place_key(candidate)
        and _place_key(candidate) == _place_key(kept)
    ):
        return False

    # Одна площадка, один день начала и совпадающее описание — это одно событие
    # под разными названиями: у музея выставка называется «Нить времени»,
    # а в городской афише — «Татарский костюм». Названия не пересекаются
    # вообще, поэтому спасает только текст анонса.
    if (
        _place_key(candidate)
        and _place_key(candidate) == _place_key(kept)
        and candidate.get("date_start")
        and candidate.get("date_start") == kept.get("date_start")
        and _desc_similar(candidate, kept) >= _DESC_TWIN_RATIO
    ):
        return True

    # Числа и месяцы в названии должны совпадать: «Программа на октябрь» и
    # «Программа на ноябрь» — разные события при почти одинаковом тексте.
    left_tokens = text.distinct_tokens(left)
    right_tokens = text.distinct_tokens(right)
    if left_tokens and right_tokens and left_tokens != right_tokens:
        return False

    # Одно различающее слово — это разные события, а не дубль:
    # «Итальянский разговорный клуб» и «Китайский разговорный клуб» совпадают
    # на 2/3 и раньше склеивались. Сравниваем по основам, чтобы не мешали
    # окончания: «История Башни» и «История Башня» — по-прежнему дубль.
    words_left = {w[:4] for w in left.split() if len(w) >= 4}
    words_right = {w[:4] for w in right.split() if len(w) >= 4}
    only_left = words_left - words_right
    only_right = words_right - words_left
    if len(only_left) == 1 and len(only_right) == 1:
        return False

    ratio = text.similar(left, right)
    if ratio >= DUPLICATE_TITLE_RATIO:
        return True

    # Одна площадка + пересечение дат + совпадающие значимые слова:
    # так ловятся «Выставка пауков и скорпионов в МЕГЕ» ↔ «Выставка экзотических пауков».
    overlap = _title_overlap(candidate, kept)
    if _place_key(candidate) and _place_key(candidate) == _place_key(kept):
        # Но если у обоих указано разное время — это разные события в одном
        # зале: «Французский разговорный клуб» в 16:00 и «Китайский» в 16:30.
        same_time = (
            not candidate.get("time_text")
            or not kept.get("time_text")
            or candidate["time_text"] == kept["time_text"]
        )
        if overlap >= DUPLICATE_VENUE_RATIO and same_time:
            return True

    # У агрегаторов вместо площадки стоит категория («Вечеринки и праздники»),
    # поэтому одну и ту же выставку приходится ловить по названию: два общих
    # значимых слова при пересекающихся датах — это дубль.
    if overlap >= 0.5:
        return True

    # Короткое название целиком входит в длинное («КУЧА КИТЧА» ⊂ «куча китча олег семенов»)
    short, long_ = sorted((left, right), key=len)
    return len(short) >= 8 and short in long_ and ratio >= 0.45


def _merge_missing(primary: dict, secondary: dict) -> None:
    """Добираем у основной карточки только безопасное: афишу и цену.

    Адрес, район и площадку НЕ переносим: у агрегаторов они часто относятся
    к другому филиалу (например, выставка в Сузуне, а адрес — новосибирский),
    и такая «помощь» только портит первоисточник.
    """
    for key in ("image_url", "image_ok", "price_text"):
        if not primary.get(key) and secondary.get(key):
            primary[key] = secondary[key]

    # Первоисточник без площадки (например, «Культура НСК» пишет адрес только
    # в тексте) может добирать её у карточки-дубля с агрегатора — но только
    # если своих данных нет совсем, иначе агрегатор перебьёт верное место.
    if not primary.get("venue") and not primary.get("address"):
        for key in ("venue", "address", "district"):
            if secondary.get(key):
                primary[key] = secondary[key]


def dedupe(events: list[dict], sources) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """Убирает дубли. Порядок задан приоритетом источника (Source.prio).

    Возвращает (оставшиеся события, [(отброшенное, его дубль), ...]).
    """
    prio = {s.name: s.prio for s in sources}
    ordered = sorted(
        events,
        key=lambda e: (prio.get(e.get("source", ""), 50), -len(e.get("text_raw") or "")),
    )

    kept: list[dict] = []
    dropped: list[tuple[dict, dict]] = []
    for candidate in ordered:
        twin = next((k for k in kept if _is_twin(candidate, k)), None)
        if twin is not None:
            _merge_missing(twin, candidate)
            dropped.append((candidate, twin))
            continue
        kept.append(candidate)
    return kept, dropped


def _verify_images(events: list[dict], workers: int = 8) -> None:
    targets = [ev for ev in events if (ev.get("image_url") or "").startswith("http")]

    def check(ev: dict) -> dict:
        ok = http.image_ok(ev["image_url"])
        ev["image_ok"] = ok
        if not ok:
            ev["image_url"] = ""
        return ev

    if targets:
        http.parallel(check, targets, workers=workers)


def summary(build: Build) -> str:
    """Компактная таблица по источникам для консоли и лога."""
    lines = []
    header = (f"{'источник':<24}{'найдено':>8}{'выходные':>10}{'годных':>8}"
              f"{'дубли':>7}{'ошибки':>8}{'сек':>7}  статус")
    lines.append(header)
    lines.append("-" * len(header))
    for s in build.stats:
        status = s.status
        if s.errors:
            status += " " + ", ".join(f"{k}×{v}" for k, v in sorted(s.errors.items()))
        if s.message and s.status != "ok":
            status += f" ({s.message})"
        lines.append(
            f"{s.name:<24}{s.raw:>8}{s.relevant:>10}{s.valid:>8}"
            f"{s.duplicates:>7}{s.error_count:>8}{s.seconds:>7.1f}  {status}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'ИТОГО':<24}{build.raw_total:>8}"
        f"{sum(s.relevant for s in build.stats):>10}{len(build.events):>8}"
        f"{len(build.duplicates):>7}{build.error_total:>8}"
    )
    return "\n".join(lines)


def duplicate_report(build: Build, limit: int = 12) -> str:
    """Что и с чем склеилось — для отчёта и ручной проверки."""
    if not build.duplicates:
        return "дублей не найдено"
    lines = [f"отброшено дублей: {len(build.duplicates)}"]
    for dropped, kept in build.duplicates[:limit]:
        lines.append(
            f"  • {dropped['title'][:55]} [{dropped['source']}]\n"
            f"    → оставлено: {kept['title'][:55]} [{kept['source']}]"
        )
    if len(build.duplicates) > limit:
        lines.append(f"  … и ещё {len(build.duplicates) - limit}")
    return "\n".join(lines)
