"""Очистка текста, цена, пять быстрых фильтров и районы — без ИИ, детерминированно."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
# Невидимые символы, которые иногда приезжают из HTML: BOM и zero-width.
_INVISIBLE = re.compile(r"[\ufeff\u200b\u200e\u200f]")
_PREFIX = re.compile(
    r"^(?:все\s+(?:выставки|события|концерты|спектакли|экскурсии|фестивали|встречи|лекции|мастер-классы)"
    r"|главная(?:\s*[/>»|]|\s|$)|афиша(?:\s+новосибирска)?|назад|вернуться|поделиться|меню"
    r"|выставки|события|концерты|спектакли|экскурсии|фестивали|встречи|лекции|мастер-классы)"
    r"[\s/,•·|—–>-]*",
    re.IGNORECASE,
)
_BOILERPLATE = (
    "купить билет", "стоимость билетов", "заказать билет", "справки по телефону",
    "справки по тел", "заказ билетов", "оплатить пушкинской картой", "как добраться",
    "подписаться на новости", "поделиться", "поделитесь", "прокрутить вверх",
    "оставить отзыв", "забронировать экскурсию", "все права защищены",
)
_PRICE_RE = re.compile(
    r"(\d[\d\s]{0,6})\s*(?:₽|руб\.?|рублей|р\.)\s*(?:/|в|руб)?", re.IGNORECASE
)
_FREE_RE = re.compile(
    r"(бесплатн|вход\s+свободн|свободный\s+вход|0\s*(?:₽|руб)|without\s+charge|free\s+entry)",
    re.IGNORECASE,
)
# Точка после сокращения — не конец предложения: «по адресу ул. Василия
# Старощука, 24» иначе рвалось на «…по адресу ул.» и «Василия Старощука…».
_ABBR_GUARD = (
    r"(?<!ул.)(?<!д.)(?<!г.)(?<!им.)(?<!пр.)(?<!наб.)(?<!пер.)(?<!рис.)(?<!стр.)"
)
_SENT_SPLIT = re.compile(_ABBR_GUARD + r"(?<=[.!?…])\s+")


def split_sentences(value: str) -> list[str]:
    """Делит текст на предложения, не разрывая сокращения и числа.

    «…«Быт крестьян Сибири к. 19 н. 20 вв».» — это одно предложение, а не три:
    фрагмент, который начинается со строчной буквы или цифры, приклеиваем
    обратно к предыдущему. Иначе карточка обрывалась на «…Сибири к.».
    """
    parts = [part for part in _SENT_SPLIT.split(value or "") if part.strip()]
    merged: list[str] = []
    for part in parts:
        head = part[:1]
        if merged and (head.islower() or head.isdigit()):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged

# --- пять быстрых фильтров из устава ---------------------------------------
# Возрастная метка: «0+», «6+», «12+», «18+»
_AGE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*\+")

# «Можно с детьми» ставится по умолчанию, а снимают его только явные
# взрослые метки. Сам «18+» проверяется через age_limit: в тексте фестиваля
# могут быть отдельные фильмы «(18+)», хотя сам он 12+.
_ADULT_ONLY = re.compile(
    r"(только\s+для\s+взрослых|строго\s*18|для\s+взрослой\s+аудитории|"
    r"эротич|стриптиз|казино|ночной\s+клуб)",
    re.IGNORECASE,
)

# Явно детское событие: по этим признакам ставится отдельная метка
# is_child_friendly, по которой работает фильтр «с детьми» на сайте.
_CHILD_FOR = re.compile(
    r"(для\s+детей|с\s+детьми|для\s+всей\s+семьи|всей\s+семьёй|для\s+семьи|семейн|"
    r"детям\b|ребён|ребен|школьник|малыш|подростк|младш\w*\s+школьник|"
    r"детск\w*\s+билет|билет\w*\s+(?:для\s+)?дет|"
    r"детск\w*\s+(спектакл|утренник|праздник|программ|фестивал|кино|показ|экскурс|мастер))",
    re.IGNORECASE,
)
# События ПРО детей — это не значит «можно прийти с ребёнком».
_CHILD_ABOUT = re.compile(
    r"(о\s+детях|про\s+детей|детств|детвор|"
    r"детск\w*\s+(выставк|музей|центр|школ|художествен)|"
    r"дети\s+(неба|войны|блокады))",
    re.IGNORECASE,
)

# «С животными» — приглашение прийти с питомцем, а не событие про собак.
_PET_RE = re.compile(
    r"(с\s+собакой|с\s+собаками|с\s+питомцем|с\s+питомцами|с\s+животн\w*|"
    r"можно\s+с\s+животн\w*|dog[-\s]?friendly|дог[-\s]?френдли|пет[-\s]?френдли)",
    re.IGNORECASE,
)
_PET_NOT_ALLOWED = re.compile(
    r"(нельзя\s+с\s+животн|без\s+животн|выставк\w*\s+(собак|кошек|животн)|приют)",
    re.IGNORECASE,
)
_NATURE_RE = re.compile(
    r"(\bпарк(?!овк|ет)|сквер|набережн|ботаническ|дендропарк|лес\b|лесопарк|озеро|река\b|берег|"
    r"пляж|смотровая|панорам|природ|аллея|зелён|зелен\w+\s+зон|графская\s+роща|"
    r"ключ-камышенск|заельцовск\w+\s+бор)", re.IGNORECASE
)
# «Синема Парк», «Лемурия Парк», «Парк развлечений» — вывески и залы, а не
# зелёные зоны, хотя слово «парк» стоит в них отдельно.
_PARK_BRAND = re.compile(
    r"(?:синем\w*|лемури\w*|аква|авто|бизнес|торгов\w*|гостиничн\w*)\s*-?\s*\bпарк\w*"
    r"|\bпарк\w*\s+(?:развлечен|аттракцион)\w*",
    re.IGNORECASE,
)
# Залы: в них может идти выставка «о природе», но это не прогулка.
_INDOOR_RE = re.compile(
    r"(музей|библиотек|галере|театр|центр\b|пространств|дворец|павильон|зал\b)",
    re.IGNORECASE,
)
_OUTSIDE_RE = re.compile(
    r"(бердск|кольцов|академгородок|искитим|мочище|сузун|обь-гэс|обьгэс|краснообск|"
    r"за\s+городом|пригород|новосибирск\w*\s+област|тоогучин|болотное|ленинск-кузнецк|"
    r"маслянино|коченево|ордынск)",
    re.IGNORECASE,
)

# --- районы Новосибирска ---------------------------------------------------
# Названия районов и их устойчивые написания (для адресов вида «Заельцовский район»).
_DISTRICTS = {
    "Центральный": ("центральн",),
    "Заельцовский": ("заельцовск",),
    "Кировский": ("кировск",),
    "Ленинский": ("ленинск",),
    "Октябрьский": ("октябрьск",),
    "Дзержинский": ("дзержинск",),
    "Калининский": ("калининск",),
    "Советский": ("советск",),
    "Первомайский": ("первомайск",),
    "Железнодорожный": ("железнодорожн",),
}

# Улицы, по которым район определяется однозначно. Спорные (Красный проспект,
# Ватутина, Немировича-Данченко) сюда не входят: без явного «район» в адресе
# лучше оставить пусто, чем поставить неверный район.
_STREETS = {
    "ленина": "Центральный", "советская": "Центральный", "свердлова": "Центральный",
    "коммунистическая": "Центральный", "октябрьская": "Центральный",
    "романова": "Центральный", "мичурина": "Центральный", "каинская": "Центральный",
    "депутатская": "Центральный", "фрунзе": "Центральный", "гоголя": "Центральный",
    "крылова": "Центральный", "чаплыгина": "Центральный", "щетинкина": "Центральный",
    "некрасова": "Центральный", "орджоникидзе": "Центральный", "трудовая": "Центральный",
    "дзержинского": "Дзержинский", "авиастроителей": "Дзержинский",
    "трикотажная": "Дзержинский", "волочаевская": "Дзержинский",
    "кирова": "Октябрьский", "большевистская": "Октябрьский", "выборная": "Октябрьский",
    "никитина": "Октябрьский", "гурьевская": "Октябрьский", "лескова": "Октябрьский",
    "старощука": "Октябрьский",
    "ватутина": "Ленинский", "станиславского": "Ленинский", "титова": "Ленинский",
    "покрышкина": "Ленинский", "связистов": "Ленинский", "пархоменко": "Ленинский",
    "геодезическая": "Ленинский", "энергетиков": "Ленинский", "троллейная": "Ленинский",
    "широкая": "Ленинский", "халтурина": "Ленинский", "планировочная": "Ленинский",
    "сибиряков-гвардейцев": "Кировский", "зорге": "Кировский", "петухова": "Кировский",
    "новогодняя": "Кировский", "виктора уса": "Кировский",
    "кутателадзе": "Советский", "молодости": "Советский", "терешковой": "Советский",
    "вяземская": "Советский", "арбузова": "Советский", "пирогова": "Советский",
    "университетский": "Советский", "морской": "Советский", "ильича": "Советский",
    "дуси ковальчук": "Заельцовский", "аэропорт": "Заельцовский",
    "планетарная": "Заельцовский",
    # Ключ-Камышенское плато относится к Октябрьскому району, хотя стоит
    # на севере города рядом с Заельцовским бором.
    "ключ-камышенск": "Октябрьский",
    "жуковского": "Заельцовский", "кропоткина": "Заельцовский",
    "вокзальная магистраль": "Железнодорожный", "димитрова": "Железнодорожный",
    "челюскинцев": "Железнодорожный", "нарымская": "Железнодорожный",
    "урицкого": "Железнодорожный", "сибирская": "Железнодорожный",
    "фабричная": "Железнодорожный",
    "богдана хмельницкого": "Калининский", "народная": "Калининский",
    "свечникова": "Калининский", "рассветная": "Калининский",
    "красных зорь": "Калининский",
}

_AREA_BY_CITY = {
    "бердск": "Бердск",
    "кольцов": "Кольцово",
    "академгородок": "Академгородок",
    "краснообск": "Краснообск",
    "сузун": "Сузун",
    "искитим": "Искитим",
    "мочище": "Мочище",
    "маслянино": "Маслянино",
    "коченево": "Коченёво",
    "ордынск": "Ордынск",
}

_RE_DISTRICT_IN_ADDRESS = re.compile(r"([А-ЯЁ][а-яё-]+)\s+район", re.IGNORECASE)

# Известные площадки: по адресу или упоминанию в тексте узнаём название,
# адрес и район. Нужно в первую очередь агрегаторам (ТИЦ, НаДвиж), где
# собственного адреса площадки в карточке нет.
VENUES: list[tuple[str, str, str, str]] = [
    (r"ватутина,?\s*29", "Креативный центр «Башня»", "ул. Ватутина, 29/1", "Ленинский"),
    (r"свердлова,?\s*13", "Центр культуры ЦК19", "ул. Свердлова, 13", "Центральный"),
    (r"красный проспект,?\s*5\b", "Новосибирский художественный музей", "Красный проспект, 5", "Центральный"),
    (r"красный проспект,?\s*23", "Новосибирский краеведческий музей", "Красный проспект, 23", "Центральный"),
    (r"военная,?\s*5", "ТРЦ «Аура»", "ул. Военная, 5", "Центральный"),
    (r"ватутина,?\s*107", "ТРЦ «МЕГА»", "ул. Ватутина, 107", "Кировский"),
    (r"морской проспект,?\s*23", "Дом учёных СО РАН", "Морской проспект, 23", "Советский"),
    (r"советская,?\s*6\b", "Новосибирская областная научная библиотека", "ул. Советская, 6", "Центральный"),
    (r"советская,?\s*24", "Музей Новосибирска", "ул. Советская, 24", "Центральный"),
    (r"крылова,?\s*2\b", "Галерея KAPUSTACOR", "ул. Крылова, 2", "Центральный"),
    (r"ключ-камышенск", "Новосибирский планетарий", "Ключ-Камышенское плато, 1/1", "Октябрьский"),
    (r"коммунистическая,?\s*34", "НГОНБ, арт-платформа «Дом да Винчи»", "ул. Коммунистическая, 34", "Центральный"),
    # Площадки по названию: в текстах анонсов они стоят в косвенном падеже
    # («в Художественном музее»), поэтому ищем по основам, а не по точной строке.
    (r"художественн\w*\s+музе", "Новосибирский художественный музей", "Красный проспект, 5", "Центральный"),
    (r"краеведческ\w*\s+музе", "Новосибирский краеведческий музей", "Красный проспект, 23", "Центральный"),
    (r"дом\w*\s+народн\w*\s+творчества", "Областной Дом народного творчества", "ул. Каинская, 5", "Центральный"),
    (r"музей\w*\s+новосибирска", "Музей Новосибирска", "ул. Советская, 24", "Центральный"),
    (r"областн\w*\s+научн\w*\s+библиотек", "Новосибирская областная научная библиотека", "ул. Советская, 6", "Центральный"),
]

_KNOWN_VENUE_NAMES = (
    ("башня", "Креативный центр «Башня»", "ул. Ватутина, 29/1", "Ленинский"),
    ("цк19", "Центр культуры ЦК19", "ул. Свердлова, 13", "Центральный"),
    ("планетари", "Новосибирский планетарий", "Ключ-Камышенское плато, 1/1", "Октябрьский"),
    ("дом учёных", "Дом учёных СО РАН", "Морской проспект, 23", "Советский"),
    ("капустакор", "Галерея KAPUSTACOR", "ул. Крылова, 2", "Центральный"),
    ("нго́нб", "Новосибирская областная научная библиотека", "ул. Советская, 6", "Центральный"),
    ("нгонб", "Новосибирская областная научная библиотека", "ул. Советская, 6", "Центральный"),
)

_RE_STREET = re.compile(
    r"((?:ул\.|улица|проспект|пр-т|переулок|площадь|набережная|шоссе|проезд)"
    r"\s*[А-ЯЁ][А-ЯЁа-яё\-\s]{2,40})",
    re.IGNORECASE,
)
_RE_HOUSE = re.compile(r"\s*,?\s*(\d+[А-ЯЁа-яё0-9/\-]*)")
_RE_STREET_AFTER = re.compile(
    r"((?:[А-ЯЁ][а-яё\-]+\s){1,3}(?:проспект|улица|площадь|переулок|бульвар|шоссе|"
    r"набережная|проезд|тупик)\s*,?\s*\d+[А-ЯЁа-яё0-9/\-]*)"
)


def normalize_address(value: str) -> str:
    """Адрес в сравнимый вид: без города и района, «улица X» → «ул. X».

    Нужно, чтобы «Новосибирск, Кировский район, улица Ватутина, 107» и
    «улица Ватутина, 107» считались одним местом при поиске дублей.
    """
    if not value:
        return ""
    parts = [_WS.sub(" ", p).strip(" .") for p in value.split(",")]
    parts = [p for p in parts if p]
    cleaned: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _CITY_NAMES or low.startswith("г. "):
            continue
        if low.endswith("район") or low.endswith("р-н"):
            continue
        cleaned.append(part)
    if not cleaned:
        return ""

    def fix_street(chunk: str) -> str:
        words = chunk.split()
        if len(words) >= 2:
            if words[-1].lower() in ("улица", "ул"):
                return "ул. " + " ".join(words[:-1])
            if words[0].lower() in ("улица", "ул"):
                return "ул. " + " ".join(words[1:])
        return chunk

    if len(cleaned) >= 2 and not re.search(r"\d", cleaned[0]):
        cleaned[0] = fix_street(cleaned[0])
    return ", ".join(cleaned)


_CITY_NAMES = {
    "новосибирск", "бердск", "кольцово", "искитим", "сузун", "обь",
    "краснообск", "мочище", "маслянино", "коченево", "ордынское",
}
_STREET_WORDS = (
    "улица", "ул.", "проспект", "пр-т", "переулок", "проезд", "бульвар",
    "шоссе", "набережная", "площадь", "магистраль", "тракт",
)


def parse_address(value: str) -> dict:
    """Разбирает адрес вида «Новосибирск, Ленинский район, Garage, Советская улица, 23».

    Возвращает {"venue": место, "address": «ул. Советская, 23», "district": «Ленинский»}.
    Место есть не во всех карточках — тогда venue пустой.
    """
    result = {"venue": "", "address": "", "district": ""}
    if not value:
        return result

    parts = [_WS.sub(" ", p).strip(" .") for p in value.split(",")]
    parts = [p for p in parts if p]

    rest: list[str] = []
    for part in parts:
        low = part.lower()
        if low.endswith("район") or low.endswith("р-н"):
            district = part.split()[0].capitalize()
            if district in _DISTRICTS:
                result["district"] = district
            continue
        if low in _CITY_NAMES or low.startswith("г. "):
            continue
        rest.append(part)

    if not rest:
        return result

    street_idx = None
    for idx, part in enumerate(rest):
        if any(word in part.lower() for word in _STREET_WORDS):
            street_idx = idx
            break

    if street_idx is None:
        result["address"] = ", ".join(rest)
        return result

    if street_idx > 0:
        result["venue"] = ", ".join(rest[:street_idx])

    street = normalize_address(", ".join(rest[street_idx:street_idx + 2]))
    result["address"] = street or ", ".join(rest[street_idx:street_idx + 2])
    return result


def find_address(text: str) -> str:
    """Первый похожий на адрес фрагмент: «ул. Октябрьская, 81»."""
    if not text:
        return ""
    for m in _RE_STREET.finditer(text):
        street = _WS.sub(" ", m.group(1)).strip(" ,;.")
        house = _RE_HOUSE.match(text[m.end():m.end() + 14])
        if house:
            street = f"{street}, {house.group(1)}"
        if len(street) >= 8:
            return street

    # Второй порядок слов: тип улицы после названия («Красный проспект, 171/4»).
    # Первый шаблон такое не ловит: после «проспект» идёт запятая, а не название.
    for m in _RE_STREET_AFTER.finditer(text):
        street = _WS.sub(" ", m.group(1)).strip(" ,;.")
        if len(street) >= 8:
            return street
    return ""


def recognize_venue(*texts: str) -> dict:
    """Название площадки, адрес и район по известному адресу или упоминанию."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return {}
    for pattern, venue, address, district in VENUES:
        if re.search(pattern, blob):
            return {"venue": venue, "address": address, "district": district}
    for key, venue, address, district in _KNOWN_VENUE_NAMES:
        if key in blob:
            return {"venue": venue, "address": address, "district": district}
    return {}




