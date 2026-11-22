"""Сборка сайта «Планы на выходные | Новосибирск» без Node.js.

Читает таблицу событий (data/events.csv или out/nsk_weekend.csv) и собирает
готовый статический сайт в dist/:

    index.html              каталог на ближайшие выходные
    besplatno/              бесплатно
    s-detmi/                с детьми
    s-zhivotnymi/           с животными (s-sobakoy/ — редирект)
    progulki-i-priroda/     прогулки и природа
    za-gorodom/             за городом
    404.html                страница «не найдено»
    sitemap.xml, robots.txt
    assets/style.css, assets/app.js, placeholder.svg

Запуск:
    python scripts/build_site.py
    python scripts/build_site.py --date 2026-11-07   # собрать под другую неделю
    python scripts/build_site.py --csv out/nsk_weekend.csv --out dist
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import articles as articles_store  # noqa: E402
from common import sheets as sheets_store  # noqa: E402
from common.address import postal_address  # noqa: E402
from common.closures import ClosureStore  # noqa: E402

# Публичный адрес сайта: он попадает в canonical, sitemap.xml и robots.txt.
# Пока домен не подключён, сайт живёт на GitHub Pages по этому адресу; при
# переезде на свой домен адрес меняется одной строкой (или флагом --site-url).
SITE_URL = "https://photoalisa.github.io/plany-na-vyhodnye"

# Версия статики для обхода кэша браузера: GitHub Pages отдаёт style.css/app.js
# с долгим кэшем без версии в URL, поэтому после правки CSS/JS посетители
# неделями видели старую версию. Число меняется при каждой сборке.
BUILD_VERSION = datetime.now().strftime("%Y%m%d%H%M")
SITE_NAME = "Планы на выходные · Новосибирск"

# Ссылка на Яндекс-Форму для «Сообщить об ошибке» и «Предложить событие».
# Пока пусто — кнопки на сайте не выводятся.
FORM_URL = "https://forms.yandex.ru/u/6ae5f9844a052dc8658c5088"

# Счётчик посещений GoatCounter: без cookie и без персональных данных, поэтому
# согласие посетителя не требуется. Код сайта — поддомен, который выдаётся при
# регистрации на goatcounter.com (например «plany-nsk»). Пока пусто — скрипт
# на сайт не попадает.
GOATCOUNTER_SITE = "plany-nsk"
DEFAULT_CSV = Path("data/events.csv")
FALLBACK_CSV = Path("out/nsk_weekend.csv")

# Готовые файлы, которые просто копируются в сборку: картинка превью для
# соцсетей и всё, что положат сюда вручную.
STATIC_DIR = ROOT / "static"

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

FILTERS = [
    ("free", "Бесплатно", "besplatno",
     "Бесплатные мероприятия в Новосибирске на выходные",
     "Куда сходить в Новосибирске бесплатно в субботу и воскресенье: выставки, "
     "лекции, концерты и прогулки без билета. Список обновляется каждую неделю."),
    ("child", "С детьми", "s-detmi",
     "Куда пойти с детьми в Новосибирске на выходные",
     "События Новосибирска, куда можно прийти с ребёнком: выставки, мастер-классы, "
     "спектакли и прогулки. Возраст и цена указаны в карточке."),
    ("dog", "С животными", "s-zhivotnymi",
     "Куда пойти с животными в Новосибирске",
     "События и места, куда можно прийти с питомцем: прогулки, парки, городские "
     "площадки и встречи. Проверяем, что животное действительно пустят."),
    ("nature", "Прогулки и природа", "progulki-i-priroda",
     "Прогулки и природа в Новосибирске на выходные",
     "Парки, набережные, Берёзовая роща, музеи природы и выставки о природе: "
     "куда пойти гулять в субботу и воскресенье."),
    ("outside", "За городом", "za-gorodom",
     "Куда съездить за город из Новосибирска на один день",
     "Пригород и область на выходные: Бердск, Кольцово, Академгородок, Сузун. "
     "Всё, что можно посетить за один день и вернуться вечером."),
]


# --------------------------------------------------------------------- данные
def events_from_rows(rows: list[dict]) -> list[dict]:
    """Сырые строки таблицы → события, готовые к выводу.

    Публикуем только активные строки с разрешённой ссылкой: если домен попал
    в запрещённые или ждёт проверки, карточка на сайт не идёт, даже когда
    статус в таблице остался `active`.
    """
    from common import validate

    ready, blocked = [], []
    for row in rows:
        if (row.get("status") or "active") != "active":
            continue
        status, _reason, host = validate.classify((row.get("source_url") or "").strip())
        if status != "allowed":
            blocked.append(f"{host or '—'} ({status})")
            continue
        ready.append(norm_event(row))
    if blocked:
        unique = sorted(set(blocked))
        print(f"[i] Не публикую строк с непроверенной ссылкой: {len(blocked)} "
              f"({', '.join(unique[:4])}{'…' if len(unique) > 4 else ''})")
    return ready


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Нет файла {path}. Сначала запустите парсер или check.py")
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return events_from_rows(rows)


def load_from_sheet(name: str, creds: str, tab: str | None = None) -> list[dict]:
    """Читает события прямо из Google-таблицы."""
    rows = sheets_store.read_table(name=name, creds=creds, tab=tab)
    return events_from_rows(rows)


def save_rows_cache(rows: list[dict], path: Path) -> None:
    """Кладём прочитанное из таблицы в CSV — пригодится офлайн и для истории."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[+] Снимок таблицы сохранён: {path} ({len(rows)} строк)")


def truthy(value: str) -> bool:
    return str(value or "").strip().upper() in {"TRUE", "1", "YES", "ДА"}


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def norm_event(row: dict) -> dict:
    start = parse_date(row.get("date_start", ""))
    end = parse_date(row.get("date_end", "")) or start
    return {
        "title": (row.get("title") or "").strip(),
        "text": (row.get("text_short") or row.get("text_raw") or "").strip(),
        "url": (row.get("source_url") or "").strip(),
        "image": (row.get("image_url") or "").strip() if truthy(row.get("image_ok", "")) else "",
        "start": start,
        "end": end,
        "kind": row.get("kind") or "one_time",
        "time": (row.get("time_text") or "").strip(),
        "venue": (row.get("venue") or "").strip(),
        "address": (row.get("address") or "").strip(),
        "district": (row.get("district") or "").strip(),
        "area": (row.get("geo_area") or "Новосибирск").strip(),
        "price": (row.get("price_text") or "").strip(),
        "age": (row.get("age") or "").strip(),
        "open_days": (row.get("open_days") or "").strip(),
        "category": (row.get("category") or "").strip(),
        "source": (row.get("source") or "").strip(),
        "flags": {
            "free": truthy(row.get("is_free", "")),
            "child": truthy(row.get("is_child_ok", "")),
            "dog": truthy(row.get("is_dog_ok", "")),
            "nature": truthy(row.get("is_nature", "")),
            "outside": truthy(row.get("is_outside_city", "")),
        },
    }


def weekend_of(today: date) -> tuple[date, date]:
    wd = today.weekday()
    if wd == 5:
        sat = today
    elif wd == 6:
        sat = today - timedelta(days=1)
    else:
        sat = today + timedelta(days=5 - wd)
    return sat, sat + timedelta(days=1)


def on_weekend(ev: dict, sat: date, sun: date) -> bool:
    if ev["kind"] == "permanent" and not ev["start"]:
        return True
    if not ev["start"]:
        return False
    end = ev["end"] or ev["start"]
    # «Сегодня» — по Новосибирску и с учётом --now, а не по часам машины:
    # иначе сборка на понедельник выбрасывала события прошлых выходных
    # и QA-прогоны с --date считали не тот набор.
    cutoff = max(sat, now_nsk().date())
    return ev["start"] <= sun and end >= cutoff


def open_weekdays(ev: dict) -> set[int] | None:
    """Дни недели, когда событие доступно, из поля open_days.

    None означает «ограничений нет»: большинство площадок работает все дни,
    а вот музей с выходным в воскресенье должен попасть только на субботу.
    """
    raw = (ev.get("open_days") or "").strip()
    if not raw:
        return None
    return {int(ch) for ch in re.findall(r"\d", raw)}


def days_of(ev: dict, sat: date, sun: date) -> list[str]:
    """В какие из двух дней выходных событие реально идёт.

    Считаем по пересечению периода события с конкретным днём, а не по дате
    начала: иначе субботний концерт попадал бы в воскресенье, а выставка,
    идущая весь ноябрь, — только в день открытия.
    """
    start = ev["start"]
    if ev["kind"] == "permanent" or not start:
        return ["sat", "sun"]
    end = ev["end"] or start
    days = []
    if start <= sat <= end:
        days.append("sat")
    if start <= sun <= end:
        days.append("sun")
    return days


def event_bounds(ev: dict, sat: date, sun: date) -> tuple[str, str]:
    """Когда событие начинается и заканчивается — в формате для браузера.

    Время берём из карточки: «19:00» или «19:00–21:00». Если времени нет,
    событие идёт весь день до 23:59 последнего дня: выставка в воскресенье
    вечером ещё работает, и прятать её рано.
    """
    days = ev.get("days") or ["sat", "sun"]
    first = sat if "sat" in days else sun
    last = sun if "sun" in days else sat

    times = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", ev.get("time") or "")
    if not times:
        return (f"{first.isoformat()}T00:00:00+07:00",
                f"{last.isoformat()}T23:59:00+07:00")

    sh, sm = int(times[0][0]), int(times[0][1])
    eh, em = int(times[-1][0]), int(times[-1][1])
    # Одно время начала без окончания: час — типичная длина лекции или встречи.
    # Два часа держали карточку в выдаче слишком долго после начала события.
    minutes = eh * 60 + em + (60 if len(times) == 1 else 0)
    end_day = last
    if minutes >= 24 * 60:
        end_day, minutes = last + timedelta(days=1), minutes % (24 * 60)
    # Смещение обязательно: без него браузер посетителя считает прошедшее
    # по своей зоне и скрывает события не в то время.
    return (f"{first.isoformat()}T{sh:02d}:{sm:02d}:00+07:00",
            f"{end_day.isoformat()}T{minutes // 60:02d}:{minutes % 60:02d}:00+07:00")


# --------------------------------------------------------------------- разметка
def esc(value: str) -> str:
    return html.escape(value or "", quote=True)


_SENTENCE_STOP = re.compile(r"[.!?…](?=\s)")
_ABBREV_TAIL = re.compile(r"(?:\b[а-яёa-z]{1,2}|\bсм|\bдр|\bруб|\bтыс)\.$", re.IGNORECASE)


def cut_text(value: str, limit: int = 240) -> str:
    """Обрезка карточки по границе предложения, а не по середине слова.

    Раньше текст резался ровно на 240-м знаке, и карточка музея
    заканчивалась обрывком вроде «…ксерокопии его пи».

    Точку внутри сокращения («к. 19 в.», «г. Искитима») и точку внутри
    кавычек («Искитим. Вехи истории») за конец предложения не считаем.
    """
    value = (value or "").strip()
    if len(value) <= limit:
        return value

    window = value[: limit + 60]
    cut = -1
    for match in _SENTENCE_STOP.finditer(window):
        before = window[: match.start() + 1]
        if before.count("«") != before.count("»"):
            continue
        if _ABBREV_TAIL.search(before):
            continue
        if match.end() >= 80:
            cut = match.end()
    if cut > 0:
        return window[:cut].strip()

    space = value.rfind(" ", 0, limit)
    return value[: space if space > 0 else limit].rstrip(" ,;:—-") + "…"


def date_label(ev: dict, weekend: bool = True) -> str:
    """Дата в карточке.

    На страницах выходных показываем только те дни, когда событие реально
    идёт: «7 ноября, сб», «8 ноября, вс» или «7–8 ноября». Период источника
    («1 ноя – 30 ноя») в подборке на выходные только сбивает с толку.
    """
    time_part = f", {ev['time']}" if ev["time"] else ""
    days = (ev.get("days") or []) if weekend else []
    if ev["kind"] == "permanent":
        # Постоянная экспозиция идёт не только по выходным — в будни она тоже
        # открыта. Поэтому пишем про два конкретных дня этих выходных, а не
        # «по субботам и воскресеньям»: множественное число читается как
        # «работает только по выходным».
        if days == ["sat"]:
            return "в субботу"
        if days == ["sun"]:
            return "в воскресенье"
        return "в субботу и воскресенье"
    if not ev["start"]:
        return "дата уточняется"
    if ev["kind"] == "recurring":
        # «Каждую неделю» не отвечает на вопрос «а в какой день?». Событие
        # повторяется в конкретные дни, их и называем.
        if days == ["sat"]:
            return "каждую субботу" + time_part
        if days == ["sun"]:
            return "каждое воскресенье" + time_part
        if days:
            return "каждые выходные" + time_part
        return "по выходным" + time_part

    days = (ev.get("days") or []) if weekend else []
    if days:
        sat, sun = CURRENT_WEEKEND
        time_part = f", {ev['time']}" if ev["time"] else ""
        if days == ["sat"]:
            return f"{sat.day} {MONTHS[sat.month - 1]}, сб{time_part}"
        if days == ["sun"]:
            return f"{sun.day} {MONTHS[sun.month - 1]}, вс{time_part}"
        if sat.month == sun.month:
            return f"{sat.day}–{sun.day} {MONTHS[sat.month - 1]}{time_part}"
        return f"{sat.day} {MONTHS[sat.month - 1]} – {sun.day} {MONTHS[sun.month - 1]}{time_part}"

    if not ev["end"] or ev["end"] == ev["start"]:
        return f"{ev['start'].day} {MONTHS_SHORT[ev['start'].month - 1]}, {WEEKDAYS[ev['start'].weekday()]}"
    return f"{ev['start'].day} {MONTHS_SHORT[ev['start'].month - 1]} – {ev['end'].day} {MONTHS_SHORT[ev['end'].month - 1]}"


