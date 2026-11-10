"""Превью ссылки (og:image) под текущие выходные.

    python scripts/make_og.py            # на ближайшие выходные
    python scripts/make_og.py --date 2026-11-14

Рисует 1200×630 в стиле сайта: знак-пилон, Golos Text, тёплый фон #FBFAF7
и дата выходных крупно справа. Файл кладётся в static/og.png, откуда сборка
копирует его в корень сайта.

Нужен Playwright (`pip install playwright`, `playwright install chromium`) —
он уже используется для локальных проверок. Каждую неделю перед публикацией
скрипт запускается заново: иначе в превью останется прошлая дата.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = (ROOT / "static" / "assets" / "fonts").resolve()
MARK = (ROOT / "static" / "mark.svg").resolve()
DEFAULT_OUT = ROOT / "static" / "og.png"

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def weekend_of(today: date) -> tuple[date, date]:
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    return sat, sat + timedelta(days=1)


def label(sat: date, sun: date) -> tuple[str, str]:
    """«14—15» и «ноября» (или два месяца, если выходные переходят)."""
    if sat.month == sun.month:
        return f"{sat.day}–{sun.day}", MONTHS[sun.month - 1]
    return (f"{sat.day} {MONTHS[sat.month - 1]} – {sun.day}",
            MONTHS[sun.month - 1])


def html(dates: str, month: str) -> str:
    mark_svg = MARK.read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<style>
@font-face{{font-family:"Golos Text";font-style:normal;font-weight:400 900;font-display:block;
  src:url("file:///{FONTS.as_posix()}/golos-text-cyrillic.woff2") format("woff2")}}
@font-face{{font-family:"Golos Text";font-style:normal;font-weight:400 900;font-display:block;
  src:url("file:///{FONTS.as_posix()}/golos-text-latin.woff2") format("woff2")}}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;width:1200px;height:630px;background:#FBFAF7;
  font-family:"Golos Text",sans-serif;color:#14110F}}
.canvas{{position:relative;width:1200px;height:630px;padding:64px 72px;
  display:grid;grid-template-columns:1fr 392px;column-gap:56px;background:#FBFAF7}}
.left{{display:flex;flex-direction:column;justify-content:space-between;min-width:0}}
.brand{{display:flex;align-items:center;gap:16px}}
.brand svg{{width:52px;height:52px;border-radius:11px;display:block}}
.brand__name{{font-size:26px;font-weight:600;line-height:1.1;letter-spacing:-.01em}}
.brand__city{{font-size:11px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;
  color:#6B625A;margin-top:5px}}
.head h1{{margin:0;font-size:76px;line-height:1.04;letter-spacing:-.02em;font-weight:700}}
.head p{{margin:22px 0 0;font-size:25px;line-height:1.35;color:#6B625A}}
.foot{{display:flex;align-items:center;gap:14px;font-size:19px;color:#6B625A}}
.foot b{{color:#14110F;font-weight:600}}
.right{{border-left:1px solid #E3DDD1;padding-left:44px;display:flex;
  flex-direction:column;justify-content:center;gap:6px}}
.days{{font-size:92px;line-height:1;font-weight:700;letter-spacing:-.03em;color:#1F5E4B;
  font-variant-numeric:tabular-nums;white-space:nowrap}}
.month{{font-size:32px;line-height:1.1;color:#1F5E4B;font-weight:600;margin-top:4px}}
.note{{margin-top:18px;font-size:17px;color:#6B625A;line-height:1.4;max-width:22ch}}
</style></head>
<body><div class="canvas">
  <div class="left">
    <div class="brand">{mark_svg}
      <div>
        <div class="brand__name">Планы на выходные</div>
        <div class="brand__city">Новосибирск</div>
      </div>
    </div>
    <div class="head">
      <h1>Куда пойти<br>на выходных</h1>
      <p>Выставки, лекции, концерты и прогулки.<br>Каждое событие ведёт на источник.</p>
    </div>
    <div class="foot"><b>суббота и воскресенье</b> · афиша на два дня</div>
  </div>
  <div class="right">
    <div class="days">{dates}</div>
    <div class="month">{month}</div>
    <div class="note">Афиша Новосибирска на два дня</div>
  </div>
</div></body></html>
"""


async def render(markup: str, out: Path) -> None:
    from playwright.async_api import async_playwright

    page_path = ROOT / ".cache" / "og-preview.html"
    page_path.parent.mkdir(exist_ok=True)
    page_path.write_text(markup, encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 630},
                                     device_scale_factor=1)
        await page.goto(page_path.as_uri())
        await page.evaluate("document.fonts.ready")
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out))
        await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Превью ссылки под текущие выходные")
    parser.add_argument("--date", help="считать эту дату сегодняшней (YYYY-MM-DD)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="куда сохранить PNG")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    sat, sun = weekend_of(today)
    dates, month = label(sat, sun)
    out = Path(args.out)
    asyncio.run(render(html(dates, month), out))
    print(f"[+] {out} — {dates} {month}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
