"""Статьи для сайта: простые файлы .md с шапкой, без внешних библиотек.

Зачем: каталог отвечает на запрос «куда пойти на выходных», а статьи — на
длинный хвост («куда сходить с ребёнком», «что посмотреть в художественном
музее»). Поисковики такие страницы любят, если текст свой, а не скопированный
с афиши.

Формат файла `content/articles/<slug>.md`:

    ---
    title: Куда сходить с ребёнком в Новосибирске
    description: 120–160 знаков для выдачи
    h1: Куда сходить с ребёнком в Новосибирске
    date: 2026-11-06
    updated: 2026-11-06
    filter: child
    ---

    ## Подзаголовок
    Абзац с **выделением** и [ссылкой](/besplatno/).

Поддерживается ровно то, что нужно для статей: заголовки ## и ###, абзацы,
списки через «-» и «1.», жирный текст и ссылки. Больше — незачем.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

ARTICLES_DIR = Path("content/articles")

_FRONT = re.compile(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


@dataclass
class Article:
    slug: str
    title: str
    description: str
    h1: str
    date: str
    updated: str
    filter: str = ""
    body: str = ""
    extra: dict = field(default_factory=dict)


def load_articles(folder: Path = ARTICLES_DIR) -> list[Article]:
    """Читает все статьи из папки. Ошибки в шапке — предупреждение в консоль."""
    if not folder.exists():
        return []
    items: list[Article] = []
    for path in sorted(folder.glob("*.md")):
        article = _parse_file(path)
        if article:
            items.append(article)
    items.sort(key=lambda a: a.date, reverse=True)
    return items


def _parse_file(path: Path) -> Article | None:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    meta: dict[str, str] = {}
    body_start = 0

    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body_start = index + 1
                break
            match = _FRONT.match(line)
            if match:
                meta[match.group(1)] = match.group(2).strip()
        else:
            print(f"[!] {path.name}: не закрыта шапка статьи")
            return None

    body = "\n".join(lines[body_start:]).strip()
    if not body:
        print(f"[!] {path.name}: пустой текст статьи")
        return None

    h1 = meta.get("h1") or meta.get("title") or path.stem
    return Article(
        slug=path.stem,
        title=meta.get("title") or h1,
        description=meta.get("description", ""),
        h1=h1,
        date=meta.get("date", ""),
        updated=meta.get("updated") or meta.get("date", ""),
        filter=meta.get("filter", ""),
        body=body,
        extra={k: v for k, v in meta.items()
               if k not in {"title", "description", "h1", "date", "updated", "filter"}},
    )


def render_markdown(source: str) -> str:
    """Markdown → HTML. Экранирование всегда первым: в статье нет сырого HTML."""
    blocks: list[str] = []
    paragraph: list[str] = []
    list_kind = ""
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                blocks.append(f"<p>{_inline(text)}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_items:
            tag = "ol" if list_kind == "ol" else "ul"
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            blocks.append(f"<{tag}>{items}</{tag}>")
            list_items.clear()
        list_kind = ""

    for raw_line in source.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h3>{_inline(stripped[4:])}</h3>")
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h2>{_inline(stripped[3:])}</h2>")
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            if list_kind and list_kind != "ul":
                flush_list()
            list_kind = "ul"
            list_items.append(stripped[2:])
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            if list_kind and list_kind != "ol":
                flush_list()
            list_kind = "ol"
            list_items.append(re.sub(r"^\d+\.\s+", "", stripped))
            continue

        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def _inline(text: str) -> str:
    """Экранирование + жирный текст + ссылки (только http(s) и относительные)."""
    out = html.escape(text, quote=False)
    out = _BOLD.sub(r"<strong>\1</strong>", out)

    def link(match: re.Match) -> str:
        label = match.group(1)
        href = match.group(2)
        if not re.match(r"^(https?://|/)", href):
            return label
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return _LINK.sub(link, out)


def plain_text(article: Article, limit: int = 300) -> str:
    """Текст статьи без разметки — для описаний и анонсов."""
    text = re.sub(r"[#*\[\]()]", " ", article.body)
    text = re.sub(r"\((/[^)]*|https?://[^)]*)\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip(" ,.;:") + ("…" if len(text) > limit else "")