def sort_key(ev: dict):
    """Тип события → дата → время → название.

    Внутри дня порядок по времени: 11:00, 13:00, 14:00. События без времени
    (постоянная экспозиция, «каждую субботу») уходят в конец дня — иначе
    они вставали бы по алфавиту между утренними и вечерними.
    """
    kind_rank = {"one_time": 0, "recurring": 1, "long_term": 2, "permanent": 3}
    return (
        kind_rank.get(ev["kind"], 2),
        ev["start"] or date.max,
        ev["time"] or "99:99",
        ev["title"],
    )


def card_html(ev: dict, weekend: bool = True) -> str:
    img = ev["image"]
    image_html = (
        f'<img src="{esc(img)}" alt="{esc(ev["title"])}" loading="lazy" width="640" height="480" '
        f'onerror="this.onerror=null;this.src=\'PLACEHOLDER\'">'
        if img else
        '<img src="PLACEHOLDER" alt="" loading="lazy" width="640" height="480">'
    )
    meta = [date_label(ev, weekend)]
    # За городом района города нет — вместо него показываем населённый пункт,
    # иначе по карточке не понять, что событие в Сузуне, а не в Новосибирске.
    place = ev["district"]
    if ev["flags"]["outside"] and ev["area"] not in ("", "Новосибирск"):
        place = ev["area"]
        # «Сузун · МТК «Сузун-завод»» — масло масляное: населённый пункт уже
        # есть в названии площадки.
        if place.lower() in ev["venue"].lower():
            place = ""
    if place:
        meta.append(place)
    # Название площадки показываем целиком: обрезка по длине прятала
    # «Искитимский городской историко-художественный музей» и читатель не
    # понимал, куда ехать. Многоточие — только для совсем длинных названий.
    venue = ev["venue"]
    if len(venue) > 70:
        venue = venue[:67].rstrip(" ,.-–—") + "…"
    if venue:
        meta.append(venue)
    # Мета — одной строкой без иконок: дата, место, площадка.
    meta_line = " · ".join(part for part in meta if part)
    attrs = " ".join(f'data-{k}="{str(v).lower()}"' for k, v in ev["flags"].items())
    day_attr = f' data-days="{" ".join(ev.get("days") or [])}"' if ev.get("days") else ""
    # data-end — конец события по часам посетителя: по нему браузер скрывает
    # прошедшее, чтобы в воскресенье вечером не висели субботние концерты.
    end_attr = f' data-end="{ev["ends_at"]}"' if ev.get("ends_at") else ""
    start_attr = f' data-start="{ev["starts_at"]}"' if ev.get("starts_at") else ""
    price_parts = []
    if ev["price"]:
        price_parts.append(ev["price"])
    elif ev["flags"]["free"]:
        price_parts.append("Бесплатно")
    if ev.get("age"):
        price_parts.append(ev["age"])
    price = " · ".join(price_parts)
    price_html = f'<p class="card__price">{esc(price)}</p>' if price else ""
    # Одна ссылка на карточку в Tab-порядке. Афиша кликабельна мышью, но
    # не создаёт вторую остановку; отчёт об ошибке живёт в подвале.
    link_label = "К источнику <span aria-hidden=\"true\">↗</span>"
    open_hint = f"Открыть страницу источника: {ev['title']}"
    return f"""
<article class="card" {attrs}{day_attr}{end_attr}{start_attr} data-district="{esc(ev['district'])}" data-geo="{esc(ev['area'])}">
  <a class="card__media" href="{esc(ev['url'])}" target="_blank" rel="noopener nofollow" tabindex="-1" aria-hidden="true">
    {image_html}
  </a>
  <div class="card__body">
    <p class="card__meta">{esc(meta_line)}</p>
    <h3 class="card__title">{esc(ev['title'])}</h3>
    <p class="card__text">{esc(cut_text(ev['text']))}</p>
    {price_html}
    <a class="card__link" href="{esc(ev['url'])}" target="_blank" rel="noopener nofollow" aria-label="{esc(open_hint)}">{link_label}</a>
  </div>
</article>"""


def chips_html(active: str = "", events: list[dict] | None = None,
               skip: frozenset = frozenset()) -> str:
    """Фильтры — настоящие ссылки: по ним ходят поисковики, а JS гасит переход.

    Кнопкой их делать нельзя: страницы подборок (/besplatno/ и остальные)
    тогда ниоткуда не linked, и в индексе их не будет.

    Пустые подборки на главной не показываем: в ноябре «С животными» — это
    гарантированно пустая страница, и кликать по ней незачем. Сама страница
    остаётся (ссылки не ломаются) и закрыта от индексации.
    """
    buttons = []
    for key, label, slug, *_ in FILTERS:
        if key in skip:
            continue
        if events is not None and key != active:
            if not any(ev["flags"].get(key) for ev in events):
                continue
        # Чип — переключатель, а не ссылка на текущую страницу.
        current = ' aria-current="true"' if key == active else ""
        buttons.append(
            f'<a class="chip" href="/{slug}/" data-filter="{key}"{current}>{label}</a>'
        )
    return "".join(buttons)


def day_chips_html() -> str:
    # Кнопки — переключатели: повторное нажатие снимает день. Отдельного чипа
    # «все выходные» нет: он обещал больше, чем показывал (прошедшее скрыто).
    return (
        '<button class="chip" data-day="sat" aria-pressed="false">Суббота</button>'
        '<button class="chip" data-day="sun" aria-pressed="false">Воскресенье</button>'
    )


def area_names(events: list[dict]) -> list[str]:
    """Населённые пункты области: сам Новосибирск сюда не входит."""
    return sorted({e["area"] for e in events
                   if e["area"] and e["area"] != "Новосибирск"})


def _picker_html(name: str, label: str, options: list[tuple[str, str]], aria: str) -> str:
    """Раскрывающийся список с чекбоксами: мест может быть несколько."""
    items = "".join(
        f'<label class="picker__item"><input type="checkbox" value="{esc(v)}">'
        f"<span>{esc(t)}</span></label>"
        for v, t in options
    )
    return (
        f'<details class="picker" data-picker="{name}">'
        f'<summary class="chip picker__btn" aria-label="{esc(aria)}">'
        f'<span data-picker-label>{esc(label)}</span></summary>'
        f'<div class="picker__menu">{items}'
        '<button type="button" class="chip picker__reset" data-picker-reset>Сбросить</button>'
        "</div></details>"
    )


def district_select_html(events: list[dict]) -> str:
    """Районы города. «Весь Новосибирск» отсекает область, не сужая до района."""
    districts = sorted({e["district"] for e in events if e["district"]})
    if not districts:
        return ""
    options = [("city", "Весь Новосибирск")] + [(d, d) for d in districts]
    return _picker_html("district", "Все районы", options, "Районы Новосибирска")


def area_select_html(events: list[dict], default_label: str = "За городом") -> str:
    """Населённые пункты области — то, что человек ищет в фильтре «За городом»."""
    areas = area_names(events)
    if len(areas) < 2:
        return ""
    return _picker_html("area", default_label, [(n, n) for n in areas],
                        "Населённые пункты области")


def json_ld(events: list[dict], page_name: str) -> str:
    def _offers(ev: dict):
        import re as _re

        common = {
            "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock",
            "url": ev["url"],
        }
        if ev["flags"]["free"]:
            return {"@type": "Offer", "price": 0, **common}
        raw = ev["price"]
        if not raw:
            return None
        nums = []
        for chunk in _re.findall(r"\d[\d\s]{0,6}", raw):
            digits = _re.sub(r"\D", "", chunk)
            if not digits:
                continue
            value = int(digits)
            if 0 < value < 100000:
                nums.append(value)
        if not nums:
            return None
        is_range = len(nums) >= 2 and (_re.search(r"[–—]", raw) or " до " in raw.lower())
        if is_range:
            return {"@type": "AggregateOffer", "lowPrice": min(nums), "highPrice": max(nums), **common}
        return {"@type": "Offer", "price": nums[0], **common}

    items = []
    for ev in events[:20]:
        item = {
            "@type": "Event",
            "name": ev["title"],
            "url": ev["url"],
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
        }
        # Даты для схемы — те же, что видит браузер: со смещением +07:00.
        # Иначе Rich Results получает то дату без времени, то обрезанный период.
        if ev.get("starts_at"):
            item["startDate"] = ev["starts_at"]
        elif ev["start"]:
            item["startDate"] = ev["start"].isoformat()
        if ev.get("ends_at"):
            item["endDate"] = ev["ends_at"]
        elif ev["end"]:
            item["endDate"] = ev["end"].isoformat()
        if ev["text"]:
            item["description"] = cut_text(ev["text"], 240)
        if ev["venue"] or ev["address"]:
            item["location"] = {
                "@type": "Place",
                "name": ev["venue"] or ev["address"],
                "address": postal_address(ev["address"], ev["area"]),
            }
        else:
            item["location"] = {
                "@type": "Place",
                "name": ev["area"] or "Новосибирск",
                "address": postal_address("", ev["area"]),
            }
        org_name = ev["venue"] or ev["source"]
        if org_name:
            item["organizer"] = {"@type": "Organization", "name": org_name}
        offer = _offers(ev)
        if offer:
            item["offers"] = offer
        if ev["image"]:
            item["image"] = ev["image"]
        items.append(item)
    return json.dumps(
        {"@context": "https://schema.org", "@type": "ItemList", "name": page_name, "itemListElement": items},
        ensure_ascii=False,
    )


def report_link() -> str:
    """Ссылка «Сообщить об ошибке»: одна на сайт, в подвале."""
    if not FORM_URL:
        return ""
    return (f'<a href="{esc(FORM_URL)}" target="_blank" '
            f'rel="noopener nofollow" data-track="report">Сообщить об ошибке</a>')


def offer_link() -> str:
    if not FORM_URL:
        return ""
    return (f'<a href="{esc(FORM_URL)}" target="_blank" rel="noopener nofollow" '
            f'data-track="offer">Предложить событие</a>')


def offer_block() -> str:
    """Строка-приглашение прислать событие под первым экраном.

    В каталоге больше двухсот карточек, поэтому блок в самом низу страницы
    никто не увидит. Держим его компактным и сразу под описанием выпуска.
    """
    if not FORM_URL:
        return ""
    return f"""
  <section class="offer" aria-labelledby="offer-title">
    <div class="offer__text">
      <h2 id="offer-title">Знаете хорошее место?</h2>
      <p>Расскажите о событии, которого нет в каталоге: проверим и поставим.</p>
    </div>
    <a href="{esc(FORM_URL)}" target="_blank" rel="noopener nofollow">Предложить событие</a>
  </section>"""


def counter_html() -> str:
    """Счётчик GoatCounter: ставим только когда заполнен код сайта."""
    code = GOATCOUNTER_SITE.strip()
    if not code:
        return ""
    return (f'<script data-goatcounter="https://{code}.goatcounter.com/count" '
            'async src="https://gc.zgo.at/count.js"></script>')


def stats_note() -> str:
    """Строка про статистику: выводим только когда счётчик действительно стоит."""
    if not GOATCOUNTER_SITE.strip():
        return ""
    return ("<br>Статистика посещений — GoatCounter: без cookie, без идентификаторов "
            "посетителя и без хранения IP, только обезличенные счётчики.")


# Роботы, которым сайт открыт намеренно: поисковики и ИИ-сервисы, которые
# приводят людей из ответов на вопросы «куда сходить в Новосибирске».
SEARCH_BOTS = ("YandexBot", "Googlebot", "Bingbot", "Mail.RU_Bot", "Applebot")
AI_BOTS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
           "PerplexityBot", "Perplexity-User", "Google-Extended", "CCBot",
           "meta-externalagent", "Amazonbot", "Applebot-Extended")


def robots_txt() -> str:
    lines = ["# Открыто всем: сайт некоммерческий, скрывать нечего.", "",
             "User-agent: *", "Allow: /", ""]
    lines.append("# Поисковые роботы")
    for bot in SEARCH_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines.append("# Роботы ИИ-сервисов: читать и цитировать можно")
    for bot in AI_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines.append(f"Sitemap: {SITE_URL}/sitemap.xml")
    return "\n".join(lines) + "\n"


