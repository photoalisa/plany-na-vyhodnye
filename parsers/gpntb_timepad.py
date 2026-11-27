"""ГПНТБ СО РАН на Timepad (gpntb-so-ran-event.timepad.ru).

Лента организатора того же формата, что у «Карты мира»: разбор общий,
различаются адрес и площадка. На 27.11.2026 в ленте двадцать карточек и все
прошедшие — актуальных событий организатор сейчас не публикует, поэтому
источник может отдавать ноль. Это не ошибка разбора.
"""

from __future__ import annotations

from common import timepad

BASE = "https://gpntb-so-ran-event.timepad.ru"


def fetch() -> list[dict]:
    return timepad.fetch_org(
        BASE,
        source="ГПНТБ",
        venue="ГПНТБ СО РАН",
        address="ул. Восход, 15",
    )
