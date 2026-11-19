"""Валидация ссылок и обязательных полей (риск 8 устава: битые данные).

Проверка ссылок устроена тремя списками:

* белый — площадки, которые мы знаем и публикуем (`ALLOWED_HOSTS` плюс
  дополнения из `data/allowed_hosts.json`);
* запрещённые — платформы, чьи ссылки не публикуем (устав, раздел 4);
* очередь проверки — всё незнакомое, подозрительное и не-веб: такие ссылки
  не публикуются, но и не теряются, а попадают в лист `review`.

`classify(url)` — единственная точка решения. Её вызывают сборщик, ручной
контур, импорт выгрузки и сборка сайта: правило одно для всех путей.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

from . import http

ROOT = Path(__file__).resolve().parents[1]
EXTRA_ALLOWED = ROOT / "data" / "allowed_hosts.json"
REJECTED = ROOT / "data" / "rejected_hosts.json"

ALLOWED_HOSTS = {
    "cc19.ru", "nsartmuseum.ru", "youmuseum.ru", "bashnya-site.ru",
    "kulturansk.ru", "nebo-nsk.ru", "aura.planeta-mall.ru", "mega.ru",
    "artweeknd.com", "kapustacor.ru", "welcome-novosibirsk.ru",
    "nadvizh.ru", "ngonb.ru", "m-nsk.ru", "dusoran.ru",
    "calendar.welcome-novosibirsk.ru", "parkigoroda-nsk.ru",
    "novosibirsk.myhistorypark.ru", "iskmuseum.ru", "gastro-nsk.ru",
    "culture.ru", "xn--80abjcbjpll9bmq.xn--p1ai", "sshbn.ru",
}

# Ресурсы, ссылки на которые не публикуем (устав, раздел 4).
BANNED_HOSTS = {"instagram.com", "facebook.com", "fb.com", "meta.com"}

# Сокращатели и редиректы: за ссылкой не видно, куда она ведёт.
SHORTENERS = {
    "bit.ly", "clck.ru", "vk.cc", "tinyurl.com", "goo.gl", "t.co",
    "is.gd", "cutt.ly", "clck.me", "u.to", "vk.link",
}

# Прямые файлы вместо страницы события.
FILE_SUFFIXES = (".exe", ".apk", ".zip", ".rar", ".7z", ".dmg", ".msi",
                 ".pdf", ".doc", ".docx", ".xls", ".xlsx")

ALLOWED_STATUS = {"allowed", "banned", "review", "invalid"}

_extra_cache: set[str] | None = None
_rejected_cache: set[str] | None = None


def extra_allowed() -> set[str]:
    """Домены, подтверждённые вручную. Хранятся отдельно от кода."""
    global _extra_cache
    if _extra_cache is None:
        try:
            data = json.loads(EXTRA_ALLOWED.read_text(encoding="utf-8"))
            _extra_cache = {str(item).strip().lower() for item in data if str(item).strip()}
        except (OSError, ValueError):
            _extra_cache = set()
    return _extra_cache


def rejected_hosts() -> set[str]:
    """Домены, которые человек отклонил в очереди проверки."""
    global _rejected_cache
    if _rejected_cache is None:
        try:
            data = json.loads(REJECTED.read_text(encoding="utf-8"))
            _rejected_cache = {str(item).strip().lower() for item in data if str(item).strip()}
        except (OSError, ValueError):
            _rejected_cache = set()
    return _rejected_cache


def reload_allowed() -> None:
    """Сбросить кеш: нужно после подтверждения или отказа по домену."""
    global _extra_cache, _rejected_cache
    _extra_cache = None
    _rejected_cache = None


def allowed_hosts() -> set[str]:
    return ALLOWED_HOSTS | extra_allowed()


def normalize_host(url: str) -> str:
    """Хост для сравнения: без www, порта и регистра, IDN в punycode."""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host


def _matches(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _lookalike(host: str) -> str:
    """Похоже на известный домен, но не он: cc19.ru.tk, vk-g0.ru."""
    for domain in allowed_hosts() | BANNED_HOSTS:
        if domain in host and not (host == domain or host.endswith("." + domain)):
            return domain
    return ""


def classify(url: str) -> tuple[str, str, str]:
    """Ссылка → (статус, причина, хост).

    Статусы: allowed — публикуем, banned — платформа под запретом,
    review — незнакомое или подозрительное, invalid — не ссылка вовсе.
    """
    raw = (url or "").strip()
    if not raw:
        return "invalid", "empty", ""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return "invalid", "scheme", ""
    host = normalize_host(raw)
    if not host:
        return "invalid", "host", ""
    if _matches(host, BANNED_HOSTS):
        return "banned", "platform", host
    if _is_ip(host):
        return "review", "ip", host
    # Сокращатели и прямые файлы проверяем раньше белого списка: даже на
    # известной площадке /file.apk — это не страница события.
    if _matches(host, SHORTENERS):
        return "review", "shortener", host
    if parsed.path.lower().endswith(FILE_SUFFIXES):
        return "review", "file", host
    if _matches(host, allowed_hosts()):
        return "allowed", "", host
    if _matches(host, rejected_hosts()):
        return "review", "rejected", host
    twin = _lookalike(host)
    if twin:
        return "review", f"lookalike:{twin}", host
    return "review", "unknown", host


def _is_banned(url: str) -> bool:
    return classify(url)[0] == "banned"


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
    status, reason, host = classify(url)
    if status == "invalid":
        errors.append("source_url")
    elif status == "banned":
        errors.append(f"banned-host:{host}")
    elif status == "review":
        # Незнакомое не публикуем, но и не теряем: хост уходит в очередь review.
        errors.append(f"source_url-review:{host}")
        ev["_review_reason"] = reason

    # У постоянной экспозиции даты начала нет по определению — это не ошибка.
    if not ev.get("date_start") and ev.get("kind") != "permanent":
        errors.append("date")
    if ev.get("kind") == "unknown":
        errors.append("kind")

    if len(ev.get("text_raw") or "") < MIN_TEXT:
        errors.append("text")

    if check_image:
        image = ev.get("image_url") or ""
        image_status = classify(image)[0] if image.startswith("http") else "invalid"
        if image_status in ("banned", "review", "invalid"):
            # Афиша на непроверенном или запрещённом хосте: не показываем её,
            # но событие из-за картинки не отбрасываем.
            ev["image_ok"] = False
            ev["image_url"] = ""
        else:
            ev["image_ok"] = http.image_ok(image)
            if not ev["image_ok"]:
                errors.append("image")

    return errors