def llms_txt(events: list[dict], articles: list | None = None) -> str:
    """Короткое описание проекта для ИИ-краулеров и людей."""
    pages = [
        ("", "Каталог на ближайшие выходные — все события субботы и воскресенья"),
        ("besplatno/", "Бесплатно — без билета"),
        ("s-detmi/", "С детьми — можно прийти с ребёнком"),
        ("s-zhivotnymi/", "С животными — куда пускают с питомцем"),
        ("progulki-i-priroda/", "Прогулки и природа"),
        ("za-gorodom/", "За городом"),
    ]
    lines = [
        f"# {SITE_NAME}",
        "",
        "> Афиша Новосибирска на ближайшие выходные: выставки, лекции, концерты, "
        "прогулки. События собраны из открытых афиш музеев, библиотек и культурных "
        "центров, каталог пересобирается каждую неделю.",
        "",
        f"- Город: Новосибирск, Россия",
        f"- Период: суббота и воскресенье текущей недели",
        f"- Событий в каталоге сейчас: {len(events)}",
        "- Язык: русский",
        "- Обновление: раз в неделю",
        "",
        "## Страницы",
    ]
    for path, text in pages:
        lines.append(f"- [{text}]({SITE_URL}/{path})")
    if articles:
        lines += ["", "## Статьи"]
        for article in articles:
            lines.append(f"- [{article.h1}]({SITE_URL}/statyi/{article.slug}/): "
                         f"{article.description}")
    lines += [
        "",
        "## Что в карточке",
        "Дата и время, площадка и район, цена (минимальная, со словом «от»), "
        "возрастная маркировка по данным организатора, описание события и ссылка "
        "на страницу источника — сайт организатора, билетный сервис или сообщество, "
        "где указаны подробности.",
        "",
        "## Чего на сайте нет",
        "Сайт не продаёт билеты и не собирает персональные данные. Афиши не хранятся "
        "на сайте: изображения загружаются по ссылке источника. За достоверность "
        "события отвечает организатор.",
    ]
    return "\n".join(lines) + "\n"


def footer_html(articles_link: bool = False) -> str:
    """Общий подвал: одинаковый на каталоге, подборках и статьях."""
    articles = '<a href="/statyi/">Статьи</a>' if articles_link else ""
    return f"""<footer>
  <p class="footer__mark">Планы на <em>выходные</em></p>
  <div class="footer__cols">
    <p class="footer__legal">Сайт не просит персональные данные и не хранит афиши: изображения
    загружаются по ссылке источника. Карточка ведёт на страницу источника,
    где можно узнать подробности и купить билет. Ответственность за
    актуальность события несёт организатор. Возрастные ограничения — по
    данным организаторов, у остальных событий метки нет.
    <br>Афиши принадлежат организаторам и их правообладателям: по запросу
    удаляем. {offer_link()} · {report_link()}{stats_note()}</p>
    <nav class="footer__nav" aria-label="Разделы">
      <a href="/">Каталог</a>
      {articles}
    </nav>
  </div>
  <p class="footer__fine">Новосибирск · выпуск по понедельникам и пятницам · собрано вручную из 23 источников</p>
</footer>"""


def plural_ru(n: int) -> str:
    """«1 событие», «2 события», «141 событие», «65 событий»."""
    m, d = n % 100, n % 10
    if 10 < m < 20:
        return "событий"
    if d == 1:
        return "событие"
    if 2 <= d <= 4:
        return "события"
    return "событий"


def page_html(*, title: str, description: str, lead: str, events: list[dict],
              all_events: list[dict], path: str, active_filter: str = "",
              h1: str = "", show_filters: bool = True, canonical: str = "",
              noindex: bool = False, geo_select: bool = False,
              release: datetime | None = None) -> str:
    cards = "\n".join(card_html(ev) for ev in events)
    canonical_url = f"{SITE_URL}/{path}".rstrip("/") + ("/" if path else "/")
    canonical_url = canonical or canonical_url

    is_home = path == "" and bool(events)
    is_404 = path == "404.html"
    now_iso = now_nsk().strftime("%Y-%m-%dT%H:%M:%S+07:00")
    future = [ev for ev in events if (ev.get("ends_at") or "") > now_iso]
    # В воскресенье вечером заканчиваются все события: пустая сетка без
    # объяснения выглядит как поломка. Показываем прошедшее и говорим прямо.
    weekend_over = bool(events) and not future
    free_future = sum(1 for ev in future if ev["flags"]["free"])
    free_total = sum(1 for ev in events if ev["flags"]["free"])
    venues_future = len({ev["venue"] for ev in future if ev.get("venue")})
    venues_total = len({ev["venue"] for ev in events if ev.get("venue")})

    release_html = ""
    if release is not None:
        release_html = (
            f'<p class="release"><time datetime="{release.date().isoformat()}">'
            f"{esc(release_label(release))}</time> · афиши и ссылки — у организаторов</p>"
        )

    # Первый экран — кинокадр: заголовок разбит на две строки, вторая курсивом.
    hero_title = f"<span><i>{esc(h1 or title)}</i></span>"
    if is_home:
        words = (h1 or title).split()
        if len(words) > 2:
            # Пробел между строками обязателен: иначе textContent склеивает
            # «пойти» и «на» в одно слово для копирования и скринридеров.
            hero_title = (f"<span><i>{esc(' '.join(words[:-2]))}</i></span> "
                          f"<span><i><em>{esc(' '.join(words[-2:]))}</em></i></span>")
    hero_media = ""
    hero_preload = ""
    if is_home:
        hero_preload = (
            '<link rel="preload" as="image" href="/assets/img/hero-nsk.jpg" '
            'imagesrcset="assets/img/hero-nsk-1280.jpg 1280w, '
            'assets/img/hero-nsk.jpg 1920w" imagesizes="100vw">'
        )
        hero_media = (
            '<div class="hero__media" aria-hidden="true">'
            '<img class="hero__img" src="/assets/img/hero-nsk.jpg" '
            'srcset="assets/img/hero-nsk-1280.jpg 1280w, assets/img/hero-nsk.jpg 1920w" '
            'sizes="100vw" alt="" width="1920" height="1716" '
            'fetchpriority="high" decoding="async"></div>'
            '<div class="hero__veil" aria-hidden="true"></div>'
            '<div class="hero__glow" aria-hidden="true"></div>'
            '<p class="hero__caption">56°01′ N · 82°55′ E<br>Настроение выпуска</p>'
        )

    eyebrow_label = "Афиша" if is_home else ("Подборка" if events else "404")
    stat_events = len(events) if weekend_over else len(future)
    stat_free = free_total if weekend_over else free_future
    stat_venues = venues_total if weekend_over else venues_future
    hero_stats = ""
    if events:
        hero_stats = (
            '<dl class="hero__stats">'
            f'<div><dt>событий</dt><dd class="stat__num">{stat_events}</dd></div>'
            f'<div><dt>бесплатных</dt><dd class="stat__num">{stat_free}</dd></div>'
            f'<div><dt>площадок</dt><dd class="stat__num">{stat_venues}</dd></div>'
            "</dl>"
        )
    hero_actions = ""
    if is_404:
        hero_actions = ('<div class="hero__actions">'
                        '<a class="btn" href="/">Вернуться в каталог'
                        '<span class="btn__arrow" aria-hidden="true">→</span></a></div>')
    elif events:
        label = "Показать прошедшее" if weekend_over else "Смотреть события"
        hero_actions = ('<div class="hero__actions">'
                        f'<a class="btn" href="#catalog">{label}'
                        '<span class="btn__arrow" aria-hidden="true">→</span></a>')
        if active_filter:
            hero_actions += '<a class="btn btn--ghost" href="/">Весь каталог</a>'
        elif free_future:
            hero_actions += (f'<a class="btn btn--ghost" href="/besplatno/">'
                             f"Бесплатно · {free_future}</a>")
        hero_actions += "</div>"

    weekend_notice = ""
    if weekend_over:
        weekend_notice = (
            '<div class="weekend-over" role="status">'
            '<p class="weekend-over__title">Эти выходные закончились.</p>'
            "<p>Следующий выпуск — в пятницу. Пока показываем прошедшее.</p>"
            "</div>"
        )

    catalog_head = ""
    if events:
        title_line = f"Выпуск {weekend_label()}" if is_home else "События"
        count_line = f"{len(events)} {plural_ru(len(events))}"
        if is_home:
            count_line += " · обновляется по понедельникам и пятницам"
        catalog_head = (
            '<div class="catalog-head">'
            f'<h2 id="catalog-title">{esc(title_line)}</h2>'
            f"<p>{esc(count_line)}</p>"
            "</div>"
        )

    controls = ""
    if show_filters:
        # Дни и счётчик — первая строка, остальные фильтры — вторая: так на
        # телефоне сразу видно, что показано, и не приходится листать чипы.
        # «За городом» здесь не чип, а список населённых пунктов области —
        # так же, как районы города в соседнем списке. На остальных подборках
        # чип остаётся: по нему иначе не дойти до страницы /za-gorodom/.
        skip = frozenset({"outside"}) if active_filter in ("", "outside") else frozenset()
        if geo_select:
            places = area_select_html(all_events, default_label="Вся область")
        else:
            places = district_select_html(all_events) + area_select_html(all_events)
        controls = (
            '<div class="controls" data-open="false" data-active="false">'
            '<div class="controls__row controls__row--top">'
            + day_chips_html()
            # Дубль «Бесплатно» для верхней строки: виден только на телефоне
            # и только в воскресенье, когда кнопки «Суббота» рядом уже нет.
            + '<a class="chip chip--top" href="/besplatno/" data-filter="free">Бесплатно</a>'
            + f'<span class="count" role="status" aria-live="polite" aria-atomic="true">'
              f'<span data-count>{len(events)} {plural_ru(len(events))}</span></span>'
            + '<button class="filters-toggle" type="button" aria-expanded="false" '
              'aria-controls="filters-more" aria-label="Показать фильтры">'
              '<svg class="filters-toggle__icon" viewBox="0 0 24 24" width="20" height="20" fill="none" '
              'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
              '<path d="M4 7h16M7 12h10M10 17h4"/></svg>'
              '<span class="filters-toggle__label">Фильтры</span>'
              '<svg class="filters-toggle__chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" '
              'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" '
              'aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
              "</button>"
            + "</div>"
            '<div class="controls__row controls__row--more" id="filters-more">'
            + chips_html(active_filter, all_events, skip=skip)
            + places
            + '<button class="chip" data-reset>Сбросить</button>'
            + "</div>"
            "</div>"
        )

    catalog_block = ""
    if not is_404:
        catalog_block = f"""
  <section class="catalog"{' aria-labelledby="catalog-title"' if events else ''}>
  {catalog_head}
  <div class="grid">{cards}</div>

  <p class="past" data-past hidden>
    <span data-past-label>Прошедшие события скрыты.</span>
    <button class="chip" data-past-toggle aria-pressed="false"></button>
  </p>

  <p class="empty" data-empty>
    <span data-empty-text>По этим фильтрам на выходные ничего нет.</span>
    <button class="chip" data-reset>Сбросить фильтры</button>
  </p>
  </section>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<script>document.documentElement.classList.add('js');setTimeout(function(){{if(!window.__revealReady)document.documentElement.classList.add('reveal-off')}},3000)</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{('<meta name="robots" content="noindex,follow">' if noindex else '')}
<link rel="canonical" href="{esc(canonical_url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical_url)}">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Планы на выходные — афиша Новосибирска">
<meta property="og:locale" content="ru_RU">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE_URL}/og.png">
<meta name="theme-color" content="#0B0D0C">
<link rel="preload" href="/assets/fonts/cormorant-cyrillic.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/assets/fonts/golos-text-cyrillic.woff2" as="font" type="font/woff2" crossorigin>
{hero_preload}
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/mark.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<script type="application/ld+json">{json_ld(events, title)}</script>
</head>
<body class="page page--catalog"{' data-weekend-over="true"' if weekend_over else ''}>
<div class="grain" aria-hidden="true"></div>
<div class="progress" aria-hidden="true"><span></span></div>
<a class="skip-link" href="{'#events' if is_404 else '#catalog'}">Перейти к содержимому</a>
<div class="wrap">
  <header class="top">
    {header_html()}
  </header>

  <main id="events"{' data-show-past="true"' if weekend_over else ''}>
  <section class="{'hero hero--photo' if is_home else 'hero hero--masthead'}"
           aria-labelledby="hero-title">
  {hero_media}
    <div class="hero__inner">
      <p class="eyebrow"><span>{eyebrow_label}</span><span>Новосибирск</span>
        <span>{esc(weekend_label())}</span></p>
      <h1 class="hero__title" id="hero-title">{hero_title}</h1>
      <p class="hero__lead">{esc(lead)}</p>
      {hero_actions}
    </div>
    {hero_stats}
    {release_html}
  </section>

  {offer_block() if show_filters else ""}
  {controls}
  {weekend_notice}
  {catalog_block}
  </main>

  {footer_html(articles_link=bool(ARTICLES))}
</div>
<script src="/assets/app.js?v={BUILD_VERSION}"></script>
{counter_html()}
</body>
</html>"""


