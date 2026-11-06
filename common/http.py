"""HTTP-слой: одна сессия, ретраи, дисковый кэш, параллельный обход, проверка картинок."""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

CACHE_DIR = Path(os.environ.get("NSK_CACHE_DIR", ".cache/http"))
CACHE_ENABLED = os.environ.get("NSK_CACHE", "1") != "0"

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=32)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        _session = s
    return _session


def _cache_path(url: str) -> Path:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / key[:2] / f"{key}.txt"


def get_text(url: str, timeout: int = 20, ttl: int = 3600, refresh: bool = False) -> str:
    """GET с дисковым кэшем. ttl=0 — без кэша. Возвращает '' при ошибке."""
    if CACHE_ENABLED and ttl > 0 and not refresh:
        path = _cache_path(url)
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    try:
        r = session().get(url, timeout=timeout)
        if r.status_code != 200:
            print(f"  [-][http] {url} -> {r.status_code}")
            return ""
        r.encoding = r.encoding or r.apparent_encoding
        text = r.text
    except requests.RequestException as e:
        print(f"  [-][http] {url}: {type(e).__name__}")
        return ""
    if CACHE_ENABLED and ttl > 0:
        try:
            path = _cache_path(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return text


def get_json(url: str, timeout: int = 20, ttl: int = 3600) -> dict | list | None:
    raw = get_text(url, timeout=timeout, ttl=ttl)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def image_ok(url: str, timeout: int = 10) -> bool:
    """HEAD/GET-проверка, что ссылка отдаёт реальную картинку (риск 9 устава)."""
    if not url or not url.startswith("http"):
        return False
    try:
        r = session().head(url, timeout=timeout, allow_redirects=True)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if r.status_code == 200 and ctype.startswith("image/"):
            return True
        if r.status_code in (403, 405):  # часть CDN не любит HEAD
            r = session().get(url, timeout=timeout, stream=True)
            ctype = (r.headers.get("Content-Type") or "").lower()
            return r.status_code == 200 and ctype.startswith("image/")
        return False
    except requests.RequestException:
        return False


def parallel(func: Callable, items: Iterable, workers: int = 6) -> list:
    """Параллельный обход страниц (ускоряет глубокое чтение в 3-5 раз)."""
    items = list(items)
    if not items:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(func, item): item for item in items}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                print(f"  [-][pool] {type(e).__name__}: {e}")
    return results
