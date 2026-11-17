"""Картинка для README: без даты, чтобы не устаревала.

Баннер `og.png` привязан к выпуску и через неделю врёт, поэтому в README
используется эта карточка: название, две строки смысла и фильтры сайта.
Собирается тем же способом, что превью ссылки — Chromium через Playwright.

    python scripts/readme_card.py
    python scripts/readme_card.py --out путь.png

Результат — 1200×630 PNG в стиле сайта: тот же знак, Golos Text и фон #FBFAF7.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = (ROOT / "static" / "assets" / "fonts").resolve()
MARK = (ROOT / "static" / "mark.svg").resolve()
DEFAULT_OUT = ROOT / "static" / "readme-card.png"

FILTERS = ("Бесплатно", "С детьми", "Прогулки и природа", "За городом")


def html() -> str:
    mark_svg = MARK.read_text(encoding="utf-8")
    pills = "".join(f'<span class="pill">{name}</span>' for name in FILTERS)
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
  display:grid;grid-template-columns:1fr 372px;column-gap:56px;background:#FBFAF7}}
.left{{display:flex;flex-direction:column;justify-content:space-between;min-width:0}}
.brand{{display:flex;align-items:center;gap:16px}}
.brand svg{{width:52px;height:52px;border-radius:11px;display:block}}
.brand__name{{font-size:26px;font-weight:600;line-height:1.1;letter-spacing:-.01em}}
.brand__city{{font-size:11px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;
  color:#6B625A;margin-top:5px}}
.head h1{{margin:0;font-size:74px;line-height:1.04;letter-spacing:-.02em;font-weight:700}}
.head p{{margin:22px 0 0;font-size:25px;line-height:1.35;color:#6B625A}}
.foot{{display:flex;align-items:center;gap:14px;font-size:19px;color:#6B625A}}
.foot b{{color:#14110F;font-weight:600}}
.right{{border-left:1px solid #E3DDD1;padding-left:44px;display:flex;
  flex-direction:column;justify-content:center;gap:14px}}
.caption{{font-size:11px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;
  color:#6B625A;margin-bottom:2px}}
.pill{{display:inline-flex;align-items:center;min-height:52px;padding:0 22px;
  border-radius:999px;background:#F1EEE6;color:#14110F;font-size:21px;font-weight:500;
  white-space:nowrap}}
.pill:first-of-type{{background:#1F5E4B;color:#FBFAF7}}
.note{{margin-top:10px;font-size:17px;color:#6B625A;line-height:1.4;max-width:24ch}}
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
      <h1>Афиша на субботу<br>и воскресенье</h1>
      <p>События из музеев, библиотек и парков.<br>Два предложения на карточку, ссылка на источник.</p>
    </div>
    <div class="foot"><b>каталог обновляется дважды в неделю</b></div>
  </div>
  <div class="right">
    <div class="caption">Фильтры</div>
    {pills}
    <div class="note">Поиск по ситуации, а не по афише: что бесплатно, куда с ребёнком, куда на природу.</div>
  </div>
</div></body></html>
"""


async def render(markup: str, out: Path) -> None:
    from playwright.async_api import async_playwright

    page_path = ROOT / ".cache" / "readme-card.html"
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
    parser = argparse.ArgumentParser(description="Карточка для README без даты")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="куда сохранить PNG")
    args = parser.parse_args()
    out = Path(args.out)
    asyncio.run(render(html(), out))
    print(f"[+] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