def header_html() -> str:
    """Шапка: знак, название, разделы и дата выходных."""
    picks = "".join(
        f'<a href="/{slug}/">{esc(label)}</a>'
        for key, label, slug, *_ in FILTERS
    )
    return (
        '<a class="brand" href="/">'
        '<img class="brand__mark" src="/mark.svg" alt="" width="30" height="30">'
        '<span class="brand__text">'
        '<span class="brand__name">Планы на выходные</span>'
        '<span class="brand__city">Новосибирск</span>'
        "</span></a>"
        '<nav class="top__nav" aria-label="Разделы">'
        '<a href="/#catalog">Афиша</a>'
        '<details class="nav-drop"><summary>Подборки</summary>'
        f'<div class="nav-drop__menu">{picks}</div></details>'
        '<a href="/statyi/">Статьи</a>'
        f'<span class="weekend">{esc(weekend_label())}</span>'
        "</nav>"
    )


def weekend_label() -> str:
    sat, sun = CURRENT_WEEKEND
    if sat.month == sun.month:
        return f"{sat.day}–{sun.day} {MONTHS[sun.month - 1]}"
    return f"{sat.day} {MONTHS[sat.month - 1]} – {sun.day} {MONTHS[sun.month - 1]}"


NOW_OVERRIDE: datetime | None = None


def now_nsk() -> datetime:
    """Текущее время в Новосибирске.

    В Windows нет системной базы часовых поясов, поэтому zoneinfo может
    упасть; тогда берём фиксированный сдвиг UTC+7 — летнего времени в
    Новосибирске нет с 2014 года, разница круглый год одна.
    """
    if NOW_OVERRIDE is not None:
        return NOW_OVERRIDE
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Novosibirsk")).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone(timedelta(hours=7))).replace(tzinfo=None)


def release_label(moment: datetime) -> str:
    """«Выпуск от 9 ноября» — дата, на которую собран каталог."""
    return f"Выпуск от {moment.day} {MONTHS[moment.month - 1]}"


def human_date(iso: str) -> str:
    """«2026-11-06» → «6 ноября 2026»."""
    try:
        value = datetime.strptime((iso or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def filter_slug(key: str) -> str:
    for item in FILTERS:
        if item[0] == key:
            return item[2]
    return ""


def article_live_block(article, events: list[dict]) -> str:
    """Живой блок: четыре актуальных события под тему статьи.

    Он и делает статью «свежей» в глазах поисковика: текст не меняется, а
    содержимое блока — каждую неделю.
    """
    key = article.filter
    matched = [ev for ev in events if not key or ev["flags"].get(key)]
    # Один источник часто отдаёт десяток событий со ссылкой на общую страницу.
    # В блоке такие строки выглядят как четыре ссылки в одно место, поэтому
    # сначала берём по одному событию на ссылку.
    subset: list[dict] = []
    seen_urls: set[str] = set()
    for ev in matched:
        url = ev.get("url") or ""
        if url and url in seen_urls:
            continue
        subset.append(ev)
        if url:
            seen_urls.add(url)
        if len(subset) == 4:
            break
    if not subset:
        return ""

    items = "".join(
        f'<li><a href="{esc(ev["url"])}" target="_blank" rel="noopener nofollow">'
        f'{esc(ev["title"])}</a> — {esc(date_label(ev))}</li>'
        for ev in subset
    )
    slug = filter_slug(key)
    more = (f'<a href="/{slug}/">Все события подборки</a>' if slug
            else '<a href="/">Все события на выходные</a>')
    return (
        '<section class="article__live">'
        "<h2>Что идёт на этих выходных</h2>"
        f"<ul>{items}</ul>"
        f"<p>{more}</p>"
        "</section>"
    )


def article_json_ld(article) -> str:
    url = f"{SITE_URL}/statyi/{article.slug}/"
    article_data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article.h1,
        "description": article.description,
        "datePublished": article.date,
        "dateModified": article.updated or article.date,
        "inLanguage": "ru-RU",
        "author": {"@type": "Person", "name": "Алиса Спиридонова"},
        "publisher": {"@type": "Organization", "name": SITE_NAME, "url": f"{SITE_URL}/"},
        "image": f"{SITE_URL}/og.png",
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
    }
    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Главная", "item": f"{SITE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "Статьи", "item": f"{SITE_URL}/statyi/"},
            {"@type": "ListItem", "position": 3, "name": article.h1, "item": url},
        ],
    }
    return json.dumps([article_data, crumbs], ensure_ascii=False)


def article_page_html(article, events: list[dict], others: list) -> str:
    url = f"{SITE_URL}/statyi/{article.slug}/"
    body = articles_store.render_markdown(article.body)
    live = article_live_block(article, events)

    related = [a for a in others if a.slug != article.slug][:3]
    related_html = ""
    if related:
        links = "".join(
            f'<li><a href="/statyi/{esc(a.slug)}/">{esc(a.h1)}</a></li>' for a in related
        )
        related_html = (f'<section class="article__related"><h2>Читайте также</h2>'
                        f"<ul>{links}</ul></section>")

    updated = human_date(article.updated or article.date)
    meta = f'<p class="article__meta">Обновлено {esc(updated)}</p>' if updated else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<script>document.documentElement.classList.add('js');setTimeout(function(){{if(!window.__revealReady)document.documentElement.classList.add('reveal-off')}},3000)</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(article.title)}</title>
<meta name="description" content="{esc(article.description)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(article.title)}">
<meta property="og:description" content="{esc(article.description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="ru_RU">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0B0D0C">
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/mark.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<script type="application/ld+json">{article_json_ld(article)}</script>
</head>
<body class="page page--article">
<div class="grain" aria-hidden="true"></div>
<div class="progress" aria-hidden="true"><span></span></div>
<a class="skip-link" href="#content">Перейти к тексту</a>
<div class="wrap">
  <header class="top">
    {header_html()}
  </header>

  <nav class="crumbs"><a href="/">Главная</a> · <a href="/statyi/">Статьи</a></nav>

  <main id="content">
  <article class="article">
    <h1>{esc(article.h1)}</h1>
    {meta}
    {body}
    {live}
    {related_html}
  </article>
  </main>

  {footer_html(articles_link=True)}
</div>
<script src="/assets/app.js?v={BUILD_VERSION}"></script>
{counter_html()}
</body>
</html>"""


def statyi_index_html(articles: list) -> str:
    """Список статей: точка входа для поисковика и для читателя."""
    items = "".join(
        f'<li><a href="/statyi/{esc(a.slug)}/">{esc(a.h1)}</a>'
        f"<p>{esc(a.description)}</p></li>"
        for a in articles
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<script>document.documentElement.classList.add('js');setTimeout(function(){{if(!window.__revealReady)document.documentElement.classList.add('reveal-off')}},3000)</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статьи о том, как выбрать событие на выходные — Новосибирск</title>
<meta name="description" content="Как выбрать событие на выходные в Новосибирске: разборы форматов, советы по возрасту, цене и дороге.">
<link rel="canonical" href="{SITE_URL}/statyi/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="Статьи — {esc(SITE_NAME)}">
<meta property="og:url" content="{SITE_URL}/statyi/">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta name="theme-color" content="#0B0D0C">
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/mark.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
</head>
<body class="page page--article">
<div class="grain" aria-hidden="true"></div>
<div class="progress" aria-hidden="true"><span></span></div>
<a class="skip-link" href="#content">Перейти к статьям</a>
<div class="wrap">
  <header class="top">
    {header_html()}
  </header>

  <nav class="crumbs"><a href="/">Главная</a> · Статьи</nav>

  <main id="content">
  <h1 class="page-title">Статьи</h1>
  <p class="lead">Как выбрать событие, а не листать двадцать вкладок.</p>
  <ul class="statyi">{items}</ul>
  </main>

  {footer_html(articles_link=False)}
</div>
<script src="/assets/app.js?v={BUILD_VERSION}"></script>
{counter_html()}
</body>
</html>"""