def clean_title(value: str) -> str:
    """Заголовок: только пробелы и мусорные кавычки-обрезки.

    Отдельно от clean(), потому что clean() срезает навигационные префиксы
    («Афиша …», «Выставки …») — для заголовка это портит название:
    «Афиша джазовых концертов 2026» превращалась в «джазовых концертов 2026».
    """
    if not value:
        return ""
    title = _INVISIBLE.sub(" ", value).replace("\u00a0", " ")
    title = _WS.sub(" ", title).strip()
    title = re.sub(r"^[\s\-–—•·|/]+|[\s\-–—•·|/]+$", "", title)
    return re.sub(r"\s+([,.;:!?])", r"\1", title)


def clean(value: str) -> str:
    """Схлопывает пробелы и срезает навигационные префиксы."""
    if not value:
        return ""
    text = _WS.sub(" ", _INVISIBLE.sub(" ", value).replace("\u00a0", " ")).strip()
    for _ in range(6):
        stripped = _PREFIX.sub("", text).strip(" ,;:-–—•·|")
        if stripped == text:
            break
        text = stripped
    return text


def _starts_lowercase(sentence: str) -> bool:
    """Предложение начинается с маленькой буквы — это обрывок, а не фраза."""
    for char in sentence:
        if char.isalpha():
            return char.islower()
    return False


