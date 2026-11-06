"""Валидация перед записью в таблицу (риск 8 устава: битые данные)."""

from __future__ import annotations

from urllib.parse import urlparse

from . import http

ALLOWED_HOSTS = {
    "cc19.ru", "nsartmuseum.ru", "youmuseum.ru", "bashnya-site.ru",
    "kulturansk.ru", "nebo-nsk.ru", "aura.planeta-mall.ru", "mega.ru",
    "artweeknd.com", "kapustacor.ru", "welcome-novosibirsk.ru",
    "nadvizh.ru", "ngonb.ru", "m-nsk.ru", "dusoran.ru",
    "calendar.welcome-novosibirsk.ru", "parkigoroda-nsk.ru",
    "novosibirsk.myhistorypark.ru", "iskmuseum.ru",
}

# Ресурсы, ссылки на которые не публикуем (устав, раздел 4).
BANNED_HOSTS = {"instagram.com", "facebook.com", "fb.com", "meta.com"}


def _is_banned(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().replace("www.", "")
    return any(host == h or host.endswith("." + h) for h in BANNED_HOSTS)

MIN_TEXT = 40
MIN_TITLE = 4


def validate(ev: dict, check_image: bool = False) -> list[str]:
    """Список ошибок. Пустой список — событие годно к публикации."""
    errors = []

    title = (ev.get("title") or "").strip()
    if len(title) < MIN_TITLE:
        errors.append("title")
    elif title.lower() in {"афиша", "события", "выставки", "новости", "главная"}:
        errors.append("title-noise")

    url = ev.get("source_url") or ""
    if not url.startswith("http"):
        errors.append("source_url")
    else:
        host = (urlparse(url).hostname or "").lower().replace("www.", "")
        if _is_banned(url):
            errors.append(f"banned-host:{host}")
        elif ALLOWED_HOSTS and not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            errors.append(f"source_url-host:{host}")

    # У постоянной экспозиции даты начала нет по определению — это не ошибка.
    if not ev.get("date_start") and ev.get("kind") != "permanent":
        errors.append("date")
    if ev.get("kind") == "unknown":
        errors.append("kind")

    if len(ev.get("text_raw") or "") < MIN_TEXT:
        errors.append("text")

    if check_image:
        image = ev.get("image_url") or ""
        if image and image.startswith("http"):
            if _is_banned(image):
                # афиша лежит на запрещённом ресурсе — просто не показываем её
                ev["image_ok"] = False
                ev["image_url"] = ""
            else:
                ev["image_ok"] = http.image_ok(image)
                if not ev["image_ok"]:
                    errors.append("image")
        else:
            ev["image_ok"] = False
            ev["image_url"] = ""

    return errors