def build(rows: list[dict], out: Path, today: date, base: str = "",
          closures: ClosureStore | None = None) -> None:
    global CURRENT_WEEKEND, ARTICLES
    CURRENT_WEEKEND = weekend_of(today)
    ARTICLES = articles_store.load_articles(ROOT / "content" / "articles")
    sat, sun = CURRENT_WEEKEND
    release = now_nsk()
    base = base.rstrip("/")

    events = [e for e in events_from_rows(rows) if on_weekend(e, sat, sun)]

    # Расписание площадки знает не каждый источник: у музея строка-первоисточник
    # приходит с open_days, а карточка агрегатора — без них. Иначе событие
    # висело бы в воскресенье у музея, закрытого по воскресеньям.
    venue_days: dict[str, str] = {}
    for ev in events:
        key = (ev.get("venue") or "").strip().lower()
        if key and ev.get("open_days"):
            venue_days.setdefault(key, ev["open_days"])
    for ev in events:
        if not ev.get("open_days"):
            ev["open_days"] = venue_days.get((ev.get("venue") or "").strip().lower(), "")

    # Площадка может не работать в один из выходных дней (санитарный день,
    # ремонт): такой день вычитаем, а если закрыты оба — событие не показываем.
    kept: list[dict] = []
    dropped = 0
    for ev in events:
        days = days_of(ev, sat, sun)

        # Площадка может работать не каждый день недели: музей с выходным в
        # воскресенье остаётся только в субботу, а закрытый в оба выходных
        # дня в каталог не попадает вовсе.
        allowed = open_weekdays(ev)
        if allowed is not None:
            days = [d for d in days if (sat if d == "sat" else sun).weekday() in allowed]

        if closures is not None:
            days = [d for d in days
                    if not closures.is_closed(ev.get("venue", ""), sat if d == "sat" else sun)]
        ev["days"] = days
        if days:
            ev["starts_at"], ev["ends_at"] = event_bounds(ev, sat, sun)
            kept.append(ev)
        else:
            dropped += 1
    if dropped:
        print(f"[i] Скрыто по закрытию площадок: {dropped}")
    events = kept
    events.sort(key=sort_key)
    if not events:
        print("[!] На выбранные выходные нет событий — проверьте даты в таблице")

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)

    def local(content: str) -> str:
        """Пути вида /assets/… → с учётом подпапки на GitHub Pages.

        Сначала подставляем подпапку, затем заглушку: иначе «src="PLACEHOLDER"»
        превращается в двойной префикс /plany-na-vyhodnye/plany-na-vyhodnye/…
        и картинки-заглушки ломаются.
        """
        if base:
            content = content.replace('href="/', f'href="{base}/').replace('src="/', f'src="{base}/')
        content = content.replace("PLACEHOLDER",
                                  f"{base}/placeholder.svg" if base else "/placeholder.svg")
        return content

    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(
        JS.replace("PLACEHOLDER", f"{base}/placeholder.svg" if base else "/placeholder.svg"),
        encoding="utf-8",
    )
    (out / "placeholder.svg").write_text(PLACEHOLDER_SVG, encoding="utf-8")

    if STATIC_DIR.exists():
        # Обходим вложенные папки: так в сборку попадают шрифты
        # (static/assets/fonts/…) и иконки, а не только файлы из корня.
        for extra in sorted(STATIC_DIR.rglob("*")):
            if extra.is_file():
                target = out / extra.relative_to(STATIC_DIR)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(extra.read_bytes())

    def write(rel: str, content: str) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(local(content), encoding="utf-8")

    write("index.html", page_html(
        title=f"Куда пойти на выходных в Новосибирске — {weekend_label()}",
        description="Афиша Новосибирска на субботу и воскресенье: выставки, лекции, концерты "
                    "и прогулки. Адреса, ссылки на организаторов, есть бесплатные и для детей.",
        lead="Анонсы выставок, лекций, концертов и прогулок. Каждое событие ведёт на источник — там актуальная информация о мероприятии.",
        events=events, all_events=events, path="",
        h1="Куда пойти на выходных",
        release=release,
    ))

    indexable: list[str] = []
    for key, label, slug, title, lead in FILTERS:
        subset = [e for e in events if e["flags"][key]]
        if subset:
            indexable.append(slug)
        write(f"{slug}/index.html", page_html(
            title=title, description=lead, lead=lead,
            events=subset, all_events=events, path=f"{slug}/",
            active_filter=key, h1=title,
            geo_select=key == "outside",
            release=release,
            # пустую страницу не отдаём поисковикам: она ничего не даёт пользователю
            noindex=not subset,
        ))
        print(f"  /{slug}/ — {len(subset)} событий"
              + ("" if subset else " (пусто, закрыта от индексации)"))

    # Статьи: отдельные страницы /statyi/<slug>/ и оглавление /statyi/.
    if ARTICLES:
        for article in ARTICLES:
            write(f"statyi/{article.slug}/index.html",
                  article_page_html(article, events, ARTICLES))
            print(f"  /statyi/{article.slug}/ — статья")
        write("statyi/index.html", statyi_index_html(ARTICLES))
        print(f"  /statyi/ — статей: {len(ARTICLES)}")

    # Старый адрес «с собакой» — мягкий редирект на новую страницу,
    # чтобы не ломались уже сохранённые и проиндексированные ссылки.
    redirect_url = f"{base}/s-zhivotnymi/" if base else "/s-zhivotnymi/"
    write("s-sobakoy/index.html", f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={redirect_url}">
<link rel="canonical" href="{SITE_URL}/s-zhivotnymi/">
<title>Страница переехала</title></head>
<body><p>Страница переехала: <a href="/s-zhivotnymi/">С животными</a></p>
{counter_html()}</body></html>""")
    write("404.html", page_html(
        title="Страница не найдена", description="Такой страницы нет.",
        lead="Кажется, этой страницы нет. Вернитесь в каталог событий на выходные.",
        events=[], all_events=events, path="404.html", show_filters=False,
        canonical=f"{SITE_URL}/404.html",
    ))

    urls = [""] + [f"{slug}/" for slug in indexable]
    today_iso = today.isoformat()
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemap.org/schemas/sitemap/0.9">'.replace(
                   "www.sitemap.org", "www.sitemaps.org")]
    for rel in urls:
        sitemap.append(f"  <url><loc>{SITE_URL}/{rel}</loc><lastmod>{today_iso}</lastmod>"
                       f"<changefreq>weekly</changefreq><priority>{'1.0' if not rel else '0.8'}</priority></url>")
    if ARTICLES:
        sitemap.append(f"  <url><loc>{SITE_URL}/statyi/</loc><lastmod>{today_iso}</lastmod>"
                       f"<changefreq>weekly</changefreq><priority>0.7</priority></url>")
        for article in ARTICLES:
            # lastmod статьи — дата её обновления, а не дата сборки: поисковик
            # должен видеть, что менялся именно текст.
            stamp = article.updated or article.date or today_iso
            sitemap.append(f"  <url><loc>{SITE_URL}/statyi/{article.slug}/</loc>"
                           f"<lastmod>{stamp}</lastmod>"
                           f"<changefreq>monthly</changefreq><priority>0.6</priority></url>")
    sitemap.append("</urlset>")
    (out / "sitemap.xml").write_text("\n".join(sitemap), encoding="utf-8")
    (out / "robots.txt").write_text(robots_txt(), encoding="utf-8")
    (out / "llms.txt").write_text(llms_txt(events, ARTICLES), encoding="utf-8")

    pages_count = 7 + (len(ARTICLES) + 1 if ARTICLES else 0)
    print(f"[+] Готово: {out} — страниц {pages_count}, событий на выходные {len(events)}")
    if base:
        print(f"[+] Это версия для публикации: заливать содержимое {out} "
              f"в репозиторий (адрес {SITE_URL}/)")
    else:
        print(f"[!] Сборка без --base: только для локального просмотра "
              f"(python -m http.server --directory {out}). На GitHub её заливать нельзя — "
              f"стили и ссылки будут битыми.")


CSS = """
/* «Ночная афиша» — тёмный зал, тёплый свет проектора, костяная типографика.
   Одна идентичность без переключения схемы: сайт должен узнаваться снимком
   экрана, а не подстраиваться под систему. */

@font-face{font-family:"Cormorant Garamond";font-style:normal;font-weight:300 600;font-display:swap;
  src:url("fonts/cormorant-cyrillic.woff2") format("woff2");
  unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:"Cormorant Garamond";font-style:normal;font-weight:300 600;font-display:swap;
  src:url("fonts/cormorant-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,
  U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}
@font-face{font-family:"Cormorant Garamond";font-style:italic;font-weight:300 600;font-display:swap;
  src:url("fonts/cormorant-italic-cyrillic.woff2") format("woff2");
  unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:"Cormorant Garamond";font-style:italic;font-weight:300 600;font-display:swap;
  src:url("fonts/cormorant-italic-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,
  U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}
@font-face{font-family:"Golos Text";font-style:normal;font-weight:400 900;font-display:swap;
  src:url("fonts/golos-text-cyrillic.woff2") format("woff2");
  unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:"Golos Text";font-style:normal;font-weight:400 900;font-display:swap;
  src:url("fonts/golos-text-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,
  U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}

:root{
  color-scheme:dark;
  --night:#0B0D0C;--night-2:#101312;--night-3:#161917;
  --bone:#EDE6D8;--bone-2:#D8CFBF;--dim:#A79E90;--muted:#8C8478;
  --brass:#C9A15F;--brass-hi:#E6C58C;
  --line:rgba(237,230,216,.14);--line-2:rgba(237,230,216,.28);
  --media:#131615;
  --shell:1400px;--pad:clamp(18px,4vw,48px);
  --ease:cubic-bezier(.16,1,.3,1);
  --serif:"Cormorant Garamond",Georgia,"Times New Roman",serif;
  --sans:"Golos Text",-apple-system,"Segoe UI",Roboto,sans-serif}

*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:
    radial-gradient(1100px 700px at 88% -8%,rgba(201,161,95,.07),transparent 62%),
    var(--night);
  color:var(--bone);font:400 17px/1.6 var(--sans);-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility}
h1,h2,h3,.brand__name,.card__title,.footer__mark,.stat__num,.hero__title{
  font-family:var(--serif);font-weight:400;letter-spacing:-.01em}
a{color:var(--brass-hi)}
img{max-width:100%}
::selection{background:var(--brass);color:var(--night)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
[hidden]{display:none !important}
:focus-visible{outline:1px solid var(--brass-hi);outline-offset:4px}
/* Светлая кнопка на светлом фоне: латунное кольцо на кости почти не видно. */
.btn:focus-visible{outline:2px solid var(--night);outline-offset:3px}
.btn--ghost:focus-visible{outline-color:var(--brass-hi)}
:focus:not(:focus-visible){outline:none}
.skip-link{position:absolute;left:-9999px;top:0;z-index:200;background:var(--bone);color:var(--night);
  padding:12px 20px;font:600 14px/1 var(--sans);text-decoration:none}
.skip-link:focus{left:0}

/* Плёнка: зерно поверх всего кадра, но ниже интерфейсных слоёв. */
.grain{position:fixed;inset:-40%;z-index:90;pointer-events:none;opacity:.055;
  mix-blend-mode:overlay;background-repeat:repeat;background-size:160px 160px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='.6'/%3E%3C/svg%3E");
  animation:grainShift 7s steps(6) infinite}
@keyframes grainShift{
  0%{transform:translate3d(0,0,0)}20%{transform:translate3d(-2%,1%,0)}
  40%{transform:translate3d(1%,-2%,0)}60%{transform:translate3d(-1%,2%,0)}
  80%{transform:translate3d(2%,1%,0)}100%{transform:translate3d(0,0,0)}}
.progress{position:fixed;inset:0 0 auto 0;height:1px;z-index:120;background:transparent}
.progress span{display:block;height:100%;background:linear-gradient(90deg,var(--brass),var(--brass-hi));
  transform:scaleX(0);transform-origin:0 50%}

.wrap{position:relative;max-width:var(--shell);margin:0 auto;padding:0 var(--pad)}

/* ---------------------------------------------------------------- шапка */
header.top{display:flex;justify-content:space-between;align-items:center;gap:24px;
  padding:20px 0 18px;border-bottom:1px solid var(--line);position:relative;z-index:20}
.brand{display:inline-flex;align-items:center;gap:13px;text-decoration:none;color:var(--bone)}
.brand__mark{display:block;width:30px;height:30px;border-radius:7px}
.brand__text{display:flex;flex-direction:column;gap:4px}
.brand__name{font-size:19px;font-weight:500;line-height:1;letter-spacing:0}
.brand__city{font:600 9.5px/1 var(--sans);letter-spacing:.24em;text-transform:uppercase;color:var(--muted)}
.top__nav{display:flex;align-items:center;gap:clamp(16px,3vw,34px)}
.top__nav a{font:500 13px/1 var(--sans);letter-spacing:.02em;color:var(--bone-2);text-decoration:none;
  border-bottom:1px solid transparent;padding-bottom:4px;transition:color .35s var(--ease),border-color .35s}
.top__nav a:hover{color:var(--bone);border-color:var(--brass)}
.weekend{font:500 11px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--dim);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.nav-drop{position:relative}
.nav-drop summary{list-style:none;cursor:pointer;font:500 13px/1 var(--sans);color:var(--bone-2);
  padding-bottom:4px;border-bottom:1px solid transparent;
  transition:color .3s var(--ease),border-color .3s var(--ease)}
.nav-drop summary::-webkit-details-marker{display:none}
.nav-drop summary::after{content:"";display:inline-block;width:5px;height:5px;margin-left:7px;
  border-right:1px solid currentColor;border-bottom:1px solid currentColor;
  transform:rotate(45deg) translateY(-1px)}
.nav-drop[open] summary::after{transform:rotate(-135deg) translateY(-1px)}
.nav-drop summary:hover{color:var(--bone);border-color:var(--brass)}
.nav-drop__menu{position:absolute;top:calc(100% + 14px);left:0;z-index:60;min-width:230px;
  display:flex;flex-direction:column;padding:8px;background:#121514;border:1px solid var(--line-2)}
.nav-drop__menu a{padding:10px;font:500 13px/1.2 var(--sans);color:var(--bone-2);border:0}
.nav-drop__menu a:hover{background:rgba(237,230,216,.06);color:var(--bone)}

/* ---------------------------------------------------------------- герой */
.hero{position:relative;isolation:isolate;display:flex;flex-direction:column;justify-content:flex-end;
  min-height:min(86vh,800px);min-height:min(86svh,800px);width:100vw;margin-inline:calc(50% - 50vw);
  padding:clamp(44px,7vw,84px) var(--pad) clamp(26px,4vw,44px);overflow:hidden}
.hero__media{position:absolute;inset:0;z-index:-3;overflow:hidden;background:var(--night-2)}
.hero__img{width:100%;height:100%;object-fit:cover;object-position:58% 50%;
  transform:scale(1.04);filter:brightness(.62) saturate(.72) contrast(1.04);
  animation:drift 36s var(--ease) infinite alternate}
@keyframes drift{from{transform:scale(1.04) translate3d(0,0,0)}
  to{transform:scale(1.11) translate3d(-1.2%,-1.4%,0)}}
.hero__veil{position:absolute;inset:0;z-index:-2;background:
  linear-gradient(to top,var(--night) 0%,rgba(11,13,12,.9) 12%,rgba(11,13,12,.34) 52%,rgba(11,13,12,.1) 100%),
  linear-gradient(to right,rgba(11,13,12,.86) 0%,rgba(11,13,12,.28) 58%,rgba(11,13,12,.02) 100%),
  radial-gradient(900px 520px at 82% 6%,rgba(201,161,95,.16),transparent 62%)}
.hero__glow{position:absolute;inset:0;z-index:-1;opacity:0;transition:opacity .8s var(--ease);
  background:radial-gradient(560px circle at var(--mx,70%) var(--my,30%),
    rgba(230,197,140,.16),transparent 62%)}
.hero:hover .hero__glow{opacity:1}
.hero__caption{position:absolute;right:var(--pad);top:clamp(44px,7vw,84px);margin:0;
  font:500 10px/1.5 var(--sans);letter-spacing:.2em;text-transform:uppercase;color:rgba(237,230,216,.55);
  text-align:right;z-index:2}
.hero__inner,.hero__stats,.hero__credit{position:relative;z-index:2}
.hero__inner{max-width:1040px}
.eyebrow{display:flex;align-items:center;flex-wrap:wrap;gap:12px 16px;margin:0;
  font:600 10.5px/1 var(--sans);letter-spacing:.22em;text-transform:uppercase;color:var(--brass)}
.eyebrow span{display:inline-flex;align-items:center;gap:16px}
.eyebrow span+span::before{content:"";width:22px;height:1px;background:rgba(201,161,95,.5)}
.hero__title{margin:clamp(18px,2.6vw,30px) 0 0;font-size:clamp(46px,8.4vw,122px);line-height:.92;
  letter-spacing:-.022em;text-wrap:balance}
.hero__title span{display:block;overflow:hidden;padding-bottom:.04em}
.hero__title i{display:block;font-style:normal}
.hero__title em{font-style:normal;color:var(--bone)}
.hero__lead{max-width:45ch;margin:clamp(16px,2.2vw,24px) 0 0;color:var(--bone-2);
  font-size:clamp(15.5px,1.35vw,19px);line-height:1.58}
.hero__actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:clamp(24px,3.6vw,38px)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:12px;min-height:52px;
  padding:0 26px;border:1px solid transparent;border-radius:0;
  font:600 15px/1 var(--sans);letter-spacing:0;text-decoration:none;
  color:var(--night);background:var(--bone);
  transition:background .45s var(--ease),color .45s var(--ease),border-color .45s var(--ease)}
.btn:hover{background:var(--brass-hi)}
.btn__arrow{transition:transform .45s var(--ease)}
.btn:hover .btn__arrow{transform:translateX(5px)}
.btn--ghost{background:transparent;color:var(--bone);border-color:var(--line-2)}
.btn--ghost:hover{background:transparent;border-color:var(--bone);color:var(--bone)}
.hero__stats{display:flex;flex-wrap:wrap;gap:clamp(22px,4.4vw,64px);margin:clamp(34px,5.5vw,60px) 0 0;
  padding-top:20px;border-top:1px solid var(--line)}
.hero__stats div{display:flex;flex-direction:column-reverse;gap:6px}
.hero__stats dt{font:500 10px/1 var(--sans);letter-spacing:.2em;text-transform:uppercase;color:var(--muted)}
.stat__num{font-size:clamp(30px,3.4vw,46px);line-height:1;color:var(--bone);font-variant-numeric:lining-nums}
.release{margin:16px 0 0;font:500 10.5px/1.6 var(--sans);letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted)}
.release time{font-variant-numeric:tabular-nums;color:var(--dim)}
.hero--masthead{min-height:0;padding-top:clamp(44px,6vw,72px);padding-bottom:clamp(32px,5vw,56px);
  border-bottom:1px solid var(--line)}
.hero--masthead .hero__title{font-size:clamp(36px,5vw,64px)}
.hero--masthead .hero__stats{margin-top:clamp(26px,4vw,40px)}

/* ---------------------------------------------------------------- фильтры */
.controls{position:sticky;top:0;z-index:40;display:flex;flex-direction:column;gap:10px;
  margin-inline:calc(-1 * var(--pad));padding:13px var(--pad);
  background:rgba(11,13,12,.96);
  -webkit-backdrop-filter:blur(18px) saturate(130%);
  backdrop-filter:blur(18px) saturate(130%);
  border-bottom:1px solid var(--line);scroll-margin-top:80px}
.controls__row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.chip{display:inline-flex;align-items:center;border:1px solid var(--line-2);background:transparent;
  color:var(--bone-2);border-radius:2px;padding:9px 14px;font:500 12.5px/1 var(--sans);
  letter-spacing:.01em;cursor:pointer;font-family:inherit;text-decoration:none;
  transition:color .3s var(--ease),border-color .3s var(--ease),background .3s var(--ease)}
.chip:hover{color:var(--bone);border-color:var(--bone)}
a.chip:hover{text-decoration:none}
.chip[aria-pressed="true"],.chip[aria-current]{
  background:transparent;border-color:var(--brass);color:var(--bone)}
.chip.chip--top{display:none}
select{border:1px solid var(--line-2);background:transparent;color:var(--bone-2);border-radius:2px;
  padding:9px 14px;font:500 12.5px/1 var(--sans);font-family:inherit}
.picker{position:relative}
.picker__btn{display:inline-flex;align-items:center;gap:8px}
.picker__btn::-webkit-details-marker{display:none}
.picker__btn::after{content:"";width:5px;height:5px;margin-left:2px;
  border-right:1px solid currentColor;border-bottom:1px solid currentColor;
  transform:rotate(45deg) translateY(-1px);opacity:.7;transition:transform .3s var(--ease)}
.picker[open] .picker__btn::after{transform:rotate(-135deg) translateY(-1px)}
.picker.is-set .picker__btn{border-color:var(--brass);color:var(--brass-hi)}
.picker__menu{position:absolute;top:calc(100% + 8px);left:0;z-index:20;min-width:250px;
  max-width:min(330px,calc(100vw - 32px));max-height:min(60vh,380px);overflow:auto;
  background:#121514;border:1px solid var(--line-2);border-radius:2px;padding:6px;
  box-shadow:0 30px 60px -20px rgba(0,0,0,.8)}
.picker__item{display:flex;align-items:center;gap:10px;padding:10px;font-size:13.5px;color:var(--bone-2);
  cursor:pointer;transition:background .25s}
.picker__item:hover{background:rgba(237,230,216,.06);color:var(--bone)}
.picker__item input{width:15px;height:15px;accent-color:var(--brass);flex:0 0 auto}
.picker__reset{margin:4px;font-size:12px}
@media (max-width:640px){
  .picker[open]{flex:1 0 100%}
  .picker__menu{position:static;margin-top:8px;width:100%;max-width:none;box-shadow:none;
    max-height:50vh;background:transparent;border-color:var(--line)}}
.count{margin-left:auto;font:500 12px/1 var(--sans);letter-spacing:.01em;
  color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.filters-toggle{display:none;align-items:center;justify-content:center;gap:7px;
  border:1px solid var(--line-2);background:transparent;color:var(--bone-2);border-radius:2px;
  padding:0 14px 0 12px;width:auto;min-width:44px;height:42px;font-family:inherit;cursor:pointer}
.filters-toggle__label{font:500 12.5px/1 var(--sans)}
.filters-toggle__chevron{flex:0 0 auto;transition:transform .3s var(--ease)}
.filters-toggle[aria-expanded="true"]{border-color:var(--brass);color:var(--brass-hi)}
.filters-toggle[aria-expanded="true"] .filters-toggle__chevron{transform:rotate(180deg)}
@media (max-width:345px){
  .filters-toggle{padding:0 9px;gap:1px}
  .filters-toggle__label{display:none}}

/* ---------------------------------------------------------------- каталог */
.catalog{padding-top:clamp(38px,6vw,76px)}
.catalog-head{display:flex;justify-content:space-between;align-items:flex-end;gap:28px;flex-wrap:wrap}
.catalog-head h2{margin:0;font-size:clamp(30px,3.6vw,50px);line-height:1}
.catalog-head p{margin:0 0 6px;max-width:44ch;color:var(--dim);font-size:13.5px;line-height:1.6}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(286px,1fr));
  gap:clamp(26px,3vw,40px) clamp(18px,2.2vw,28px);margin-top:clamp(26px,3.4vw,40px)}
.card{display:flex;flex-direction:column;background:transparent;border:0;border-radius:0;overflow:visible}
.card__media{position:relative;display:block;aspect-ratio:4/3;border:1px solid var(--line);
  background:#121514;overflow:hidden;transition:border-color .5s var(--ease)}
.card__media::after{content:"";position:absolute;inset:10px;z-index:1;pointer-events:none;
  background:linear-gradient(to top,rgba(11,13,12,.42),transparent 46%);opacity:0;
  transition:opacity .6s var(--ease)}
.card__media img{position:absolute;inset:10px;width:calc(100% - 20px);height:calc(100% - 20px);
  object-fit:contain;object-position:center;opacity:1;
  transition:opacity .5s var(--ease)}
.card:hover .card__media{border-color:var(--line-2)}
.card:hover .card__media::after{opacity:1}
.card__body{display:flex;flex-direction:column;gap:8px;flex:1;padding:16px 2px 0}
.card__meta{margin:0;font:500 12px/1.45 var(--sans);color:var(--dim)}
.card__title{margin:0;font-family:var(--sans);font-size:20px;line-height:1.32;font-weight:600;
  letter-spacing:-.01em;color:var(--bone);transition:color .35s var(--ease)}
.card:hover .card__title{color:var(--brass-hi)}
.card__text{margin:0;font-size:14px;line-height:1.6;color:var(--dim);flex:1;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card__price{margin:0;font:500 13px/1.4 var(--sans);color:var(--bone-2)}
.card__link{display:inline-flex;align-items:center;gap:8px;min-height:44px;width:max-content;
  font:600 13px/1 var(--sans);letter-spacing:0;color:var(--brass);
  text-decoration:none;border-bottom:1px solid transparent;padding-bottom:3px;
  transition:color .35s var(--ease),border-color .35s var(--ease)}
.card__link:hover{color:var(--brass-hi);border-color:var(--brass-hi);text-decoration:none}
.card--past{opacity:.45;filter:grayscale(.5)}
.card--past .card__meta::after{content:" · уже прошло";color:var(--muted)}
.past{margin:28px 0 0;font-size:13px;color:var(--dim)}
.past .chip{margin-left:10px;font-size:11.5px;padding:6px 13px}
.lead{margin:0;max-width:54ch;color:var(--dim);font-size:16px;line-height:1.6}
.page-title{margin:clamp(36px,5vw,64px) 0 12px;font-size:clamp(42px,6.4vw,88px);
  line-height:.95;letter-spacing:-.02em}
.empty{display:none;padding:64px 0;color:var(--dim);font-family:var(--serif);font-size:24px}
.empty .chip{font-family:var(--sans);font-size:12px;margin-left:10px}

/* ---------------------------------------------------------------- приглашение */
.offer{display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;
  padding:26px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.offer__text{flex:1 1 340px;min-width:0}
.offer h2{margin:0;font-size:clamp(24px,2.4vw,32px);line-height:1.1}
.offer p{margin:6px 0 0;font-size:13px;color:var(--dim)}
.offer a{display:inline-flex;align-items:center;justify-content:center;min-height:46px;padding:0 22px;
  border:1px solid var(--line-2);border-radius:0;color:var(--bone);background:transparent;
  font:600 15px/1 var(--sans);letter-spacing:0;text-decoration:none;
  white-space:nowrap;transition:border-color .4s var(--ease),background .4s var(--ease),color .4s}
.offer a:hover{border-color:var(--brass);background:var(--brass);color:var(--night)}

/* Выходные закончились: объясняем, почему в сетке прошедшее. */
.weekend-over{margin-top:clamp(24px,4vw,40px);padding:20px 24px;border:1px solid var(--brass);
  background:rgba(201,161,95,.05)}
.weekend-over p{margin:0;color:var(--dim);font-size:14px;line-height:1.6}
.weekend-over__title{margin-bottom:4px !important;font-family:var(--serif);font-size:28px;
  line-height:1.1;color:var(--bone)}

/* ---------------------------------------------------------------- подвал */
footer{margin-top:clamp(56px,8vw,110px);border-top:1px solid var(--line);
  padding:clamp(36px,5vw,64px) 0 56px;color:var(--muted);font-size:12px;line-height:1.75}
.footer__mark{margin:0 0 clamp(20px,3vw,32px);font-size:clamp(38px,6vw,78px);line-height:.92;
  color:var(--bone);letter-spacing:-.02em}
.footer__mark em{font-style:italic;color:var(--brass-hi)}
.footer__cols{display:flex;justify-content:space-between;align-items:flex-start;gap:36px;
  padding-top:22px;border-top:1px solid var(--line)}
.footer__legal{max-width:72ch;margin:0}
.footer__legal a{color:var(--bone-2);text-decoration:none;border-bottom:1px solid var(--line-2)}
.footer__legal a:hover{color:var(--bone);border-color:var(--bone)}
.footer__nav{display:flex;flex-direction:column;gap:8px;flex:0 0 auto;align-items:flex-end}
.footer__nav a{color:var(--bone-2);text-decoration:none;font:500 11px/1 var(--sans);
  letter-spacing:.12em;text-transform:uppercase}
.footer__nav a:hover{color:var(--brass-hi)}
.footer__fine{margin:26px 0 0;font:500 9.5px/1.6 var(--sans);letter-spacing:.14em;
  text-transform:uppercase;color:rgba(140,132,120,.7)}

/* ---------------------------------------------------------------- статьи */
.crumbs{margin:26px 0 0;font:500 10.5px/1.5 var(--sans);letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted)}
.crumbs a{color:var(--dim);text-decoration:none}
.crumbs a:hover{color:var(--brass-hi)}
.article{max-width:66ch;margin:clamp(36px,5vw,64px) auto 0}
.article h1{font-size:clamp(38px,5.6vw,68px);line-height:.98;margin:0 0 20px;text-wrap:balance}
.article__meta{margin:0 0 34px;font:500 10.5px/1.5 var(--sans);letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted)}
.article p{font-size:clamp(17px,1.5vw,19.5px);line-height:1.72;margin:0 0 18px;color:var(--bone-2)}
.article h2{font-size:clamp(26px,3vw,36px);line-height:1.1;margin:44px 0 14px;color:var(--bone)}
.article h3{font-size:21px;margin:32px 0 10px;color:var(--bone)}
.article ul,.article ol{margin:0 0 20px;padding-left:24px;font-size:clamp(17px,1.5vw,19.5px);
  line-height:1.72;color:var(--bone-2)}
.article li{margin:0 0 10px}
.article li::marker{color:var(--brass)}
.article a{color:var(--brass-hi);text-decoration-color:rgba(230,197,140,.4);text-underline-offset:3px}
.article__live{margin:42px 0 10px;padding:26px 28px;border:1px solid var(--line);
  background:linear-gradient(160deg,#151917,#0E1110)}
.article__live h2{font-size:24px;margin:0 0 14px}
.article__live ul{margin:0 0 12px;font-size:15px;line-height:1.55}
.article__live a{font-weight:600}
.article__related{margin:44px 0 0;padding-top:24px;border-top:1px solid var(--line)}
.article__related h2{font-size:22px;margin:0 0 12px}
.article__related ul{list-style:none;padding:0;font-size:16px}
.article__related a{text-decoration:none}
.statyi{list-style:none;padding:0;margin:clamp(30px,4vw,48px) 0 0;max-width:72ch;counter-reset:st}
.statyi li{counter-increment:st;padding:26px 0;border-bottom:1px solid var(--line)}
.statyi li::before{content:counter(st,decimal-leading-zero);display:block;margin-bottom:10px;
  font:500 10px/1 var(--sans);letter-spacing:.2em;color:var(--brass)}
.statyi a{font-family:var(--serif);font-size:clamp(24px,3vw,34px);line-height:1.1;font-weight:500;
  color:var(--bone);text-decoration:none}
.statyi a:hover{color:var(--brass-hi)}
.statyi p{margin:8px 0 0;font-size:14px;color:var(--dim)}
.page--article .hero{display:none}

/* ---------------------------------------------------------------- адаптив */
@media (max-width:1000px){
  .hero{min-height:min(84vh,720px);min-height:min(84svh,720px)}
  .hero__caption{display:none}
  .card__title{font-size:22px}}
@media (max-width:900px){
  .nav-drop,.top__nav > a[href="/#catalog"]{display:none}
}
@media (max-width:760px){
  body{font-size:16px}
  /* Зерно с blend-режимом и анимацией на телефоне дороже, чем красивее. */
  .grain{mix-blend-mode:normal;animation:none;opacity:.04}
  .hero{min-height:min(84vh,680px);min-height:min(84svh,680px);padding-top:40px}
  .hero--photo::after{inset:8px}
  .hero__stats{gap:22px 30px;padding-top:16px}
  .hero__stats div{flex:1 1 auto}
  .catalog-head{gap:10px;align-items:flex-start;flex-direction:column}
  .catalog-head p{margin:0}
  .footer__cols{flex-direction:column;gap:20px}
  .footer__nav{align-items:flex-start;flex-direction:row;gap:18px}
  .article{padding:0}
}
@media (max-width:640px){
  header.top{padding:14px 0 12px;gap:12px}
  .brand__name{font-size:17px}
  .top__nav{gap:14px}
  .weekend{font-size:9.5px;letter-spacing:.1em}
  .hero{min-height:min(82vh,620px);min-height:min(82svh,620px)}
  .hero__title{font-size:clamp(40px,12.5vw,60px)}
  .hero__lead{font-size:14.5px;line-height:1.5}
  .hero__actions{gap:8px;margin-top:22px}
  .btn{min-height:48px;padding:0 18px;font-size:11.5px;flex:1 1 auto}
  .hero__stats{margin-top:26px;gap:18px}
  .stat__num{font-size:30px}
  .hero__stats dt{font-size:9px;letter-spacing:.14em}
  /* Третья метка (дата) уходит на свою строку: на узком экране тире
     от переноса оказывалось в начале строки. */
  .eyebrow span:nth-child(3){flex-basis:100%}
  .eyebrow span:nth-child(3)::before{display:none}
  .controls{margin-inline:calc(-1 * var(--pad))}
  .grid{grid-template-columns:1fr;gap:30px;margin-top:24px}
  .card__media{aspect-ratio:4/3}
  .card__title{font-size:23px}
  .card__text{font-size:14px;-webkit-line-clamp:4}
  .card__link{justify-content:center;width:100%;min-height:48px;margin-top:4px;
    border:1px solid var(--line-2);border-radius:0;font-size:13px}
  .card__link:hover{border-color:var(--brass)}
  .offer{padding:20px 0;gap:14px}
  .offer p{display:none}
  .offer a{width:100%}
  .controls__row--top{gap:6px}
  .controls__row--top .chip{display:inline-flex;align-items:center;min-height:42px;padding:0 13px;
    font-size:11.5px}
  .count{font-size:11px;min-width:0;overflow:hidden;text-overflow:ellipsis}
  .filters-toggle{display:inline-flex}
  .controls[data-sunday="true"] .chip.chip--top{display:inline-flex}
  .controls[data-sunday="true"] .controls__row--top{gap:4px}
  .controls[data-sunday="true"] .controls__row--top .chip{padding:0 10px;font-size:10.5px}
  .controls[data-open="false"] .controls__row--more{display:none}
  .controls[data-open="false"][data-active="true"] .controls__row--more{display:flex}
  .controls[data-open="false"] .controls__row--more > *{display:none}
  .controls[data-open="false"] .controls__row--more > .chip[aria-current],
  .controls[data-open="false"] .controls__row--more > [data-reset]{display:inline-flex}
  .controls[data-open="false"] .controls__row--more > select.is-set{display:inline-block}
  .controls__row select{flex:1 1 auto;min-width:0;min-height:42px}
  .empty{font-size:21px;padding:48px 0}
}
@media (max-width:380px){
  .brand__mark{width:26px;height:26px}
  .brand__name{font-size:15.5px}
  .top__nav .weekend{display:none}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms !important;animation-iteration-count:1 !important;
    transition-duration:.001ms !important}
  .hero__img{transform:none}
  .grain{animation:none}
}

/* ---------------------------------------------------------------- движение */
@media (prefers-reduced-motion:no-preference){
  .hero__title i{transform:translateY(110%);animation:rise 1.2s var(--ease) .15s forwards}
  .hero__title span:nth-child(2) i{animation-delay:.3s}
  .hero .eyebrow,.hero__lead,.hero__actions,.hero__stats,.hero__caption{
    opacity:0;animation:fadeUp 1s var(--ease) forwards}
  .hero .eyebrow{animation-delay:.42s}
  .hero__lead{animation-delay:.56s}
  .hero__actions{animation-delay:.7s}
  .hero__stats{animation-delay:.84s}
  .hero__caption{animation-delay:1.05s}
}
@keyframes rise{to{transform:translateY(0)}}
@keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
html.js [data-reveal]{opacity:0;transform:translateY(18px)}
html.js [data-reveal].is-in{opacity:1;transform:none;
  transition:opacity .9s var(--ease) var(--d,0s),transform 1s var(--ease) var(--d,0s)}
/* Если app.js не загрузился, карточки не должны остаться невидимыми. */
html.js.reveal-off [data-reveal]{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){
  html.js [data-reveal]{opacity:1;transform:none}
}
.hero__media{inset:-10% 0 0 0;will-change:transform}
"""

JS = """
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const gridEl = document.querySelector('.grid');
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const dayChips = [...document.querySelectorAll('.chip[data-day]')];
// Два списка мест: районы города и населённые пункты области (он же «За городом»).
// В каждом можно отметить несколько значений; пустой список значит «не фильтровать».
// Префиксы «d:» и «g:» остались в старых ссылках, их снимаем.
const districtPicker = document.querySelector('[data-picker="district"]');
const areaPicker = document.querySelector('[data-picker="area"]');
function plain(value){
  const raw = value || '';
  const cut = raw.indexOf(':');
  return cut > 0 ? raw.slice(cut + 1) : raw;
}
function pickerValues(picker){
  if (!picker) return [];
  return [...picker.querySelectorAll('input[type="checkbox"]')]
    .filter(input => input.checked)
    .map(input => input.value);
}
// Подпись кнопки: пусто — название по умолчанию, одно значение — его имя,
// несколько — счётчик, иначе на телефоне не видно, что фильтр включён.
function updatePickerLabel(picker){
  if (!picker) return;
  const label = picker.querySelector('[data-picker-label]');
  const values = pickerValues(picker);
  if (label){
    label.textContent = values.length === 0 ? label.dataset.default
      : values.length === 1 ? (values[0] === 'city' ? 'Весь Новосибирск' : values[0])
      : 'Выбрано: ' + values.length;
  }
  picker.classList.toggle('is-set', values.length > 0);
}
[...document.querySelectorAll('.picker')].forEach(picker => {
  const label = picker.querySelector('[data-picker-label]');
  if (label) label.dataset.default = label.textContent;
  updatePickerLabel(picker);
});
const pastNote = document.querySelector('[data-past]');
const pastToggle = document.querySelector('[data-past-toggle]');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');
const controlsEl = document.querySelector('.controls');
const toggleBtn = document.querySelector('.filters-toggle');
// Если сборка знает, что выходные уже закончились, прошедшее показываем
// сразу — пустая сетка в воскресенье вечером выглядит как поломка.
let showPast = Boolean(document.querySelector('[data-show-past="true"]'));

// Действия для счётчика: GoatCounter не ставит cookie и не хранит персональные
// данные, поэтому согласия посетителя не спрашиваем. Если скрипт не загрузился
// (блокировщик), track() просто ничего не делает.
function track(action, label){
  if (!window.goatcounter || !window.goatcounter.count) return;
  window.goatcounter.count({
    path: label ? action + '/' + label : action,
    title: action,
    event: true,
  });
}

// активные фильтры: из URL (?free=1) или из заранее нажатых чипов на лендинге
const active = new Set();
chips.forEach(chip => {
  const key = chip.dataset.filter;
  if (params.get(key) === '1' || chip.getAttribute('aria-current') === 'page'
      || chip.getAttribute('aria-current') === 'true') {
    active.add(key);
    chip.setAttribute('aria-current', 'true');
  }
});

// день выходных: пусто — оба дня, иначе 'sat' или 'sun'
let day = ['sat', 'sun'].includes(params.get('day')) ? params.get('day') : '';

// В воскресенье субботние события уже прошли: кнопку «Суббота» убираем и
// показываем только воскресенье. День считаем по Новосибирску, а не по часам
// посетителя: страница собрана одна для всех, и часовой пояс у людей разный.
function isSundayInNovosibirsk(){
  try {
    const weekday = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Novosibirsk',
      weekday: 'short',
    }).format(new Date());
    return weekday === 'Sun';
  } catch (e) {
    return false;
  }
}
if (isSundayInNovosibirsk()){
  const satChip = dayChips.find(chip => chip.dataset.day === 'sat');
  if (satChip) satChip.hidden = true;
  // В воскресенье субботние события уже прошли: показываем воскресенье.
  if (!day) day = 'sun';
  if (controlsEl) controlsEl.dataset.sunday = 'true';
}
function syncDayChips(){
  dayChips.forEach(c => c.setAttribute('aria-pressed', String(c.dataset.day === day)));
}
syncDayChips();

// Состояние из адреса: список через запятую, старые одиночные значения тоже читаются.
function restorePicker(picker, param){
  if (!picker) return;
  const raw = params.get(param) || '';
  if (!raw) return;
  const wanted = raw.split(',').map(v => plain(v.trim())).filter(Boolean);
  picker.querySelectorAll('input[type="checkbox"]').forEach(input => {
    if (wanted.includes(input.value)) input.checked = true;
  });
  updatePickerLabel(picker);
}
restorePicker(districtPicker, 'district');
restorePicker(areaPicker, 'geo');

function plural(n){
  const m = n % 100, d = n % 10;
  if (m > 10 && m < 20) return 'событий';
  if (d === 1) return 'событие';
  if (d >= 2 && d <= 4) return 'события';
  return 'событий';
}

function syncUrl(){
  ['free','child','dog','nature','outside'].forEach(k => {
    if (active.has(k)) params.set(k, '1'); else params.delete(k);
  });
  if (day) params.set('day', day); else params.delete('day');
  const districts = pickerValues(districtPicker);
  if (districts.length) params.set('district', districts.join(',')); else params.delete('district');
  const areas = pickerValues(areaPicker);
  if (areas.length) params.set('geo', areas.join(',')); else params.delete('geo');
  const q = params.toString();
  const next = q ? '?' + q : location.pathname;
  // apply() вызывается ещё и по таймеру: без сравнения адрес переписывался бы
  // каждую минуту вхолостую.
  if (next !== location.pathname + location.search) history.replaceState(null, '', next);
}

// Состояние шторки: если ни один фильтр не включён, второй строки нет вовсе —
// на телефоне остаются только дни, счётчик и кнопка. Если включён, под ней
// видны только включённые фильтры и «Сбросить», иначе непонятно, почему
// выдача короче полной.
function syncFilters(){
  if (!controlsEl) return;
  const place = pickerValues(districtPicker).length > 0 || pickerValues(areaPicker).length > 0;
  controlsEl.dataset.active = String(active.size > 0 || place);
  [districtPicker, areaPicker].forEach(updatePickerLabel);
}

// Прошедшее событие определяем по часам посетителя: сервер собрал страницу
// утром, а смотрят её вечером, и субботний концерт в воскресенье уже неактуален.
function isPast(card){
  const end = card.dataset.end;
  return Boolean(end) && new Date(end).getTime() < Date.now();
}

function updatePast(past){
  if (!pastNote || !pastToggle) return;
  pastNote.hidden = !past;
  const label = pastNote.querySelector('[data-past-label]');
  if (label) label.textContent = showPast ? 'Прошедшие события показаны.' : 'Прошедшие события скрыты.';
  pastToggle.textContent = (showPast ? 'Скрыть' : 'Показать') + ' (' + past + ')';
  pastToggle.setAttribute('aria-pressed', String(showPast));
}

// Если страница открыта с вечера пятницы до вечера воскресенья, в какой-то
// момент все события становятся прошедшими. Не оставляем пустую сетку.
let weekendStateDone = document.body.dataset.weekendOver === 'true';
function ensureWeekendState(){
  if (weekendStateDone || !cards.length) return;
  if (!cards.every(card => isPast(card))) return;
  weekendStateDone = true;
  showPast = true;
  const main = document.querySelector('main');
  if (main) main.dataset.showPast = 'true';
  const catalog = document.querySelector('.catalog');
  if (catalog && !document.querySelector('.weekend-over')){
    const box = document.createElement('div');
    box.className = 'weekend-over';
    box.setAttribute('role', 'status');
    box.innerHTML = '<p class="weekend-over__title">Эти выходные закончились.</p>'
      + '<p>Следующий выпуск — в пятницу. Пока показываем прошедшее.</p>';
    catalog.before(box);
  }
  const cta = document.querySelector('.hero__actions .btn');
  if (cta) cta.innerHTML = 'Показать прошедшее '
    + '<span class="btn__arrow" aria-hidden="true">→</span>';
}

function apply(animate){
  const districts = pickerValues(districtPicker);
  const areas = pickerValues(areaPicker);
  const cityWide = districts.includes('city');
  const districtNames = districts.filter(v => v !== 'city');
  let shown = 0, past = 0;
  const visibleCards = [];
  for (const card of cards){
    const okFlags = [...active].every(k => card.dataset[k] === 'true');
    const inCity = (card.dataset.geo || 'Новосибирск') === 'Новосибирск';
    const okDistrict = !districts.length
      || (cityWide && inCity)
      || districtNames.includes(card.dataset.district || '');
    const okArea = !areas.length || areas.includes(card.dataset.geo || '');
    const okZone = okDistrict && okArea;
    const okDay = !day || (card.dataset.days || '').split(' ').includes(day);
    const matches = okFlags && okZone && okDay;
    const gone = matches && isPast(card);
    if (gone) past++;
    card.classList.toggle('card--past', gone);
    const visible = matches && (!gone || showPast);
    card.hidden = !visible;
    if (visible) { shown++; visibleCards.push(card); }
  }

  // Порядок задаёт сервер: он уже отсортирован по типу, дате и времени.
  // Клиентская перестановка на загрузке меняла первые карточки местами,
  // а раз в минуту перестраивала всю сетку и могла уронить фокус.
  if (gridEl && animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      && typeof Element.prototype.animate === 'function'){
    visibleCards.slice(0, 28).forEach((card, i) => {
      card.animate(
        [{opacity: 0, transform: 'translateY(14px)'}, {opacity: 1, transform: 'none'}],
        {duration: 520, delay: Math.min(i, 10) * 35,
         easing: 'cubic-bezier(.16,1,.3,1)', fill: 'both'}
      );
    });
  }

  if (counter) {
    counter.textContent = shown + ' ' + plural(shown);
  }
  if (empty) empty.style.display = shown || past ? 'none' : 'block';
  updatePast(past);
  syncFilters();
  syncUrl();
}

chips.forEach(chip => chip.addEventListener('click', (e) => {
  // Чипы — ссылки на страницы подборок: без JS они ведут на /besplatno/ и т.д.,
  // с JS фильтруют на месте, не перезагружая страницу.
  e.preventDefault();
  const key = chip.dataset.filter;
  if (active.has(key)){
    active.delete(key);
  } else {
    active.add(key);
  }
  // Одна метка может стоять дважды: в верхней строке на телефоне и в шторке.
  // Подсветку переносим на все копии, иначе непонятно, включён фильтр или нет.
  chips.filter(c => c.dataset.filter === key).forEach(c => {
    if (active.has(key)) c.setAttribute('aria-current', 'true');
    else c.removeAttribute('aria-current');
  });
  apply(true);
  track('filter', key);
}));

dayChips.forEach(chip => chip.addEventListener('click', () => {
  day = day === chip.dataset.day ? '' : chip.dataset.day;
  syncDayChips();
  apply(true);
  track('day', day || 'both');
}));

// Выбор места: чекбоксы в раскрывающемся списке. Район города и населённый
// пункт области вместе дают пустую выдачу, поэтому один список сбрасывает другой.
function clearPicker(picker){
  if (!picker) return;
  picker.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = false; });
  updatePickerLabel(picker);
}
[[districtPicker, areaPicker, 'district'], [areaPicker, districtPicker, 'place']]
  .forEach(([picker, other, key]) => {
    if (!picker) return;
    picker.addEventListener('change', (e) => {
      if (!e.target.matches('input[type="checkbox"]')) return;
      if (pickerValues(picker).length) clearPicker(other);
      updatePickerLabel(picker);
      apply(true);
      track(key, pickerValues(picker).join(',') || 'all');
    });
  });