def shorten(text: str, sentences: int = 2, limit: int = 320) -> str:
    """До двух ёмких предложений (устав: «ИИ-очистка до двух предложений»)."""
    text = clean(text)
    if not text:
        return ""
    parts = split_sentences(text)
    # Вопрос и его хвост («…? — разберём это вместе!», после чистки — с
    # маленькой буквы) в карточке занимают место и обрываются на полуслове.
    # Берём их, только если других предложений не осталось.
    steady = [
        p for p in parts
        if p.rstrip().endswith((".", "!", "…")) and not _starts_lowercase(p)
    ]
    source = steady if steady else parts
    result = " ".join(source[:sentences]) if source else text
    if len(result) > limit:
        # Одно длинное предложение без точек внутри режется по живому:
        # если оно само уже содержательное, лучше остановиться на нём.
        first = source[0] if source else result
        if 100 <= len(first) <= limit:
            result = first
        else:
            cut = result[:limit]
            pos = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            result = (cut[: pos + 1] if pos > limit * 0.5 else cut.rsplit(" ", 1)[0]) + "…"
    return result


# Служебные предложения анонса: дата, время, цена и «для кого». В карточке
# всё это уже стоит отдельными полями (мета, цена, фильтры), поэтому в тексте
# только дублирует и отвлекает.
_MONTHS_WORD = (r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|"
                r"октябр|ноябр|декабр)\w*")
