"""Собирает локальный прототип каталога из out/nsk_weekend.csv.

    python scripts/build_preview.py

Результат: site-preview/index.html — один самодостаточный файл с фильтрами.
Нужен, чтобы увидеть внешний вид на реальных данных до сборки Astro.
"""

from __future__ import annotations

import csv
import html
from datetime import date, datetime
from pathlib import Path

CSV_PATH = Path("out/nsk_weekend.csv")
OUT_PATH = Path("site-preview/index.html")

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

FILTERS = [
    ("free", "Бесплатно"),
    ("child", "С детьми"),
    ("dog", "С собакой"),
    ("nature", "Прогулки и природа"),
    ("outside", "За городом"),
]

PLACEHOLDER = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='480'>"
    "<rect width='640' height='480' fill='%23E8E2D6'/>"
    "<text x='320' y='250' font-family='sans-serif' font-size='26' fill='%231F5E4B' "
    "text-anchor='middle'>Планы на выходные</text></svg>"
)

CSS = """
:root{--bg:#FBFAF7;--ink:#14110F;--muted:#6B625A;--accent:#1F5E4B;--chip:#E8E2D6;--line:#E3DDD1}
@media (prefers-color-scheme:dark){:root{--bg:#14110F;--ink:#F3EFE7;--muted:#A79E93;--accent:#7FC7A8;--chip:#242019;--line:#2E2822}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:24px 20px 64px}
header.top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;
  border-bottom:1px solid var(--line);padding-bottom:16px}
.brand{font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:14px}
.weekend{color:var(--muted);font-size:14px}
h1{font-size:28px;line-height:1.2;margin:28px 0 6px}
.lead{color:var(--muted);margin:0 0 22px;max-width:62ch}
.controls{position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:5;
  display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--line)}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
  padding:7px 14px;font-size:14px;cursor:pointer}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
select{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
  padding:7px 12px;font-size:14px}
.count{margin-left:auto;color:var(--muted);font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:22px;margin-top:26px}
.card{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--bg)}
.card__media img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--chip)}
.card__body{padding:14px 16px 18px;display:flex;flex-direction:column;gap:8px;flex:1}
.card__meta{margin:0;font-size:13px;color:var(--accent);font-weight:600}
.card__title{margin:0;font-size:18px;line-height:1.25}
.card__text{margin:0;font-size:15px;color:var(--muted);flex:1}
.card__price{margin:0;font-size:14px;font-weight:600}
.card__link{font-size:14px;color:var(--accent);text-decoration:none}
.card__link:hover{text-decoration:underline}
.empty{display:none;padding:40px 0;color:var(--muted)}
footer{margin-top:48px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:13px}
"""

JS = """
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const chips = [...document.querySelectorAll('.chip')];
const district = document.querySelector('#district');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');

function sync(){ chips.forEach(c => c.setAttribute('aria-pressed', params.get(c.dataset.filter) === '1')); }
function apply(){
  const active = chips.map(c => c.dataset.filter).filter(k => params.get(k) === '1');
  const d = params.get('district') || '';
  district.value = d;
  let shown = 0;
  for (const card of cards){
    const okFlags = active.every(k => card.dataset[k] === 'true');
    const okDistrict = !d || card.dataset.district === d;
    const visible = okFlags && okDistrict;
    card.hidden = !visible;
    if (visible) shown++;
  }
  counter.textContent = shown + ' ' + plural(shown);
  if (empty) empty.style.display = shown ? 'none' : 'block';
  sync();
  const q = params.toString();
  history.replaceState(null, '', q ? '?' + q : location.pathname);
}
function plural(n){ const m = n % 100, d = n % 10;
  if (m > 10 && m < 20) return 'событий';
  if (d === 1) return 'событие'; if (d >= 2 && d <= 4) return 'события'; return 'событий'; }
chips.forEach(c => c.addEventListener('click', () => {
  const k = c.dataset.filter;
  params.get(k) === '1' ? params.delete(k) : params.set(k, '1');
  apply();
}));
district.addEventListener('change', () => {
  district.value ? params.set('district', district.value) : params.delete('district');
  apply();
});
document.querySelector('[data-reset]').addEventListener('click', () => {
  ['free','child','dog','nature','outside','district'].forEach(k => params.delete(k)); apply();
});
apply();
"""


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def date_label(row: dict) -> str:
    start, end = parse_date(row["date_start"]), parse_date(row["date_end"])
    if row.get("kind") == "permanent":
        return "постоянно"
    if not start:
        return "дата уточняется"
    if row.get("kind") == "recurring":
        return f"каждую неделю" + (f", {row['time_text']}" if row.get("time_text") else "")
    if not end or end == start:
        return f"{start.day} {MONTHS[start.month - 1]}, {WEEKDAYS[start.weekday()]}"
    tail = f"{end.day} {MONTHS[end.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} – {tail}"