document.querySelectorAll('[data-picker-reset]').forEach(btn => {
  btn.addEventListener('click', () => {
    clearPicker(btn.closest('.picker'));
    apply(true);
  });
});

document.addEventListener('click', (e) => {
  document.querySelectorAll('details[open]').forEach(picker => {
    if (!picker.contains(e.target)) picker.open = false;
  });
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('details[open]').forEach(picker => { picker.open = false; });
  }
});

if (controlsEl && toggleBtn){
  toggleBtn.addEventListener('click', () => {
    const open = controlsEl.dataset.open === 'true';
    controlsEl.dataset.open = String(!open);
    toggleBtn.setAttribute('aria-expanded', String(!open));
    toggleBtn.setAttribute('aria-label', !open ? 'Свернуть фильтры' : 'Показать фильтры');
    track('filters', !open ? 'open' : 'close');
  });
}

if (pastToggle) {
  pastToggle.addEventListener('click', () => {
    showPast = !showPast;
    apply(true);
    track('past', showPast ? 'show' : 'hide');
  });
}

// Клик по афише и по «Билеты и подробности» — главный признак того, что
// событие заинтересовало и человек ушёл к организатору.
document.querySelectorAll('.card__link').forEach(link => {
  link.addEventListener('click', () => track('click-card'));
});
document.querySelectorAll('.card__media').forEach(link => {
  link.addEventListener('click', () => track('click-poster'));
});
document.querySelectorAll('[data-track]').forEach(link => {
  link.addEventListener('click', () => track(link.dataset.track));
});