# «17 октября 1931 года» — дата из истории, её трогать нельзя: она часть текста.
_HISTORY_DATE = re.compile(
    rf"\b\d{{1,2}}\s+{_MONTHS_WORD}\s+((?:19|20)\d{{2}})\s*(?:год\w*|гг|г\.)\.?",
    re.IGNORECASE,
)
_SERVICE_RE = re.compile(
    rf"(?:"
    rf"\b\d{{1,2}}\s*(?:-|–|—|по)\s*\d{{1,2}}\s+{_MONTHS_WORD}\b(?:\s+\d{{4}}\s*(?:год\w*)?)?"  # 7–8 ноября 2026
    rf"|\b\d{{1,2}}\s+{_MONTHS_WORD}\s+\d{{4}}\s*(?:год\w*|гг|г\.)?"                          # 7 ноября 2026 года
    rf"|\b(?:с\s+)?\d{{1,2}}\s+{_MONTHS_WORD}\s*(?:по|-|–|—)\s*\d{{1,2}}\s+{_MONTHS_WORD}\b"  # 5–8 ноября
    rf"|\bс\s+\d{{1,2}}\s+по\s+\d{{1,2}}\s+{_MONTHS_WORD}\b"                                # с 5 по 8 ноября
    rf"|\b(?:с|по|от|до|в|во)\s+\d{{1,2}}\s+{_MONTHS_WORD}\b"                               # с 7 ноября
    rf"|\b\d{{1,2}}\s+{_MONTHS_WORD}\b"                                                     # 7 ноября
    rf"|\b{_MONTHS_WORD}\s+\d{{1,2}}\b"                                                     # ноября 7
    rf"|\b\d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{2,4}})?\b"                                       # 07.11.2026
    rf"|\b(?:с|до|в|во|около)\s+\d{{1,2}}\s*[:.]\s*\d{{2}}\b"                               # в 19:00
    rf"|\b\d{{1,2}}\s*[:.]\s*\d{{2}}\b"                                                     # 15:00
    rf"|\b(?:понедельник|вторник|сред[аыу]|четверг|пятниц[ауы]|суббот[ауы]|"
    rf"воскресень[яе]|пн|вт|ср|чт|пт|сб|вс)\b"
    rf"|\bкажд\w*\s+(?:суббот|воскресень|пятниц|понедельник|вторник|сред|четверг|недел)\w*"
    rf"|\b(?:ежедневно|ежедневн\w*|выходн\w*\s+дн\w*)\b"
    rf"|\b\d[\d\s]{{0,6}}\s*(?:₽|руб\.?|рублей|р\.)(?=[\s,.;!?]|$)"
    rf"|\b\d{{1,2}}\s*\+"                                                                   # 12+
    rf"|бесплатн|вход\s+свободн|билет\w*|стоимост\w*|\bцен[аы]\b|\bпо\s+цене\b"
    rf"|для\s+(?:детей|детям|детск\w*\s+сад\w*|взрослых|всей\s+семьи|семей|"
    rf"школьников|студентов|пенсионеров|малышей|дошкольников|родителей))",
    re.IGNORECASE,
)


