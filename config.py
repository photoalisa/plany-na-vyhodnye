"""Реестр источников и общие настройки запуска."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str        # короткое имя для колонки source
    module: str      # parsers.xxx
    domain: str
    category: str    # по умолчанию для карточек без явной категории
    venue: str = ""
    district: str = ""
    # Чем меньше число, тем «первичнее» источник. При дубле одного события
    # в таблицу попадает карточка с наименьшим prio; ТИЦ и НаДвиж — агрегаторы,
    # они дополняют каталог, но не перекрывают первоисточники.
    prio: int = 50


SOURCES: list[Source] = [
    Source("ЦК19", "parsers.cc19", "cc19.ru", "выставка", "Центр культуры ЦК19", "Центральный", 10),
    Source("Художественный музей", "parsers.nsartmuseum", "nsartmuseum.ru", "выставка", "НГХМ", "Центральный", 20),
    Source("Краеведческий музей", "parsers.youmuseum", "youmuseum.ru", "выставка", "Краеведческий музей", "Центральный", 20),
    Source("Музей Новосибирска", "parsers.museum_nsk", "m-nsk.ru", "выставка", "Музей Новосибирска", "Центральный", 25),
    Source("Планетарий", "parsers.nebo", "nebo-nsk.ru", "лекция", "Новосибирский планетарий", "Октябрьский", 30),
    Source("НГОНБ", "parsers.ngonb", "ngonb.ru", "лекция", "Областная научная библиотека", "Центральный", 30),
    Source("Дом учёных", "parsers.dusoran", "dusoran.ru", "выставка", "Дом учёных СО РАН", "Советский", 30),
    Source("Моя история", "parsers.myhistory", "novosibirsk.myhistorypark.ru", "выставка",
           "Исторический парк «Россия — моя история»", "Октябрьский", 35),
    Source("Башня", "parsers.bashnya", "bashnya-site.ru", "встреча", "Креативный центр «Башня»", "Ленинский", 35),
    Source("Студия 84", "parsers.artweeknd", "artweeknd.com", "выставка", "Студия 84", "Центральный", 35),
    Source("Капустакор", "parsers.kapustacor", "kapustacor.ru", "выставка", "Галерея KAPUSTACOR", "", 35),
    Source("Культура НСК", "parsers.kulturansk", "kulturansk.ru", "событие", "", "", 40),
    Source("Аура", "parsers.aura", "aura.planeta-mall.ru", "событие", "ТРЦ «Аура»", "Центральный", 50),
    Source("МЕГА", "parsers.mega", "mega.ru", "событие", "ТРЦ «МЕГА»", "Кировский", 50),
    Source("Парки", "parsers.parkigoroda", "parkigoroda-nsk.ru", "событие", "", "", 45),
    Source("Искитимский музей", "parsers.iskmuseum", "iskmuseum.ru", "выставка",
           "Искитимский городской историко-художественный музей", "", 42),
    Source("Гастрокорт", "parsers.gastronom", "gastro-nsk.ru", "событие",
           "Гастрокорт", "Центральный", 60),
    # Агрегаторы: только то, чего нет у первоисточников.
    Source("ТИЦ", "parsers.welcome_tic", "welcome-novosibirsk.ru", "событие", "Туристско-информационный центр", "Центральный", 80),
    Source("НаДвиж", "parsers.nadvizh", "nadvizh.ru", "событие", "", "", 90),
]

PARALLEL_SOURCES = 4      # сколько парсеров крутим одновременно
PARALLEL_PAGES = 6        # сколько карточек читаем параллельно внутри парсера
CHECK_IMAGES = True       # HEAD-проверка ссылок на афиши

# Обогащение с билетного сайта kassy.ru: каталог не расширяем, только добираем
# описание, афишу и цену событиям, которые уже нашли сами. Это 9 запросов
# листингов плюс страница события на каждое совпадение.
ENRICH_KASSY = True

# Нечёткое сравнение названий при поиске дублей.
DUPLICATE_TITLE_RATIO = 0.72   # 0..1, выше — строже
DUPLICATE_DAY_WINDOW = 14      # насколько даты могут расходиться, дней
DUPLICATE_VENUE_RATIO = 0.25   # порог совпадения слов, если площадка одна и та же

# Стоп-слова: заголовки, которые в каталог «планы на выходные» не берём.
# По умолчанию пусто: ручная чистка теперь запоминается в листе `hidden`
# (см. common/hidden.py), поэтому срезать целые категории не нужно.
# Пример, если понадобится: ("афтепати", "afterparty", "дискотек").
EXCLUDE_WORDS: tuple[str, ...] = ()
