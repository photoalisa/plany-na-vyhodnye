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
    """Сырые строки таблицы → события, готовые к выводу."""
    return [norm_event(r) for r in rows if (r.get("status") or "active") == "active"]


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
    return ev["start"] <= sun and end >= max(sat, date.today())


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
        return f"{first.isoformat()}T00:00:00", f"{last.isoformat()}T23:59:00"

    sh, sm = int(times[0][0]), int(times[0][1])
    eh, em = int(times[-1][0]), int(times[-1][1])
    # одно время начала — считаем, что событие идёт два часа: спектакль или
    # лекция короче не бывают, а при ошибке карточка просто провисит лишний час.
    minutes = eh * 60 + em + (120 if len(times) == 1 else 0)
    end_day = last
    if minutes >= 24 * 60:
        end_day, minutes = last + timedelta(days=1), minutes % (24 * 60)
    return (f"{first.isoformat()}T{sh:02d}:{sm:02d}:00",
            f"{end_day.isoformat()}T{minutes // 60:02d}:{minutes % 60:02d}:00")


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
    if ev["kind"] == "permanent":
        return "постоянно"
    if not ev["start"]:
        return "дата уточняется"
    if ev["kind"] == "recurring":
        return "каждую неделю" + (f", {ev['time']}" if ev["time"] else "")

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
    if ev["venue"] and len(ev["venue"]) < 44:
        meta.append(ev["venue"])
    attrs = " ".join(f'data-{k}="{str(v).lower()}"' for k, v in ev["flags"].items())
    day_attr = f' data-days="{" ".join(ev.get("days") or [])}"' if ev.get("days") else ""
    # data-end — конец события по часам посетителя: по нему браузер скрывает
    # прошедшее, чтобы в воскресенье вечером не висели субботние концерты.
    end_attr = f' data-end="{ev["ends_at"]}"' if ev.get("ends_at") else ""
    start_attr = f' data-start="{ev["starts_at"]}"' if ev.get("starts_at") else ""
    price = ev["price"] or ("Бесплатно" if ev["flags"]["free"] else "Цена на сайте")
    # Возрастная маркировка (436-ФЗ) — рядом с ценой, чтобы была видна сразу.
    if ev.get("age"):
        price = f"{price} · {ev['age']}"
    # Участники теста не понимали, что ссылка уводит на чужой сайт: подписываем
    # переход словами и значком внешней ссылки.
    # Ссылка ведёт не всегда на сайт организатора: это может быть группа во
    # «ВКонтакте», билетный сервис или другой агрегатор. Поэтому говорим
    # «страница источника», не обещая, кто именно её ведёт.
    link_label = ("Подробнее на странице источника "
                  "<span aria-hidden=\"true\">↗</span>" if ev["flags"]["free"]
                  else "Билеты на странице источника <span aria-hidden=\"true\">↗</span>")
    open_hint = f"Открыть страницу источника: {ev['title']}"
    return f"""
<article class="card" {attrs}{day_attr}{end_attr}{start_attr} data-district="{esc(ev['district'])}" data-geo="{esc(ev['area'])}">
  <a class="card__media" href="{esc(ev['url'])}" target="_blank" rel="noopener nofollow" title="{esc(open_hint)}">
    {image_html}
  </a>
  <div class="card__body">
    <p class="card__meta">{esc(' · '.join(meta))}</p>
    <h3 class="card__title">{esc(ev['title'])}</h3>
    <p class="card__text">{esc(cut_text(ev['text']))}</p>
    <p class="card__price">{esc(price)}</p>
    <a class="card__link" href="{esc(ev['url'])}" target="_blank" rel="noopener nofollow">{link_label}</a>
    {report_link()}
  </div>
</article>"""


def chips_html(active: str = "", events: list[dict] | None = None) -> str:
    """Фильтры — настоящие ссылки: по ним ходят поисковики, а JS гасит переход.

    Кнопкой их делать нельзя: страницы подборок (/besplatno/ и остальные)
    тогда ниоткуда не linked, и в индексе их не будет.

    Пустые подборки на главной не показываем: в ноябре «С животными» — это
    гарантированно пустая страница, и кликать по ней незачем. Сама страница
    остаётся (ссылки не ломаются) и закрыта от индексации.
    """
    buttons = []
    for key, label, slug, *_ in FILTERS:
        if events is not None and key != active:
            if not any(ev["flags"].get(key) for ev in events):
                continue
        extra = " chip--free" if key == "free" else ""
        current = ' aria-current="page"' if key == active else ""
        buttons.append(
            f'<a class="chip{extra}" href="/{slug}/" data-filter="{key}"{current}>{label}</a>'
        )
    return "".join(buttons)