def truthy(value: str) -> bool:
    return str(value).strip().upper() in {"TRUE", "1", "YES", "ДА"}


def render_card(row: dict) -> str:
    image = row["image_url"] if truthy(row.get("image_ok", "")) and row.get("image_url") else PLACEHOLDER
    title = html.escape(row["title"])
    short = html.escape(row.get("text_short") or row.get("text_raw", "")[:160])
    price = html.escape(row.get("price_text") or "Цена на сайте")
    url = html.escape(row.get("source_url", ""), quote=True)
    district = row.get("district", "")
    meta_bits = [date_label(row)]
    if district:
        meta_bits.append(district)
    if row.get("venue") and len(row["venue"]) < 42:
        meta_bits.append(row["venue"])
    attrs = " ".join(
        f'data-{key}="{str(truthy(row.get(column, ""))).lower()}"'
        for key, column in [
            ("free", "is_free"), ("child", "is_child_friendly"), ("dog", "is_dog_ok"),
            ("nature", "is_nature"), ("outside", "is_outside_city"),
        ]
    )
    return f"""
<article class="card" {attrs} data-district="{html.escape(district, quote=True)}" data-start="{row['date_start']}">
  <a class="card__media" href="{url}" target="_blank" rel="noopener nofollow">
    <img src="{html.escape(image, quote=True)}" alt="{title}" loading="lazy"
         onerror="this.onerror=null;this.src='{PLACEHOLDER}'">
  </a>
  <div class="card__body">
    <p class="card__meta">{html.escape(" · ".join(meta_bits))}</p>
    <h2 class="card__title">{title}</h2>
    <p class="card__text">{short}</p>
    <p class="card__price">{price}</p>
    <a class="card__link" href="{url}" target="_blank" rel="noopener nofollow">Источник →</a>
  </div>
</article>"""


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"Нет {CSV_PATH}. Сначала выполните: python check.py")

    with CSV_PATH.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("status") == "active" and r.get("date_start")]

    # разовые и короткие — вперёд по дате, длинные выставки — за ними,
    # постоянные экспозиции — в конце
    kind_rank = {"one_time": 0, "recurring": 1, "long_term": 2, "permanent": 3}
    rows.sort(key=lambda r: (kind_rank.get(r.get("kind", ""), 2), r["date_start"], r["title"]))

    districts = sorted({r["district"] for r in rows if r.get("district")})
    cards = "\n".join(render_card(r) for r in rows)

    options = "".join(f'<option value="{html.escape(d, quote=True)}">{html.escape(d)}</option>'
                      for d in districts)
    chips = "".join(
        f'<button class="chip" data-filter="{key}" aria-pressed="false">{label}</button>'
        for key, label in FILTERS
    )

    today = date.today()
    sat = today
    html_doc = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Планы на выходные — Новосибирск (прототип)</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <span class="brand">Планы на выходные · Новосибирск</span>
    <span class="weekend">прототип на реальных данных · {len(rows)} событий</span>
  </header>

  <h1>Куда пойти на выходных</h1>
  <p class="lead">Короткие анонсы на ближайшие субботу и воскресенье: выставки,
  лекции, концерты и прогулки. Отобрано вручную, без «воды» и рекламы.</p>

  <div class="controls">
    {chips}
    <select id="district" aria-label="Район"><option value="">Все районы</option>{options}</select>
    <button class="chip" data-reset>Сбросить</button>
    <span class="count"><span data-count>0</span></span>
  </div>

  <div class="grid">
    {cards}
  </div>

  <p class="empty">По этим фильтрам на выходные ничего нет. <button class="chip" data-reset>Сбросить фильтры</button></p>

  <footer>
    Сайт не собирает персональные данные и не хранит афиши: изображения загружаются
    по ссылке источника, карточка ведёт на официальную страницу организатора.
    Ответственность за актуальность несёт организатор.
  </footer>
</div>
<script>{JS}</script>
</body>
</html>"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_doc, encoding="utf-8")
    print(f"[+] Прототип: {OUT_PATH} — карточек {len(rows)}, районов {len(districts)}")


if __name__ == "__main__":
    main()
