"""Книжный магазин «Карта мира» (kartamira.timepad.ru) — лента на Timepad.

Разбор карточек живёт в `common/timepad.py`: тот же формат у второго
организатора (ГПНТБ СО РАН).
"""

from __future__ import annotations

from common import timepad

BASE = "https://kartamira.timepad.ru"


def fetch() -> list[dict]:
    return timepad.fetch_org(
        BASE,
        source="Карта мира",
        venue="Книжный магазин «Карта мира»",
        address="ул. Красноярская, 3",
    )
