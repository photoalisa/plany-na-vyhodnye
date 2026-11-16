"""Оффлайн-проверка разбора текстов для ручных событий.

    python tests/test_manual_add.py

Проверяем то, что чаще всего ломается в ручных текстах: шапка с русскими
названиями полей, несколько событий в одном файле через «---», пустые строки
между шапкой и описанием и грамматика дат вроде «14 ноября».
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.manual_add import (  # noqa: E402
    is_vk_dump, read_file, split_block, vk_to_blocks,
)

TEXT = """название: Мастер-класс «Глина» в парке «Берёзовая роща»
дата: 14 ноября
время: 15:00
площадка: Парк «Берёзовая роща»
адрес: ул. Планетная, 53
район: Дзержинский
цена: Бесплатно

Лепим из глины, приходите всей семьёй.

---
title: Мастер-класс в парке «Бугринская роща»
date: 15 ноября
time: 11:00
venue: Парк «Бугринская роща»
address: ул. Саввы Кожевникова, 39а
price: Бесплатно

Второе событие в том же файле.
"""


ANNOUNCEMENT = """Музей на набережной. Обская, 4. Вход платный. 📅 14 ноября, 10:00
Экскурсия по выставке «Время денег» 6+
Знакомимся с историей денег и музейной коллекцией.

📅 14 ноября, 15:00
Обзорная экскурсия «Открой музей» 6+
Вход платный, экскурсионное обслуживание — бесплатно.
"""


def check() -> int:
    fields, body = split_block(TEXT.split("---")[0])
    assert fields["title"].startswith("Мастер-класс"), fields
    assert fields["date_raw"] == "14 ноября", fields
    assert fields["venue"] == "Парк «Берёзовая роща»", fields
    assert body.startswith("Лепим из глины"), body

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "park.txt"
        path.write_text(TEXT, encoding="utf-8")
        events = read_file(path, "Вручную")

    assert len(events) == 2, [ev["title"] for ev in events]
    first, second = events
    assert first["date_start"] == "2026-11-14", first["date_start"]
    assert first["time_text"] == "15:00", first["time_text"]
    assert second["date_start"] == "2026-11-15", second["date_start"]
    assert first["is_free"] and first["is_child_ok"], first
    assert first["district"] == "Дзержинский", first["district"]
    assert first["source"] == "Вручную"
    assert first["id"] and second["id"] and first["id"] != second["id"]
    print("ок: шапка «ключ: значение», 2 события, даты и флаги разобраны")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "museum.txt"
        path.write_text(ANNOUNCEMENT, encoding="utf-8")
        live = read_file(path, "Вручную")

    assert len(live) == 2, [ev["title"] for ev in live]
    tour, open_day = live
    assert tour["date_start"] == "2026-11-14", tour["date_start"]
    assert tour["time_text"] == "10:00", tour["time_text"]
    assert tour["title"] == "Экскурсия по выставке «Время денег»", tour["title"]
    assert tour["venue"] == "Музей на набережной", tour["venue"]
    assert tour["address"] == "Обская, 4", tour["address"]
    assert tour["age"] == "6+", tour["age"]
    assert tour["price_text"] == "Вход платный", tour["price_text"]
    # «экскурсионное обслуживание — бесплатно» не делает платное событие бесплатным
    assert not open_day["is_free"], open_day["price_text"]
    print("ок: анонс с афиши, 2 события, цена и возраст разобраны")

    check_vk_dump()
    return 0


VK_DUMP = """1. Источник https://vk.ru/wall-1
Изображение https://example.com/1.jpg

ТРЦ «Аура» Соседский клуб\tул. Военная, 5\t

«Это тебе на Новый год. Но давай я уже покажу!» 😄

Ближайшие мастер-классы:
📅 15 ноября — 14:00–16:00
📅 28 ноября — 15:00–17:00
💜 Стоимость участия — 1 100 ₽.

2. Источник https://vk.ru/wall-2
Изображение https://example.com/2.jpg

Крыша | Музыкальное пространство Нарымская улица, 25

Музыкальный разбор полетов возвращается!

14 ноября
Сбор в 18:30 | Начало в 19:00
Нарымская, 25 | Вход свободный
"""


def check_vk_dump() -> None:
    """Выгрузка постов ВК: две площадки и два события, а не одно с временем."""
    today = date(2026, 11, 13)
    assert is_vk_dump(VK_DUMP), "файл с двумя постами не распознан"
    blocks, count = vk_to_blocks(VK_DUMP, today)
    assert count == 2, count
    first, second = blocks.split("\n---\n")
    assert "venue: ТРЦ «Аура» Соседский клуб" in first, first
    assert "address: ул. Военная, 5" in first, first
    assert "date: 15 ноября" in first, first
    assert "time: 14:00–16:00" in first, first
    assert "price: 1 100 ₽" in first, first
    assert "Другие даты: 28 ноября." in first, first
    assert "date: 14 ноября" in second, second
    assert "time: 19:00" in second, second
    assert "price: Бесплатно" in second, second
    assert "address: Нарымская улица, 25" in second, second

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dump-vk.txt"
        path.write_text(blocks, encoding="utf-8")
        events = read_file(path, "Вручную")

    assert len(events) == 2, [ev["title"] for ev in events]
    for ev in events:
        # Раньше на этом месте появлялось название «17:00».
        assert not ev["title"].replace(":", "").isdigit(), ev["title"]
        assert ev["venue"], ev
    assert events[0]["date_start"] == "2026-11-15", events[0]["date_start"]
    assert events[1]["date_start"] == "2026-11-14", events[1]["date_start"]
    print("ок: выгрузка ВК, 2 события, площадка и дата на месте")


if __name__ == "__main__":
    raise SystemExit(check())
