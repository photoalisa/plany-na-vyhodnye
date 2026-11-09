"""Оффлайн-проверка разбора текстов для ручных событий.

    python tests/test_manual_add.py

Проверяем то, что чаще всего ломается в ручных текстах: шапка с русскими
названиями полей, несколько событий в одном файле через «---», пустые строки
между шапкой и описанием и грамматика дат вроде «14 ноября».
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.manual_add import read_file, split_block  # noqa: E402

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
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