def day_chips_html() -> str:
    return (
        '<button class="chip" data-day="sat" aria-pressed="false">Суббота</button>'
        '<button class="chip" data-day="sun" aria-pressed="false">Воскресенье</button>'
    )


def area_names(events: list[dict]) -> list[str]:
    """Населённые пункты области: сам Новосибирск сюда не входит."""
    return sorted({e["area"] for e in events
                   if e["area"] and e["area"] != "Новосибирск"})


def zone_select_html(events: list[dict], geo: bool = False) -> str:
    """Один список мест: районы города и населённые пункты области.

    На каталоге нужны районы, но человек, который выбрал «За городом», ищет
    Сузун и Искитим — поэтому в списке две группы, а значение опции помечено
    префиксом: «d:Центральный» — район, «g:Искитим» — город области.
    На странице «За городом» остаются только пункты области.
    """
    districts = sorted({e["district"] for e in events if e["district"]})
    areas = area_names(events)

    if geo:
        if len(areas) < 2:
            return ""
        options = "".join(f'<option value="g:{esc(n)}">{esc(n)}</option>' for n in areas)
        return ('<select id="district" data-zone="place" aria-label="Населённый пункт">'
                '<option value="">Вся область</option>' + options + "</select>")

    groups = []
    if districts:
        options = "".join(f'<option value="d:{esc(d)}">{esc(d)}</option>' for d in districts)
        groups.append(f'<optgroup label="Районы города">{options}</optgroup>')
    if areas:
        options = "".join(f'<option value="g:{esc(n)}">{esc(n)}</option>' for n in areas)
        groups.append(f'<optgroup label="Область">{options}</optgroup>')
    if not groups:
        return ""
    return ('<select id="district" data-zone="place" '
            'aria-label="Район города или населённый пункт области">'
            '<option value="">Все места</option>' + "".join(groups) + "</select>")


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
        if ev["start"]:
            item["startDate"] = ev["start"].isoformat() + (f"T{ev['time']}" if ev["time"] else "")
        if ev["end"]:
            item["endDate"] = ev["end"].isoformat()
        if ev["text"]:
            item["description"] = cut_text(ev["text"], 240)
        if ev["venue"] or ev["address"]:
            item["location"] = {
                "@type": "Place",
                "name": ev["venue"] or ev["address"],
                "address": {"@type": "PostalAddress", "addressLocality": ev["area"],
                            "streetAddress": ev["address"]},
            }
        else:
            item["location"] = {
                "@type": "Place",
                "name": ev["area"] or "Новосибирск",
                "address": {"@type": "PostalAddress", "addressLocality": "Новосибирск"},
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
    """Кнопка «Сообщить об ошибке» на карточке (однополевая Яндекс Форма)."""
    if not FORM_URL:
        return ""
    return (f'<a class="card__report" href="{esc(FORM_URL)}" '
            f'target="_blank" rel="noopener nofollow">Сообщить об ошибке</a>')


def offer_link() -> str:
    if not FORM_URL:
        return ""
    return f'<a href="{esc(FORM_URL)}" target="_blank" rel="noopener nofollow">Предложить событие</a>'


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
    link = '<a href="/statyi/">Статьи</a> · ' if articles_link else ""
    return f"""<footer>
    {link}Сайт не просит персональные данные и не хранит афиши: изображения
    загружаются по ссылке источника. Карточка ведёт на страницу источника,
    где можно узнать подробности и купить билет. Ответственность за
    актуальность события несёт организатор. Возрастные ограничения — по
    данным организаторов, у остальных событий метки нет.
    <br>Афиши принадлежат организаторам и их правообладателям: по запросу
    удаляем. {offer_link()}{stats_note()}
  </footer>"""


def page_html(*, title: str, description: str, lead: str, events: list[dict],
              all_events: list[dict], path: str, active_filter: str = "",
              h1: str = "", show_filters: bool = True, canonical: str = "",
              noindex: bool = False, geo_select: bool = False,
              release: datetime | None = None) -> str:
    cards = "\n".join(card_html(ev) for ev in events)
    canonical_url = f"{SITE_URL}/{path}".rstrip("/") + ("/" if path else "/")
    canonical_url = canonical or canonical_url

    sr_heading = '  <h2 class="sr-only">События</h2>\n' if events else ""
    release_html = ""
    if release is not None:
        release_html = (
            f'  <p class="release"><time datetime="{release.date().isoformat()}">'
            f"{esc(release_label(release))}</time></p>\n"
        )

    controls = ""
    if show_filters:
        controls = (
            '<div class="controls">'
            + day_chips_html()
            + '<span class="controls__divider" aria-hidden="true"></span>'
            + chips_html(active_filter, all_events)
            + zone_select_html(all_events, geo_select)
            + '<button class="chip" data-reset>Сбросить</button>'
            + f'<span class="count" role="status" aria-live="polite" aria-atomic="true">'
              f'<span data-count>{len(events)}</span></span>'
            + "</div>"
        )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
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
<meta property="og:locale" content="ru_RU">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#FBFAF7">
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/placeholder.svg" type="image/svg+xml">
<script type="application/ld+json">{json_ld(events, title)}</script>
</head>
<body>
<a class="skip-link" href="#events">Перейти к содержимому</a>
<div class="wrap">
  <header class="top">
    <a class="brand" href="/">{esc(SITE_NAME)}</a>
    <span class="weekend">{esc(weekend_label())}</span>
  </header>

  <main id="events">
  <h1>{esc(h1 or title)}</h1>
  <p class="lead">{esc(lead)}</p>
{release_html}
  {controls}

{sr_heading}  <div class="grid">{cards}</div>

  <p class="past" data-past hidden>
    <span data-past-label>Прошедшие события скрыты.</span>
    <button class="chip" data-past-toggle aria-pressed="false"></button>
  </p>

  <p class="empty">По этим фильтрам на выходные ничего нет.
    <button class="chip" data-reset>Сбросить фильтры</button>
  </p>
  </main>

  {footer_html(articles_link=bool(ARTICLES))}
</div>
<script src="/assets/app.js?v={BUILD_VERSION}"></script>
{counter_html()}
</body>
</html>"""


def weekend_label() -> str:
    sat, sun = CURRENT_WEEKEND
    if sat.month == sun.month:
        return f"{sat.day}–{sun.day} {MONTHS[sun.month - 1]}"
    return f"{sat.day} {MONTHS[sat.month - 1]} – {sun.day} {MONTHS[sun.month - 1]}"


def now_nsk() -> datetime:
    """Текущее время в Новосибирске.

    В Windows нет системной базы часовых поясов, поэтому zoneinfo может
    упасть; тогда берём фиксированный сдвиг UTC+7 — летнего времени в
    Новосибирске нет с 2014 года, разница круглый год одна.
    """
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
    subset = [ev for ev in events if not key or ev["flags"].get(key)][:4]
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
<meta name="theme-color" content="#FBFAF7">
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/placeholder.svg" type="image/svg+xml">
<script type="application/ld+json">{article_json_ld(article)}</script>
</head>
<body>
<a class="skip-link" href="#content">Перейти к тексту</a>
<div class="wrap">
  <header class="top">
    <a class="brand" href="/">{esc(SITE_NAME)}</a>
    <span class="weekend">{esc(weekend_label())}</span>
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статьи о том, как выбрать событие на выходные — Новосибирск</title>
<meta name="description" content="Как выбрать событие на выходные в Новосибирске: разборы форматов, советы по возрасту, цене и дороге.">
<link rel="canonical" href="{SITE_URL}/statyi/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="Статьи — {esc(SITE_NAME)}">
<meta property="og:url" content="{SITE_URL}/statyi/">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta name="theme-color" content="#FBFAF7">
<link rel="stylesheet" href="/assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/placeholder.svg" type="image/svg+xml">
</head>
<body>
<a class="skip-link" href="#content">Перейти к статьям</a>
<div class="wrap">
  <header class="top">
    <a class="brand" href="/">{esc(SITE_NAME)}</a>
    <span class="weekend">{esc(weekend_label())}</span>
  </header>

  <nav class="crumbs"><a href="/">Главная</a> · Статьи</nav>

  <main id="content">
  <h1>Статьи</h1>
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
        for extra in sorted(STATIC_DIR.iterdir()):
            if extra.is_file():
                (out / extra.name).write_bytes(extra.read_bytes())

    def write(rel: str, content: str) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(local(content), encoding="utf-8")

    write("index.html", page_html(
        title=f"Куда пойти на выходных в Новосибирске — {weekend_label()}",
        description="Афиша Новосибирска на субботу и воскресенье: выставки, лекции, концерты "
                    "и прогулки. Адреса, ссылки на организаторов, есть бесплатные и для детей.",
        lead="Выставки, лекции, концерты и прогулки.",
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
:root{--bg:#FBFAF7;--ink:#14110F;--muted:#6B625A;--accent:#1F5E4B;--chip:#E8E2D6;--line:#E3DDD1}
@media (prefers-color-scheme:dark){:root{--bg:#14110F;--ink:#F3EFE7;--muted:#A79E93;--accent:#7FC7A8;--chip:#242019;--line:#2E2822}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif}
.skip-link{position:absolute;left:-9999px;top:0;z-index:100;background:var(--accent);color:#fff;
  padding:10px 18px;border-radius:0 0 10px 0;font-size:14px;font-weight:600;text-decoration:none}
.skip-link:focus{left:0}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.chip:focus-visible,a.chip:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.wrap{max-width:1120px;margin:0 auto;padding:24px 20px 64px}
header.top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;
  border-bottom:1px solid var(--line);padding-bottom:16px}
.brand{font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:14px;color:var(--ink);text-decoration:none}
.weekend{color:var(--muted);font-size:14px}
h1{font-size:28px;line-height:1.2;margin:28px 0 6px}
h2{font-size:20px;margin:32px 0 12px}
.lead{color:var(--muted);margin:0 0 22px;max-width:62ch}
.release{color:var(--muted);font-size:13px;margin:-14px 0 18px}
.release time{font-variant-numeric:tabular-nums}
.controls{position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:5;
  display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--line)}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
  padding:7px 14px;font-size:14px;cursor:pointer;font-family:inherit}
a.chip{display:inline-flex;align-items:center;text-decoration:none}
.chip[aria-pressed="true"],.chip[aria-current="page"]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip--free{font-weight:600}
.chip--free[aria-pressed="true"],.chip--free[aria-current="page"]{background:var(--accent);border-color:var(--accent);color:#fff}
.controls__divider{width:1px;height:22px;background:var(--line);margin:0 4px}
select{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
  padding:7px 12px;font-size:14px;font-family:inherit}
.count{margin-left:auto;color:var(--muted);font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:22px;margin-top:26px}
@keyframes gridFlash{from{opacity:.3}to{opacity:1}}
.grid--flash{animation:gridFlash .3s ease-out}
@media (prefers-reduced-motion: reduce){.grid--flash{animation:none}}
/* атрибут hidden должен побеждать display:flex у .card, иначе фильтры не скрывают карточки */
[hidden]{display:none !important}
.card{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--bg)}
.card__media{display:block;background:var(--chip);height:200px}
/* фиксированная высота 200px, афиша по центру без обрезки */
.card__media img{display:block;width:100%;height:100%;object-fit:contain;object-position:center}
.card__body{padding:14px 16px 18px;display:flex;flex-direction:column;gap:8px;flex:1}
.card__meta{margin:0;font-size:13px;color:var(--accent);font-weight:600}
.card__title{margin:0;font-size:18px;line-height:1.25}
.card__text{margin:0;font-size:15px;color:var(--muted);flex:1}
.card__price{margin:0;font-size:14px;font-weight:600}
.card__link{font-size:14px;color:var(--accent);text-decoration:none}
.card__link:hover{text-decoration:underline}
.card__report{display:inline-block;font-size:13px;color:var(--muted);text-decoration:none;margin-top:2px}
.card__report:hover{text-decoration:underline}
/* прошедшее событие: видно, что оно было, но не путается с актуальным */
.card--past{opacity:.55}
.card--past .card__meta::after{content:" · уже прошло";color:var(--muted)}
.past{margin:20px 0 0;font-size:14px;color:var(--muted)}
.past .chip{margin-left:8px;font-size:13px;padding:5px 12px}
.empty{display:none;padding:40px 0;color:var(--muted)}
footer{margin-top:32px;color:var(--muted);font-size:13px}
@media (max-width:640px){.grid{grid-template-columns:1fr}h1{font-size:24px}}
/* Статьи: узкая колонка для чтения, живой блок событий и перелинковка */
.crumbs{margin:18px 0 0;font-size:14px;color:var(--muted)}
.crumbs a{color:var(--muted)}
.article{max-width:70ch;margin-top:8px}
.article h1{font-size:30px;line-height:1.2;margin:18px 0 8px}
.article__meta{margin:0 0 18px;font-size:14px;color:var(--muted)}
.article h2{font-size:22px;margin:30px 0 10px}
.article h3{font-size:18px;margin:24px 0 8px}
.article p{font-size:17px;line-height:1.6;margin:0 0 14px}
.article ul,.article ol{margin:0 0 16px;padding-left:22px;font-size:17px;line-height:1.6}
.article li{margin:0 0 8px}
.article a{color:var(--accent)}
.article__live{margin:32px 0 8px;padding:18px 20px;border:1px solid var(--line);border-radius:14px;background:var(--chip)}
.article__live h2{font-size:18px;margin:0 0 10px}
.article__live ul{margin:0 0 10px;font-size:15px;line-height:1.45}
.article__live a{font-weight:600}
.article__related{margin:32px 0 0;padding-top:18px;border-top:1px solid var(--line)}
.article__related h2{font-size:18px;margin:0 0 10px}
.article__related ul{list-style:none;padding:0;font-size:16px}
.statyi{list-style:none;padding:0;margin:26px 0 0;max-width:70ch}
.statyi li{padding:16px 0;border-bottom:1px solid var(--line)}
.statyi a{font-size:19px;font-weight:600;color:var(--ink);text-decoration:none}
.statyi a:hover{color:var(--accent)}
.statyi p{margin:6px 0 0;font-size:15px;color:var(--muted)}
"""

JS = """
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const gridEl = document.querySelector('.grid');
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const dayChips = [...document.querySelectorAll('.chip[data-day]')];
// Список площадок: на каталоге это районы города, на «За городом» —
// населённые пункты области. Скрипт один, поэтому ключ берём из data-zone.
const zone = document.querySelector('#district, #geo');
// Значение опции: «d:Центральный» — район города, «g:Искитим» — город
// области. Старые ссылки без префикса считаем районом.
function zoneOf(value){
  const raw = value || '';
  const cut = raw.indexOf(':');
  if (cut > 0){
    const prefix = raw.slice(0, cut);
    if (prefix === 'd' || prefix === 'g'){
      return {field: prefix === 'g' ? 'geo' : 'district', name: raw.slice(cut + 1)};
    }
  }
  return {field: 'district', name: raw};
}
const pastNote = document.querySelector('[data-past]');
const pastToggle = document.querySelector('[data-past-toggle]');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');
let showPast = false;

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
  if (params.get(key) === '1' || chip.getAttribute('aria-current') === 'page') {
    active.add(key);
    chip.setAttribute('aria-current', 'page');
  }
});

// день выходных: пусто — оба дня, иначе 'sat' или 'sun'
let day = ['sat', 'sun'].includes(params.get('day')) ? params.get('day') : '';
function syncDayChips(){
  dayChips.forEach(c => c.setAttribute('aria-pressed', String(c.dataset.day === day)));
}
syncDayChips();

if (zone){
  const wanted = params.get('district') || params.get('geo') || '';
  if (wanted){
    const values = [...zone.options].map(o => o.value);
    const match = [wanted, 'd:' + wanted, 'g:' + wanted].find(v => values.includes(v));
    if (match) zone.value = match;
  }
}

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
  const z = zone ? zone.value : '';
  if (z) params.set('district', z); else params.delete('district');
  const q = params.toString();
  const next = q ? '?' + q : location.pathname;
  // apply() вызывается ещё и по таймеру: без сравнения адрес переписывался бы
  // каждую минуту вхолостую.
  if (next !== location.pathname + location.search) history.replaceState(null, '', next);
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

function apply(animate){
  const z = zone ? zone.value : '';
  const zoneSel = zoneOf(z);
  let shown = 0, past = 0;
  const visibleCards = [];
  for (const card of cards){
    const okFlags = [...active].every(k => card.dataset[k] === 'true');
    const okZone = !z || (card.dataset[zoneSel.field] || '') === zoneSel.name;
    const okDay = !day || (card.dataset.days || '').split(' ').includes(day);
    const matches = okFlags && okZone && okDay;
    const gone = matches && isPast(card);
    if (gone) past++;
    card.classList.toggle('card--past', gone);
    const visible = matches && (!gone || showPast);
    card.hidden = !visible;
    card.style.display = visible ? '' : 'none';
    if (visible) { shown++; visibleCards.push(card); }
  }

  // Участники теста принимали неизменный порядок за сломанные фильтры:
  // первая пара карточек подходила под любой фильтр и оставалась на месте.
  // Пересобираем сетку по времени начала и подсвечиваем, что выдача изменилась.
  if (gridEl){
    visibleCards.sort((a, b) =>
      (a.dataset.start || a.dataset.end || '').localeCompare(b.dataset.start || b.dataset.end || '')
    );
    for (const card of visibleCards) gridEl.appendChild(card);
    for (const card of cards) if (card.hidden) gridEl.appendChild(card);
    if (animate){
      gridEl.classList.remove('grid--flash');
      void gridEl.offsetWidth;
      gridEl.classList.add('grid--flash');
    }
  }

  if (counter) {
    counter.textContent = shown === cards.length
      ? shown + ' ' + plural(shown)
      : shown + ' из ' + cards.length;
  }
  if (empty) empty.style.display = shown || past ? 'none' : 'block';
  updatePast(past);
  syncUrl();
}

chips.forEach(chip => chip.addEventListener('click', (e) => {
  // Чипы — ссылки на страницы подборок: без JS они ведут на /besplatno/ и т.д.,
  // с JS фильтруют на месте, не перезагружая страницу.
  e.preventDefault();
  const key = chip.dataset.filter;
  if (active.has(key)){
    active.delete(key);
    chip.removeAttribute('aria-current');
  } else {
    active.add(key);
    chip.setAttribute('aria-current', 'page');
  }
  apply(true);
  track('filter', key);
}));

dayChips.forEach(chip => chip.addEventListener('click', () => {
  day = day === chip.dataset.day ? '' : chip.dataset.day;
  syncDayChips();
  apply(true);
  track('day', day || 'both');
}));

if (zone) {
  zone.addEventListener('change', () => {
    apply(true);
    track(zoneKey, zone.value || 'all');
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
document.querySelectorAll('.card__report').forEach(link => {
  link.addEventListener('click', () => track('report'));
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
  day = '';
  showPast = false;
  chips.forEach(chip => chip.removeAttribute('aria-current'));
  syncDayChips();
  if (zone) zone.value = '';
  apply(true);
}));

apply();
// Время идёт: страница, открытая в браузере весь вечер, должна сама убрать
// событие, которое только что закончилось.
setInterval(apply, 60000);
"""

PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
<rect width="640" height="480" fill="#E8E2D6"/>
<text x="320" y="250" font-family="Segoe UI, sans-serif" font-size="26" fill="#1F5E4B" text-anchor="middle">Планы на выходные</text>
</svg>
"""

CURRENT_WEEKEND = weekend_of(date.today())

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
    parser.add_argument("--base", default="",
                        help="подпапка для GitHub Pages, например /plany-na-vyhodnye")
    parser.add_argument("--site-url", default="",
                        help="публичный адрес сайта для canonical и sitemap "
                             "(по умолчанию SITE_URL)")
    args = parser.parse_args(argv)

    global SITE_URL
    if args.site_url:
        SITE_URL = args.site_url.rstrip("/")

    today = date.fromisoformat(args.date) if args.date else date.today()
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