# Предложения целиком про деньги: даже после вырезания сумм остаются огрызки
# вида «Сумма пожертвования 500 – (, студентов 300, пенсионеров.)».
_MONEY_SENT = re.compile(
    r"(пожертвован|льгот|\bбилет\w*|стоимост|₽|\bруб\w*|\bоплат\w*|\bплатн\w*|"
    r"бесплатн|\bцен[аы]\b|\bвзнос\b)",
    re.IGNORECASE,
)


def _strip_service(sentence: str) -> str:
    value = _SERVICE_RE.sub(" ", sentence)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,;:])\s*(?=[,;:.!?]|$)", "", value)
    value = re.sub(r"\b(?:в|во|с|до|от|на|по|и|а)\s+([,.;:!?]|$)", r"\1", value)
    return value.strip(" ,;:—-").strip()


def clean_announcement(raw: str, this_year: int | None = None) -> str:
    """Анонс без дат, времени, цен и «для кого» — то, что видно в карточке."""
    text = clean(raw)
    if not text:
        return ""
    if this_year is None:
        from datetime import date as _date

        this_year = _date.today().year

    # Исторические даты («родился 17 октября 1931 года») прячем под метку,
    # иначе чистка вырезает год и получается «родился 1931 года».
    saved: list[str] = []

    def _hide(match: re.Match) -> str:
        if int(match.group(1)) < this_year - 1:
            saved.append(match.group(0))
            return f"@@H{len(saved) - 1}@@"
        return match.group(0)

    text = _HISTORY_DATE.sub(_hide, text)

    kept: list[str] = []
    for sentence in split_sentences(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _MONEY_SENT.search(sentence):
            continue
        stripped = _strip_service(sentence)
        if len(stripped) < 25 or not re.search(r"[A-Za-zА-Яа-яЁё]", stripped):
            continue
        # почти всё предложение было служебным — оставшийся огрызок не нужен
        if len(stripped) < len(sentence) * 0.45:
            continue
        kept.append(stripped)
    result = re.sub(r"\s+", " ", " ".join(kept)).strip()
    # Если начало цитаты отбросили как служебное, текст начинается с обрывка
    # вроде «…истории по-новому» Выставка …». Тогда берём всё после кавычки,
    # но только если обрывок — это самое первое предложение.
    close = result.find("»")
    if 0 < close < 80 and "«" not in result[:close] and ". " not in result[:close]:
        tail = result[close + 1:].lstrip(" .,;:—–-")
        if len(tail) >= 40:
            result = tail
    for index, value in enumerate(saved):
        result = result.replace(f"@@H{index}@@", value)
    # Дату вырезали вместе с предлогом, и в начале осталось служебное слово:
    # «С 28 октября по 8 ноября включительно, гостям расскажут…» превращалось
    # в «Включительно, гостям расскажут…».
    result = re.sub(r"^(?:включительно|начиная|в\s+течение)\s*[.,;:—-]?\s*",
                    "", result, flags=re.IGNORECASE)
    if result and result[0].islower():
        result = result[0].upper() + result[1:]
    return result if len(result) >= 40 else ""


_PAGE_NOISE = re.compile(r"^(?:Все\s+[А-Яа-яЁё]+\s+)?(?:Поделиться\s+)?")
# Мета-описание каталогов вида «…в Новосибирске. Посмотреть афишу и узнать цену
# билетов можно на сайте kulturansk.ru» — это не анонс.
_OG_JUNK = re.compile(
    r"(узнать\s+цен|посмотреть\s+афиш|на\s+сайте\s+[a-zа-я.\-]+|подробнее\s+на|купить\s+билет)",
    re.IGNORECASE,
)


def page_description(url: str) -> str:
    """Абзац со страницы события — когда в карточке источника текста мало."""
    if not url or not url.startswith("http"):
        return ""
    from . import http

    html = http.get_text(url, ttl=24 * 3600)
    if not html:
        return ""
    import bs4

    soup = bs4.BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:description")
    og_text = clean(og.get("content", "")) if og and og.get("content") else ""
    if og_text and _OG_JUNK.search(og_text):
        og_text = ""

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    main = soup.select_one("main") or soup.body or soup

    best = ""
    blocks = main.select(".event-content, .article__text, .event-detail, article,"
                         " [itemprop='description'], .content")
    for block in blocks:
        value = _PAGE_NOISE.sub("", clean(block.get_text(" ", strip=True)))
        if len(value) > len(best) and not is_boilerplate(value):
            best = value
    if not best:
        for p in main.find_all("p"):
            value = clean(p.get_text(" ", strip=True))
            if len(value) >= 60 and len(value) > len(best) and not is_boilerplate(value):
                best = value
    best = best[:900]

    if len(og_text) >= 80 and len(og_text) > len(best) * 0.6:
        return og_text
    return best or og_text


def is_boilerplate(text: str) -> bool:
    low = text.lower()
    return any(b in low for b in _BOILERPLATE)


_PROMO_RE = re.compile(
    r"(скидк|акци[яию]\b|акций|распродаж|дисконт|промокод|кэшбэк|cashback|"
    r"подарочн\w*\s+карт|карта\s+лояльност|бонусн\w*\s+карт|дари\s+выбор|"
    r"выиграй|розыгрыш|конкурс\b|приз\b|"
    r"чёрн\w*\s+пятниц|черн\w*\s+пятниц|black\s*friday|шопинг\w*|"
    r"выгодн\w*\s+предложен|спецпредложен)",
    re.IGNORECASE,
)


def is_promo(title: str) -> bool:
    """Акции, скидки и карты лояльности — не события каталога."""
    return bool(title) and bool(_PROMO_RE.search(title))


# Спортивные соревнования в подборку «планы на выходные» не берём: это не
# культурный досуг, а турнир с расписанием поединков.
_SPORT_RE = re.compile(
    r"(турнир|чемпионат|первенств|соревнован|\bпо\s+борьбе\b|армрестлинг|"
    r"единоборств|боев\w*\s+искусств|"
    r"\bкубок\s+(?:мира|россии|сибири|новосибирска))",
    re.IGNORECASE,
)


def is_sport(title: str) -> bool:
    return bool(title) and bool(_SPORT_RE.search(title))


_DIGEST_START = re.compile(
    r"^(афиша|мероприятия\b|программа мероприятий\b|квесты\b|календарь|события\b|расписание|"
    r"выставки\b|концерты\b|октябрь|ноябрь|декабрь|январь|февраль|март|апрель|май|июнь|июль|август|сентябрь)",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    r"\b(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+20\d{2}\b",
    re.IGNORECASE,
)
_ANY_YEAR = re.compile(r"\b20\d{2}\b")
_SERVICE_TITLES = {
    "ближайшие события", "текущая выставка", "мероприятия", "архив", "концепция",
    "афиша", "события", "выставки", "концерты", "главная",
}


def is_digest(title: str) -> bool:
    """Дайджест-страница вместо события: «Афиша джазовых концертов 2026»,
    «Мероприятия в парках. Ноябрь 2026», «Программа мероприятий на октябрь»."""
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    if low in _SERVICE_TITLES:
        return True
    if "событий на" in low or "мероприятий на" in low:
        return True
    if _DIGEST_START.search(t) and (_MONTH_YEAR.search(t) or _ANY_YEAR.search(t)):
        return True
    # «Октябрь в парках: танцы под открытым небом» — тоже подборка
    if _DIGEST_START.search(t) and len(t) < 60 and not re.search(r"[«»\"']", t):
        return True
    return bool(_MONTH_YEAR.search(t)) and len(t) < 60


_OTHER_CITIES = (
    "томск", "кемерово", "барнаул", "омск", "красноярск", "новокузнецк",
    "бийск", "тюмень", "иркутск", "екатеринбург", "москва", "санкт-петербург",
)


def is_far_city(*texts: str) -> bool:
    """Событие не из Новосибирска и не из его пригородов."""
    blob = " ".join(t for t in texts if t).lower()
    if "новосибирск" in blob:
        return False
    return any(city in blob for city in _OTHER_CITIES)


def parse_price(*texts: str) -> tuple[str, bool]:
    """Возвращает (price_text, is_free). is_free — под фильтр «бесплатно».

    Цена важнее слова «бесплатно»: в анонсах часто есть «бесплатно для
    школьников» или «бесплатный вход по Пушкинской карте», хотя событие
    платное. Раньше проверка шла наоборот, и ЦК19 с билетом 300/400 руб.
    попадал в бесплатные.
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return "", False
    m = _PRICE_RE.search(blob)
    if m:
        amount = _WS.sub(" ", m.group(0)).strip()
        if len(amount) > 32:
            amount = amount[:32]
        return amount, False
    if _FREE_RE.search(blob):
        return "Бесплатно", True
    return "", False


_RE_MONEY = re.compile(r"\d[\d\s]*")


def price_label(value: str) -> str:
    """Единый вид цены: минимальная сумма и «от».

    Источники пишут по-разному — «300/400 руб.» (полный и льготный),
    «600–1000 ₽», «500 р.». В карточке нужна одна цифра, ниже которой
    билет не найти, поэтому берём минимум и ставим «от».
    """
    text = (value or "").strip()
    if not text:
        return ""

    amounts = []
    for chunk in _RE_MONEY.findall(text):
        digits = re.sub(r"\D", "", chunk)
        if digits:
            amounts.append(int(digits))
    amounts = [n for n in amounts if n > 0]
    if amounts:
        low = min(amounts)
        return f"от {low:,} ₽".replace(",", " ")
    # «Бесплатно» проверяем только когда цифр нет: «бесплатно для школьников,
    # взрослым 300 р.» — событие платное, цена важнее слова.
    if _FREE_RE.search(text):
        return "Бесплатно"
    return text


def age_limit(*texts: str) -> int | None:
    """Минимальный возрастной порог из текста: «0+», «6+», «18+»."""
    blob = " ".join(t for t in texts if t)
    ages = [int(x) for x in _AGE_RE.findall(blob)]
    return min(ages) if ages else None


def age_max(*texts: str) -> int | None:
    """Старшая возрастная метка: для маркировки нужен худший случай.

    В программе фестиваля фильмы идут с разными метками, и «12+» рядом с
    «18+» не делает показ детским.
    """
    blob = " ".join(t for t in texts if t)
    ages = [int(x) for x in _AGE_RE.findall(blob)]
    return max(ages) if ages else None


def infer_filters(title: str = "", text: str = "", hints: dict | None = None,
                  venue: str = "", age_text: str = "") -> dict:
    """Пять житейских меток устава + пара служебных.

    «Можно с детьми» — по умолчанию да, снимается на 16+/18+ и явных взрослых
    предупреждениях. «Для детей» — отдельная метка по явным признакам.
    «С животными» — только приглашение прийти с питомцем, а не событие про собак.
    `age_text` — возрастная метка из карточки источника («18+»), её в тексте
    анонса может и не быть.
    """
    blob = f"{title} {text} {age_text}"
    hints = hints or {}

    # Два разных вопроса: «можно ли прийти с ребёнком» (по умолчанию да,
    # снимается взрослыми метками) и «это событие для детей» (только по
    # явным признакам).
    age = age_limit(blob)
    # Если в программе есть «18+», с ребёнком туда не пойдёшь, даже когда
    # рядом стоят детские метки: берём не минимальный возраст, а худший.
    adult_18 = bool(re.search(r"(?<!\d)18\s*\+", blob))
    child_ok = (
        not adult_18
        and not (age is not None and age >= 16)
        and not _ADULT_ONLY.search(blob)
    )
    child_friendly = child_ok and (
        (age is not None and age <= 12)
        or bool(_CHILD_FOR.search(blob) and not _CHILD_ABOUT.search(blob))
    )

    pet_ok = bool(_PET_RE.search(blob)) and not _PET_NOT_ALLOWED.search(blob)

    flags = {
        "is_free": bool(hints.get("is_free", False)),
        "is_child_ok": child_ok,
        "is_child_friendly": child_friendly,
        "is_dog_ok": pet_ok,
        # «Прогулки и природа» — только по заголовку и только вне залов.
        # В описаниях слова «природа», «парк», «берег» встречаются в любом
        # музейном тексте, а площадка-парк часто сдаёт залы под мастер-классы,
        # поэтому описание и место давали в рубрику выставки и лекции.
        "is_nature": bool(_NATURE_RE.search(_PARK_BRAND.sub(" ", title)))
                     and not _INDOOR_RE.search(venue or ""),
        "is_outside_city": bool(_OUTSIDE_RE.search(blob)),
    }
    for key, value in hints.items():
        if key in flags and value:
            flags[key] = True
    return flags


def guess_district(*texts: str, strict: bool = False) -> str:
    """Район из адреса: явное «X район» → улица → название района в тексте.

    strict=True отключает последний шаг: в свободном тексте «советский»
    срабатывает на «советских художников», и событие в центре получает
    Советский район.
    """
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return ""

    m = _RE_DISTRICT_IN_ADDRESS.search(blob)
    if m:
        name = m.group(1).capitalize()
        if name in _DISTRICTS:
            return name

    for street, district in _STREETS.items():
        if street in blob:
            return district

    if strict:
        return ""

    for district, keys in _DISTRICTS.items():
        if any(k in blob for k in keys):
            return district
    return ""


def guess_area(*texts: str) -> str:
    """Новосибирск или конкретный пригород/областной город."""
    blob = " ".join(t for t in texts if t).lower()
    for key, area in _AREA_BY_CITY.items():
        if key in blob:
            return area
    return "Новосибирск"


def normalize_title(title: str) -> str:
    """Ключ для поиска одного события в разных источниках."""
    t = title.lower().replace("ё", "е")
    t = re.sub(r"«|»|\"|'|\(|\)|\[|\]", " ", t)
    t = re.sub(r"\b(выставка|выставки|лекция|концерт|спектакль|экскурсия|фестиваль|"
               r"мастер-класс|встреча|открытие|премьера|проект)\b", " ", t)
    t = re.sub(r"[^a-zа-я0-9 ]+", " ", t)
    t = _WS.sub(" ", t).strip()
    return t[:70]


def similar(left: str, right: str) -> float:
    """Похожесть двух строк, 0..1. Результат не зависит от порядка аргументов."""
    import difflib

    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        short, long_ = sorted((left, right), key=len)
        return 0.55 + 0.44 * (len(short) / max(len(long_), 1))
    # autojunk=False обязателен: на строках длиннее 200 символов difflib
    # начинает считать частые символы «мусором», и похожесть перестаёт быть
    # симметричной — 0.5 в одну сторону и 0.06 в другую.
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


_MONTH_WORDS = {
    "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
    "сентябрь", "октябрь", "ноябрь", "декабрь",
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря",
}


def distinct_tokens(normalized: str) -> frozenset[str]:
    """Значимые различители в названии: числа и месяцы.

    «Программа на октябрь» и «Программа на ноябрь» — разные события, хотя
    по буквам совпадают на 90%.
    """
    if not normalized:
        return frozenset()
    numbers = {n for n in re.findall(r"\d{1,4}", normalized)}
    months = {w for w in normalized.split() if w in _MONTH_WORDS}
    return frozenset(numbers | months)