// Статьи: видно, ведёт ли текст людей в каталог или сразу к организатору.
const article = document.querySelector('.article');
if (article) {
  const parts = location.pathname.split('/').filter(Boolean);
  const slug = parts[parts.length - 1] || 'article';
  article.querySelectorAll('a[href]').forEach(link => {
    link.addEventListener('click', () => {
      const href = link.getAttribute('href') || '';
      track(href.startsWith('/') ? 'article-to-site' : 'article-to-source', slug);
    });
  });
}

document.querySelectorAll('[data-reset]').forEach(btn => btn.addEventListener('click', () => {
  active.clear();
  // В воскресенье «оба дня» уже не имеет смысла: суббота прошла.
  day = controlsEl && controlsEl.dataset.sunday === 'true' ? 'sun' : '';
  showPast = false;
  chips.forEach(chip => chip.removeAttribute('aria-current'));
  syncDayChips();
  [districtPicker, areaPicker].forEach(clearPicker);
  apply(true);
}));

ensureWeekendState();
apply();
// Время идёт: страница, открытая весь вечер, должна сама убрать событие,
// которое только что закончилось. Переставлять карточки не нужно — только
// пересчитать видимость, иначе фокус и позиция скролла теряются.
setInterval(() => { ensureWeekendState(); apply(false); }, 300000);
// ------------------------------------------------------------------ движение
// Всё необязательное: без JS и при reduced motion страница остаётся целой.
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hero = document.querySelector('.hero');
  const heroMedia = document.querySelector('.hero__media');
  const bar = document.querySelector('.progress span');
  const revealItems = [...document.querySelectorAll('[data-reveal]')];

  if (revealItems.length){
    if (reduce || !('IntersectionObserver' in window)){
      revealItems.forEach(el => el.classList.add('is-in'));
    } else {
      const io = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-in');
          io.unobserve(entry.target);
        });
      }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });
      revealItems.forEach(el => io.observe(el));
    }
  }
  // Сигнал страховке в <head>: скрипт дошёл до появления карточек.
  window.__revealReady = true;

  let queued = false;
  function frame(){
    const y = window.scrollY || 0;
    if (bar){
      const max = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.transform = 'scaleX(' + (max > 0 ? Math.min(1, y / max) : 0) + ')';
    }
    if (hero && heroMedia && !reduce && window.innerWidth > 760){
      if (y < hero.offsetHeight + 160){
        heroMedia.style.transform = 'translate3d(0,' + (y * 0.09).toFixed(1) + 'px,0)';
      }
    }
    queued = false;
  }
  function onScroll(){
    if (!queued){ queued = true; requestAnimationFrame(frame); }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  frame();

  if (hero && !reduce && window.matchMedia('(pointer: fine)').matches){
    hero.addEventListener('pointermove', (event) => {
      const rect = hero.getBoundingClientRect();
      hero.style.setProperty('--mx', ((event.clientX - rect.left) / rect.width * 100).toFixed(1) + '%');
      hero.style.setProperty('--my', ((event.clientY - rect.top) / rect.height * 100).toFixed(1) + '%');
    }, { passive: true });
  }
})();
"""

PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
<rect width="640" height="480" fill="#E8E2D6"/>
<text x="320" y="250" font-family="Segoe UI, sans-serif" font-size="26" fill="#1F5E4B" text-anchor="middle">Планы на выходные</text>
</svg>
"""

