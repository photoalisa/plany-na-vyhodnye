"""Нормализация адресов площадок перед выводом и записью в таблицу.

Выгрузки из справочников приходят в канцелярском виде:

    Новосибирская обл, ул. Свердлова, д. 13

Регион здесь дублирует addressLocality, а «д.» — остаток справочника, а не
часть адреса. Для карточки и разметки нужен вид «ул. Свердлова, 13».

У событий области населённый пункт попадает дважды — в addressLocality и
внутри улицы:

    addressLocality: Труновское
    streetAddress:   Новосибирская обл, ж/д_ст. Труновское, ул. Северная, д. 25

Такой префикс снимается, если совпадает с населённым пунктом.
"""

from __future__ import annotations

import re

REGION = "Новосибирская область"

_REGION_RE = re.compile(r"^новосибирск(?:ая)?\s+обл(?:асть)?\.?[\s,]*", re.I)

# Тип населённого пункта в начале адреса: «г. Черепаново», «рп. Сузун»,
# «ж/д_ст. Труновское». Дом («д.») сюда же: он стоит перед номером, а не
# перед названием, поэтому сравнение с населённым пунктом его отсеет.
_SETTLEMENT_RE = re.compile(
    r"^(?:г\.|город|рп\.|р\.п\.|пгт\.?|с\.|село|пос\.|посёлок|поселок|д\.|деревня|"
    r"ж/д[_\s]?ст\.|ст\.|аул|станица)\s*(\S.*)$",
    re.I,
)

_HOUSE_RE = re.compile(r"(?:^|\s)(?:д\.|дом|зд\.|здание)\s*(?=\d)", re.I)
_BLOCK_RE = re.compile(r"(?:^|\s)к\.\s*(?=\d)", re.I)


def normalize(raw: str, locality: str = "") -> str:
    """«Новосибирская обл, ул. Свердлова, д. 13» → «ул. Свердлова, 13»."""
    text = " ".join((raw or "").split())
    if not text:
        return ""

    text = _REGION_RE.sub("", text)
    parts = [piece.strip() for piece in text.split(",")]
    parts = [piece for piece in parts if piece]

    if parts and locality:
        match = _SETTLEMENT_RE.match(parts[0])
        if match:
            name = re.split(r"[,\s]", match.group(1).strip(), maxsplit=1)[0]
            if name.lower() == locality.strip().lower():
                rest = match.group(1)[len(name):].strip(" ,")
                parts = ([rest] if rest else []) + parts[1:]

    cleaned: list[str] = []
    for part in parts:
        part = _HOUSE_RE.sub(" ", part)
        part = _BLOCK_RE.sub(" корп. ", part)
        # «Красный пр-кт» и «пр-т» — тот же проспект, но по словарю улиц
        # сокращение не находилось, и район оставался пустым.
        part = re.sub(r"\bпр-?к?т\.?", "проспект", part, flags=re.IGNORECASE)
        part = re.sub(r"\s+", " ", part).strip(" ,")
        if part and (not cleaned or cleaned[-1] != part):
            cleaned.append(part)

    return ", ".join(cleaned)


def postal_address(raw: str, locality: str = "") -> dict:
    """PostalAddress для разметки: регион отдельно, улица без региона."""
    town = (locality or "").strip() or "Новосибирск"
    data = {
        "@type": "PostalAddress",
        "addressRegion": REGION,
        "addressLocality": town,
    }
    street = normalize(raw, town)
    if street:
        data["streetAddress"] = street
    return data
