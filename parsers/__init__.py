"""Парсеры источников. Контракт: fetch() -> list[dict] с сырыми полями события.

Обязательные ключи: title, date_raw, source_url, text_raw.
Необязательные: image_url, venue, address, district, geo_area, category,
tags, price_text, kind_hint, is_free.

Что парсер НЕ должен делать:
  * разбирать дату вручную — этим занимается common.dates;
  * вычислять актуальность — сделать prefilter через dates.is_weekend_relevant
    можно, но финальное решение принимает оркестратор.
"""