# Заглушка до build(): тот пересчитает выходные от today с учётом --date.
# Дата машины здесь не годится: на сервере она может быть в UTC.
CURRENT_WEEKEND = weekend_of(now_nsk().date())

# Статьи загружаются в build(); здесь — заглушка, чтобы page_html и подвал
# могли проверить наличие раздела до первой сборки.
ARTICLES: list = []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сборка статического сайта из таблицы событий")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="файл с событиями")
    parser.add_argument("--sheet", action="store_true",
                        help="читать события напрямую из Google-таблицы")
    parser.add_argument("--sheet-name", default="nsk_weekend", help="имя Google-таблицы")
    parser.add_argument("--sheet-tab", default=None, help="лист (по умолчанию events)")
    parser.add_argument("--creds", default="credentials.json", help="ключ сервисного аккаунта")
    parser.add_argument("--save-cache", default="", help="сохранить снимок таблицы в CSV")
    parser.add_argument("--out", default="dist", help="папка результата")
    parser.add_argument("--date", help="считать эту дату сегодняшней (YYYY-MM-DD)")
    parser.add_argument("--now", help="считать это время текущим (ISO, для проверки "
                                      "состояния «выходные закончились»)")
    parser.add_argument("--base", default="",
                        help="подпапка для GitHub Pages, например /plany-na-vyhodnye")
    parser.add_argument("--site-url", default="",
                        help="публичный адрес сайта для canonical и sitemap "
                             "(по умолчанию SITE_URL)")
    args = parser.parse_args(argv)

    global SITE_URL, NOW_OVERRIDE
    if args.site_url:
        SITE_URL = args.site_url.rstrip("/")
    if args.now:
        NOW_OVERRIDE = datetime.fromisoformat(args.now)

    today = date.fromisoformat(args.date) if args.date else now_nsk().date()
    print(f"[i] Сборка на {today:%d.%m.%Y}, выходные {weekend_of(today)[0]:%d.%m}–{weekend_of(today)[1]:%d.%m}")

    rows: list[dict] | None = None

    if args.sheet:
        try:
            raw = sheets_store.read_table(
                name=args.sheet_name, creds=args.creds, tab=args.sheet_tab
            )
            if not raw:
                raise RuntimeError("лист пуст")
            print(f"[+] Google Sheets: «{args.sheet_name}», строк {len(raw)}")
            rows = raw
            if args.save_cache:
                save_rows_cache(raw, Path(args.save_cache))
        except Exception as e:  # noqa: BLE001
            print(f"[!] Google Sheets недоступен ({type(e).__name__}: {e}). Беру CSV.")

    if rows is None:
        csv_path = Path(args.csv)
        if not csv_path.exists() and FALLBACK_CSV.exists():
            print(f"[i] {csv_path} не найден, беру {FALLBACK_CSV}")
            csv_path = FALLBACK_CSV
        if not csv_path.exists():
            raise SystemExit(f"Нет ни таблицы, ни {csv_path}. Сначала запустите парсер.")
        with csv_path.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        print(f"[i] Данные из {csv_path}: строк {len(rows)}")

    closures = ClosureStore.open(args.sheet_name, args.creds)
    if closures.days:
        print(f"[i] Закрытия площадок: {closures.summary()}")
    build(rows, Path(args.out), today, base=args.base, closures=closures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
