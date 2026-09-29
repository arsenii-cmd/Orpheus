"""What the program does by itself: the scenarios of intents_ru.txt, one do_<name> method each.

A scenario gets the match (its slots) and the time, and returns what to say (str; "" is to stay
silent), facts for the model to answer from (Context), or None - "not mine after all": then the
next matching scenario is tried, and at last the model. The model takes 3-8 s on this laptop and
gets dates, sums and its own tool calls wrong now and then; these take milliseconds.
"""

import dataclasses
import random
import re
from datetime import date, datetime, timedelta

import numpy as np

from . import calc
from .intents import Intents
from .memory import RECALL_MARGIN
from .numbers import plural, to_digits
from .web import Offline
from .planner import (ABOUT_DATE, ADD_WORDS, DAY_WORDS, MONTHS, NO, WEEKDAYS, WHEN, YES, Note, Unavailable, _has_day,
                      _has_time, _just_listing, _say, _stems, _strip, clean_quick, period, shift_of, spoken_day, spoken_item,
                      tidy_title, time_span as planner_time_span)


class Context(str):
    """Facts for the model to answer from ("есть ли завтра русский в 10?"), put next to the phrase.
    [tool]: (name, arguments) when it is what one of the model's tools would have found (web_search):
    then it goes into the history as that call and its result, and the model does not search again."""

    tool = None
    tried = False  # a search tried and failed: not to be made once more for the model's "не могу найти"


def found(text, tool, **arguments):
    context = Context(text)
    context.tool = (tool, arguments)
    return context


class Lookup:
    """A scenario that has to go out to the internet: [prelude] is said at once ("Сейчас поищу."),
    then fetch() - seconds over the network - gives what to say (str), facts for the model
    (Context), or None (the model's after all)."""

    def __init__(self, prelude, fetch):
        self.prelude = prelude
        self.fetch = fetch


# In «Личное» the small talk is the model's: warm words, not canned ones.
SMALL_TALK = {"greet", "thanks", "bye", "how_are_you", "ack", "who"}
# never one of two requests in a phrase: "Привет! Какая погода?" is the weather
NOT_A_HALF = SMALL_TALK | {"repeat", "capabilities", "reset"}
WEATHER = {"weather", "weather_degrees", "weather_rain", "weather_clothes"}
WEATHER_WORDS = re.compile(r"погод|дожд|снег|зонт|градус|ветер|ветр|холодн|тепл|жар|одеться|облачн|солнц|влажн|гроз|туман", re.I)
# the weather's questions after which "а там дождь будет?", "а ветер?" keep the city
WEATHER_ALL = WEATHER | {"weather_wind", "weather_humidity", "weather_sun"}
# the scenarios a short "а завтра?", "а в Сочи?" goes on from: the same question, another day or city
FOLLOWED = WEATHER | {"weather_wind", "weather_humidity", "time_in", "time_until", "plan_ask", "plan_next", "date_of", "days_until", "year_month", "rate", "web_search", "fresh"}
FOLLOW_START = re.compile(r"^\W*(?:(?:а|ага|угу|ну|и|так|тогда|ладно|хорошо|окей|понятно|ясно)\W+)*(?:(?:как|что)\s+(?:насч[её]т|по\s+поводу|там\s+с|с)"
                          r"\s+|что\s+(?:там\s+)?|как\s+(?:там\s+)?)?", re.I)
# words that may stand around the new day or city: "а что там на выходных?", "а в Сочи будет?"
FOLLOW_WORDS = {"а", "и", "ну", "что", "как", "там", "тогда", "будет", "будут", "было", "на", "в", "во", "про", "по",
                "поводу", "насчет", "с", "тоже", "еще", "же", "вообще"}
FOLLOW_WORDS_PLANS = FOLLOW_WORDS | {"у", "меня", "нас", "есть", "планы", "план", "плану", "дела", "какие"}
FOLLOW_WORDS_WEATHER = FOLLOW_WORDS  # "какая погода в Сочи?" is a question of its own, not "а в Сочи?"
# a day as said, with what goes before it: "на эту субботу", "в понедельник", "через 2 дня", "завтра"
DAY_EXPR = re.compile(r"(?<!\w)(?:(?:на|в|во|к|до|с|через|эт\w+|следующ\w+|ближайш\w+|будущ\w+)\s+)*\w*(?:%s)\w*" % DAY_WORDS,
                      re.I)
# where one request ends and another begins: "что у меня завтра и какая погода?", "…? А …"
SPLIT = re.compile(r"\s*(?:,?\s+(?:и|а\s+также|а\s+ещ[её]|и\s+ещ[её]|а\s+заодно|и\s+заодно|плюс)|[;.?!]|,\s+а)\s+", re.I)
HESITATION = re.compile(r"^\W*(?:(?:э+|м+|хм+|эм+|гм+|мм+)\W*)+$", re.I)
# "перенеси его", "удали это", "удали второе", "а последнее перенеси"
PRONOUN = re.compile(r"^(?:его|ее|её|это|этот|эту|эта|то|тот|ту|него|нее|неё)(?:\s+(?:же|событие|дело|задачу|занятие|встречу|"
                     r"мероприятие|запись|напоминание))?$", re.I)
ORDINAL = re.compile(r"^(перв|втор|трет|четв[её]рт|пят|предпоследн|последн)\w*(?:\s+(?:событие|дело|задачу|занятие|встречу|"
                     r"мероприятие|запись|напоминание|из них))?$", re.I)
ORDINALS = {"перв": 0, "втор": 1, "трет": 2, "четверт": 3, "четвёрт": 3, "пят": 4, "предпоследн": -2, "последн": -1}
NOT_A_NAME = {"на", "в", "во", "к", "с", "домой", "обратно", "туда", "сюда", "гулять", "все", "всё", "так", "как", "никак",
              "по", "за", "из", "меня", "тебя", "его", "ее", "её", "они", "уже", "опять", "снова", "работать", "ужинать"}
# "каждую неделю", "по средам", "каждый месяц": a series in the Planner (it repeats by weeks and months only)
REPEAT_WEEK = re.compile(r"(?<!\w)(?:каждую\s+неделю|еженедельно|раз\s+в\s+неделю)(?!\w)", re.I)
REPEAT_MONTH = re.compile(r"(?<!\w)(?:каждый\s+месяц|ежемесячно|раз\s+в\s+месяц)(?!\w)", re.I)
# "по понедельникам, средам и пятницам", "каждый вторник и четверг", "по будням", "каждый день": the days of a
# weekly repeat, each its own series in the Planner (it repeats by weeks and months only)
DAY_STEMS = ["понедельн", "вторн", "сред", "четверг", "пятниц", "суббот", "воскресен"]
# "сред" only as a day: "в среду", "по средам", not "в среднем зале" or "среди дня"
DAY_WORD = r"(?:понедельн\w*|вторн\w*|сред(?:а|у|ы|е|ам|ами|ах)(?!\w)|четверг\w*|пятниц\w*|суббот\w*|воскресен\w*)"
# between the days: a comma, "и", or nothing at all (the recogniser drops commas: "по понедельникам средам и пятницам")
DAY_SEP = r"(?:\s*,\s*(?:и\s+)?|\s+и\s+|\s+)(?:по\s+|в\s+|во\s+)?"
MULTI_DAYS = re.compile(r"(?<!\w)(?:по|каждый|каждую|каждое|каждые)\s+%s(?:%s%s)*" % (DAY_WORD, DAY_SEP, DAY_WORD), re.I)
# "в понедельник, среду и пятницу": several days once each (a repeat only with "каждую неделю" or "10 недель")
SEVERAL_DAYS = re.compile(r"(?<!\w)(?:в|во)\s+%s(?:%s%s)+" % (DAY_WORD, DAY_SEP, DAY_WORD), re.I)
EVERY_DAY = re.compile(r"(?<!\w)(?:каждый\s+день|ежедневно|каждое\s+утро|каждый\s+вечер|каждую\s+ночь)(?!\w)", re.I)
WEEKDAYS_ONLY = re.compile(r"(?<!\w)(?:по\s+будням|по\s+рабочим\s+дням|в\s+будни)(?!\w)", re.I)
WEEKENDS_ONLY = re.compile(r"(?<!\w)(?:по\s+выходным|в\s+выходные)(?!\w)", re.I)
# "с повтором 10 недель", "на 10 недель", "10 раз", "в течение 3 месяцев"
REPEAT_COUNT = re.compile(r"(?<!\w)(?:(?:с\s+)?повтор\w*\s+|на\s+|в\s+течение\s+)?(\d+)\s*(недел\w*|раз\w*|месяц\w*)(?!\w)", re.I)
DAYS_DAT = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
DAYS_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]


# what follows "удали/отмени" and is no planner item: a change of mind, a thought (the undo's or the model's)
NOT_PLANS = re.compile(r"(?<!\w)(?:я|мы)\s+(?:уже\s+)?передумал\w*|(?<!\w)передумал\w*|из\s+головы|(?<!\w)забудь(?!\w)|"
                       r"^\W*(?:я|мы|ты)\s+\w+л[аи]?(?!\w)", re.I)
# "Напомни, какое сегодня число": a question, not a reminder (a task «Какое число» was added)
REMIND_ASKS = re.compile(r"^\W*(?:напомни|напомните)(?:\s+мне)?[\s,:—-]+(?=(?:(как\w*|кто|кого|кому|где|куда|когда|сколько|почему|"
                         r"зачем|чей|чья|чье|чьи|во\s+сколько|о\s+ч[её]м)|что|чт[оа]|про|об?|чем)(?!\w))", re.I)
# "напомни, что я хотел на выходных": what the speaker said or meant before - a question about it, a day named or not
# (it became a task «Я хотел на выходных»)
REMIND_PAST = re.compile(r"^\W*(?:напомни|напомните)(?:\s+мне)?[\s,:—-]+(?:что|о\s+ч[её]м|про\s+что)\s+(?:я|мы)\s+(?:\w+\s+)?"
                         r"(?:хотел|говорил|собирал|думал|планировал|рассказывал|обещал|решил|придумал|спрашивал)\w*", re.I)
# said as it is spoken: "че у меня на завтра", "сколько щас времени"
COLLOQUIAL = [(re.compile(r"(?<!\w)(?:че|чё|чо|шо)(?!\w)", re.I), "что"), (re.compile(r"(?<!\w)щас(?!\w)", re.I), "сейчас"),
              (re.compile(r"(?<!\w)скок[оа]?(?!\w)", re.I), "сколько"), (re.compile(r"(?<!\w)ваще(?!\w)", re.I), "вообще")]


# said in passing, not a part of a title: "добавь, короче, …", "типа встречу"
SPOKEN_FILLERS = re.compile(r"(?<!\w)(?:короче|типа|как\s+бы|это\s+самое|в\s+общем|значит|э+м*|ну\s+вот|вот)(?!\w)", re.I)
# an event rather than a thing to do: without a day or time, asked "на когда?"
EVENT_NOUN = re.compile(r"^\W*(?:встреч\w*|созвон\w*|звонок|тренировк\w*|заняти\w*|урок\w*|собеседовани\w*|при[её]м\w*|"
                        r"концерт\w*|кино|спектакл\w*|вебинар\w*|лекци\w*|семинар\w*|бассейн\w*|пробежк\w*|стрижк\w*|"
                        r"поход\w*|ужин\w*|обед\w*|свидани\w*|вечеринк\w*)(?:\s|$)", re.I)
# said with a plan: "задачу", "в календарь", "напоминание"
PLAN_MARKED = re.compile(r"(?<!\w)(?:задач\w*|дело|дела|напоминани\w*|событи\w*|в\s+(?:план|планы|календарь|планировщик|"
                         r"расписание|список\s+дел))(?!\w)", re.I)
# what a plan looks like without a day or time: something to do ("купить хлеб", "позвонить маме") or an event
PLAN_LIKE = re.compile(r"^\W*(?:\w+(?:ть|чь)(?:ся|сь)?|\w*(?:нес|вез|вес|брес|пас|полз|ид|й)ти(?:сь)?)(?!\w)|(?<!\w)(?:встреч|созвон|звон[окк]|тренировк|заняти|урок|собеседован|"
                       r"при[её]м|врач|стоматолог|день\s+рожден|экзамен|контрольн|пробник|дедлайн|отч[её]т|презентац|поездк|"
                       r"рейс|концерт|кино|спектакл|вебинар|лекци|семинар|репетитор|бассейн|пробежк|зарядк|йог|массаж|"
                       r"стрижк|парикмахер|маникюр|уборк|оплат|плат[её]ж|поход|свидани|вечеринк|праздник|ужин|обед|завтрак)", re.I)


def spoken(text):
    for pattern, word in COLLOQUIAL:
        text = pattern.sub(word, text)
    return text


# a day and a time and nothing else: "в 8", "на завтра в 15:30", "в пятницу вечером", "на полчаса позже"
_WHEN_WORD = (r"(?:на|в|во|к|с|до|к\s+\d|через|сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред[ауеы]|четверг\w*|пятниц[ауеы]|"
              r"суббот[ауеы]|воскресень[еяю]|утр[аоом]\w*|дн[яем]|днем|вечер\w*|ноч\w*|полдень|полночь|час\w*|минут\w*|полчаса|"
              r"половин\w*|пол\w+|без|четверти|раньше|позже|попозже|пораньше|вперед|назад|следующ\w+|недел\w+|день|дня|"
              r"\d{1,2}(?::\d{2})?|\d{1,2}[./]\d{1,2}|январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|август\w*|"
              r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|лучше|давай|пусть|нет|ну|тогда|же|а)")
WHEN_ONLY = re.compile(r"%s(?:\s+%s)*" % (_WHEN_WORD, _WHEN_WORD))


# the start of a correction: "нет", "не, ", "ой", "блин", "лучше", and "не в 12, а" - what it is not
CORRECTION = re.compile(r"^\W*(?:(?:нет|не(?!\s+(?:в|на|к)\s)|ой|блин|лучше|давай|хотя|точнее|вернее|стоп|упс|а|и)(?!\w)\W*)*"
                        r"(?:не\s+(?:в|на|к)\s+[\w:]+(?:\s+(?:час\w*|утра|вечера|дня))?\W+(?:а\s+)?)?", re.I)


def when_only(text):
    """Is it only a day and a time ("Завтра в 12.", "на 15:30", "в пятницу вечером")?"""
    words = re.sub(r"[^\w\s:./]|(?<!\d)[./]|[./](?!\d)", " ", to_digits(text).lower().replace("ё", "е"))
    words = " ".join(words.split())
    return bool(words) and WHEN_ONLY.fullmatch(words) is not None


def repeat_days(text):
    """The weekdays (0 = Monday) a repeat names, and the text without them; None when it names none."""
    if EVERY_DAY.search(text):
        return list(range(7)), EVERY_DAY.sub(" ", text)
    if WEEKDAYS_ONLY.search(text):
        return list(range(5)), WEEKDAYS_ONLY.sub(" ", text)
    if WEEKENDS_ONLY.search(text):
        return [5, 6], WEEKENDS_ONLY.sub(" ", text)
    m = MULTI_DAYS.search(text)
    if not m:
        return None, text
    return _days_in(m.group(0)), text[:m.start()] + " " + text[m.end():]


def several_days(text):
    """"в понедельник, среду и пятницу" -> ([0, 2, 4], the text without them), or (None, text)."""
    m = SEVERAL_DAYS.search(text)
    if not m:
        return None, text
    return _days_in(m.group(0)), text[:m.start()] + " " + text[m.end():]


def _days_in(said):
    return sorted({i for w in re.findall(DAY_WORD, said, re.I) for i, s in enumerate(DAY_STEMS) if w.lower().startswith(s)})


def days_said(days):
    if days == list(range(7)):
        return "каждый день"
    if days == list(range(5)):
        return "по будням"
    if days == [5, 6]:
        return "по выходным"
    names = [DAYS_DAT[d] for d in days]
    return "по " + (names[0] if len(names) == 1 else "%s и %s" % (", ".join(names[:-1]), names[-1]))


ALL_OF = re.compile(r"^(?:вс[её]|весь|все\s+(?:дела|планы|события|задачи|встречи|занятия|мероприятия|записи))(?!\w)", re.I)
# "кто был первым президентом России": a fact to look up, not an article to read out
SPECIFIC = re.compile(r"(?<!\w)(?:перв\w*|последн\w*|нынешн\w*|текущ\w*|сейчас|теперь|самы\w+|главн\w*|действующ\w*|"
                      r"бывш\w*|следующ\w*|предыдущ\w*)(?!\w)", re.I)
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь"]
SEASONS = ["зима", "зима", "весна", "весна", "весна", "лето", "лето", "лето", "осень", "осень", "осень", "зима"]
HOLIDAYS = [  # (words, month, day, how to say it after "до")
    (r"нов\w*\s+год|нг\b", 1, 1, "Нового года"),
    (r"рождеств", 1, 7, "Рождества"),
    (r"8\s+марта|восьмого\s+марта|женск\w+\s+д", 3, 8, "8 марта"),
    (r"23\s+февраля|дня\s+защитника", 2, 23, "23 февраля"),
    (r"9\s+мая|дня\s+победы", 5, 9, "9 мая"),
    (r"1\s+сентября|дня\s+знаний", 9, 1, "1 сентября"),
    (r"лета\b", 6, 1, "лета"),
    (r"осени\b", 9, 1, "осени"),
    (r"зимы\b", 12, 1, "зимы"),
    (r"весны\b", 3, 1, "весны"),
]
# "погода в Сочи", "в Нижнем Новгороде": a capitalized name after "в" (the recogniser writes cities so)
CITY = re.compile(r"(?<!\w)(?:в|во)\s+((?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)(?:\s+[А-ЯЁ][а-яё]+)?)")
# ... and written small: "какая погода в питере" (tried, and the home city when there is none such)
LOWER_CITY = re.compile(r"(?<!\w)(?:в|во)\s+([а-яё]{3,}(?:-[а-яё]+)*)", re.I)
NOT_CITY = {"что", "чем", "чего", "том", "котором", "которой", "общем", "городе", "центре", "области", "стране", "мире", "доме", "квартире", "офисе", "парке", "лесу", "горах", "поле",
            "итоге", "целом", "общем", "принципе", "сколько", "какой", "какую", "каком", "какие", "течение", "среднем",
            "основном", "обед", "обеда", "субботу", "среду", "пятницу", "выходные", "будни", "утра", "вечера", "ночи",
            "дня", "полдень", "полночь", "интернете", "сети", "гугле", "яндексе", "заметках", "планах", "календаре"}


# the parts of a day the hourly forecast is read for: (word heard, said, first hour, last hour, days on);
# "ночью" is the night coming after that day
PARTS_OF_DAY = [("утр", "утром", 6, 12, 0), ("дн[её]м", "днём", 12, 18, 0), ("вечер", "вечером", 18, 24, 0),
                ("ноч", "ночью", 0, 6, 1)]


PART_EXPR = re.compile(r"(?<!\w)(?:утр(?:ом|а)|дн[её]м|вечер(?:ом|а)|ноч(?:ью|и))(?!\w)", re.I)


def _city(text, lower=True):
    """The city a phrase names, as a match whose group 1 is the name: "в Сочи", "в питере"."""
    for m in CITY.finditer(text):
        if not DAY_EXPR.fullmatch(m.group(0)):
            return m
    if lower:
        for m in LOWER_CITY.finditer(text):
            if m.group(1).lower() not in NOT_CITY and not DAY_EXPR.fullmatch(m.group(0)):
                return m
    return None


QUESTION = re.compile(r"\?|(?<!\w)(?:кто|что|где|когда|сколько|как|какой|какая|какое|какие|каков\w*|почему|зачем|чей|чья|ли)(?!\w)", re.I)
# "Выясни, кто …", "найди в интернете …": the words asking for a search are not what to search for
ASKING = re.compile(r"^\W*(?:(?:а|ну|слушай|орфей)\W+)*(?:выясни|узнай|разузнай|найди|поищи|посмотри|проверь|глянь|подскажи|скажи|расскажи|покажи)"
                    r"(?:\s+(?:мне|пожалуйста))*(?:\s+в\s+(?:интернете|сети))?[\s,:]+", re.I)
# after a search, "а сколько она будет стоить?" is about the same thing: searched again, with it
ABOUT_THAT = re.compile(r"(?<!\w)(?:он|она|оно|они|его|её|ее|их|ему|ей|им|нему|ней|них|этот|эта|это|эти|этого|этой|этих|"
                        r"там|тот|та|те|того|той)(?!\w)", re.I)
TO_ME = re.compile(r"(?<!\w)(?:ты|тебя|тебе|тобой|твой|твоё|твое|твоя|твои|думаешь|считаешь|посоветуешь)(?!\w)", re.I)


# (told only "не называй того, что меняется каждый день", it said "Президент США — Джо Байден" and "GTA 6 — в 2025
# году" from a memory years old: what may have changed since is not said at all)
# (and with the failure in its hands, it said "поиск показывает, что…": the program says it itself first)
SEARCH_DOWN = ("поиск в интернете не ответил, и это уже сказано вслух; не пиши, что поиск что-то показал. Давно известное "
               "(история, наука, книги) — ответь по своим знаниям одним предложением; то, что могло измениться за последние "
               "годы (кто сейчас на должности, даты выхода, цены, курсы, счёт, новости), не называй — скажи только, что "
               "проверить это сейчас не получится")


# "Забавно. Расскажи последние новости…": a reaction to the last answer, then the request - the reaction alone
# made the phrase no scenario's (the model answered, and searched nothing)
REACTION = re.compile(r"^\W*(?:(?:ну|очень|как|вот)\s+)?(?:забавно|интересно|понятно|ясно|круто|класс|классно|здорово|ого|вау|"
                      r"прикольно|отлично|супер|понял|поняла|спасибо|благодарю|жаль|обидно|смешно|любопытно|неплохо|"
                      r"вот\s+как|вот\s+оно\s+что)[.!…]+\s+(?=\w)", re.I)
# "какие новости?" - "Блокировки VPN.": the topic alone, after news: the news about it
NEWS = re.compile(r"(?<!\w)новост", re.I)


# plans that are outdoors: the morning's rain warning names them
OUTDOOR = re.compile(r"пробежк|прогулк|(?<!\w)бег(?!\w)|футбол|баскетбол|велосипед|велик|пикник|поход|шашлык|рыбалк|дач|парк|"
                     r"забег|гулять|погулять|самокат|ролик|на\s+улиц|стадион|пляж", re.I)
PART_OF_DAY = re.compile(r"(?<!\w)(?:с\s+)?(утром|утра|вечером|днём|днем)(?!\w)(?!\s+(?:в|к)\s+\d)", re.I)
PART_TIME = {"утр": "9:00", "веч": "19:00", "днё": "13:00", "дне": "13:00"}


# the owner's own words, said back to them: "Мой лучший друг Костя" -> "твой лучший друг Костя"
YOU_WORDS = {"я": "ты", "мне": "тебе", "меня": "тебя", "мной": "тобой", "мой": "твой", "моя": "твоя", "мое": "твоё", "моё": "твоё",
             "мои": "твои", "моего": "твоего", "мою": "твою", "моей": "твоей", "моему": "твоему", "моих": "твоих", "моим": "твоим",
             "люблю": "любишь", "хочу": "хочешь", "живу": "живёшь", "учусь": "учишься", "хожу": "ходишь", "сдаю": "сдаёшь",
             "ем": "ешь", "пью": "пьёшь", "могу": "можешь", "родился": "родился", "родилась": "родилась", "ненавижу": "ненавидишь",
             "обожаю": "обожаешь", "болею": "болеешь", "пою": "поёшь", "учу": "учишь", "рисую": "рисуешь", "танцую": "танцуешь"}


def to_you(fact):
    """A saved fact (the owner's words) said to the owner: pronouns and the first-person verbs turned."""
    def turn(m_):
        w = m_.group(0)
        low = w.lower()
        out = YOU_WORDS.get(low)
        if out is None and re.fullmatch(r"\w{3,}(?:аю|яю|ею)", low):
            out = low + "шь" if False else low[:-1] + "ешь"  # "играю" -> "играешь"
        if out is None and re.fullmatch(r"\w{3,}(?:аюсь|яюсь)", low):
            out = low[:-3] + "ешься"  # "занимаюсь" -> "занимаешься"
        return out if out is not None else w
    said = re.sub(r"\w+", turn, fact)
    return said[:1].lower() + said[1:] if not re.match(r"[А-ЯЁ]{2}", said) else said


def search_query(text):
    return ASKING.sub("", text).strip(" ?.!,") or text.strip(" ?.!,")


def found_online(query, results):
    # three results, short: every token here is read by the model before it says a word
    lines = "; ".join("%d) %s — %s (%s)" % (i, r.title[:80], r.snippet[:150], r.site) for i, r in enumerate(results[:3], 1))
    return ("найдено в интернете по запросу «%s»: %s. Это свежее, чем твоя память: где они расходятся, верно найденное. "
            "Ответь одним-двумя короткими предложениями только по этому и назови сайт; чисел и дат, которых здесь нет, не называй — тогда скажи, что точных данных нет, и где смотреть"
            % (query, lines))


# ------------------------------------------------------------------------ exchange rates (ЦБ)

# the currency's name in any case -> its code, its forms after a number (1, 2-4, 5+) and the name alone
CURRENCIES = [
    (r"доллар\w{0,3}|бакс\w{0,3}", "USD", ("доллар", "доллара", "долларов"), "доллар"),
    (r"евро", "EUR", ("евро", "евро", "евро"), "евро"),
    (r"юан[ьяюейи]\w{0,2}", "CNY", ("юань", "юаня", "юаней"), "юань"),
    (r"фунт\w{0,3}(?:\s+стерлинг\w{0,3})?", "GBP", ("фунт", "фунта", "фунтов"), "фунт"),
    (r"[иий]ен[аыуе]?|[иий]еной", "JPY", ("иена", "иены", "иен"), "иена"),
    (r"(?:швейцарск\w+\s+)?франк\w{0,2}", "CHF", ("франк", "франка", "франков"), "франк"),
    (r"(?:турецк\w+\s+)?лир[аыуе]?|лирой", "TRY", ("лира", "лиры", "лир"), "лира"),
    (r"тенге", "KZT", ("тенге", "тенге", "тенге"), "тенге"),
    (r"белорусск\w+\s+рубл\w{0,2}", "BYN", ("белорусский рубль", "белорусских рубля", "белорусских рублей"), "белорусский рубль"),
    (r"гривн[аыуе]?|гривной|гривен", "UAH", ("гривна", "гривны", "гривен"), "гривна"),
    (r"дирхам\w{0,2}", "AED", ("дирхам", "дирхама", "дирхамов"), "дирхам"),
    (r"драм[аыу]?|драмов", "AMD", ("драм", "драма", "драмов"), "драм"),
    (r"лари", "GEL", ("лари", "лари", "лари"), "лари"),
    (r"злот\w{1,3}", "PLN", ("злотый", "злотых", "злотых"), "злотый"),
    (r"рупи[яиейю]\w{0,2}", "INR", ("рупия", "рупии", "рупий"), "рупия"),
    (r"бат[аыу]?|батов", "THB", ("бат", "бата", "батов"), "бат"),
    (r"сом[аыу]?|сомов", "KGS", ("сом", "сома", "сомов"), "сом"),
]
CURRENCY_NAME = "|".join("(%s)" % c[0] for c in CURRENCIES)
CURRENCY = re.compile(r"(?<!\w)(?:%s)(?!\w)" % CURRENCY_NAME, re.I)
AMOUNT = r"(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(тыс\w*|млн|миллион\w*)?"
COUNTED = re.compile(AMOUNT + r"\s*(?:%s)(?!\w)" % CURRENCY_NAME, re.I)  # "100 долларов"
IN_RUBLES = re.compile(AMOUNT + r"\s*(?:руб\w*|₽)", re.I)  # "10 000 рублей"


def _currency(groups):
    return CURRENCIES[next(i for i, g in enumerate(groups) if g)]


def currencies(text):
    """The currencies a phrase names, in order: [(code, forms, name)]."""
    out = []
    for m in CURRENCY.finditer(text):
        cur = _currency(m.groups())
        if cur[1] not in [c[0] for c in out]:
            out.append(cur[1:])
    return out


def _amount(m):
    value = float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
    scale = (m.group(2) or "").lower()
    return value * (1000 if scale.startswith("тыс") else 10 ** 6 if scale.startswith(("млн", "миллион")) else 1)


def rubles(value, exact=False):
    """82.1512 -> "82 рубля 15 копеек"; 8215.1 -> "8 215 рублей" (kopecks only below 100 rubles, or [exact])."""
    whole, kop = divmod(round(value * 100), 100)
    if value >= 100 and not exact:
        whole, kop = round(value), 0
    out = "%s %s" % (calc.number(whole), plural(whole, "рубль", "рубля", "рублей")) if whole else ""
    if kop:
        out += "%s%d %s" % (" " if out else "", kop, plural(kop, "копейка", "копейки", "копеек"))
    return out or "0 рублей"


def units(value, forms):
    """So many of a currency: whole ones from 10 up ("122 доллара"), else to the cent ("1,22 доллара")."""
    if value >= 10 or abs(value - round(value)) < 0.005:
        n = round(value)
        return "%s %s" % (calc.number(n), plural(n, *forms))
    return "%s %s" % (calc.number(round(value, 2)), forms[1])


def rate_in_force(day, asked):
    """Whether the rate set for [day] is still the one on [asked]: set on a working day, it holds till the
    day after the next working day (the one set on Friday - for Saturday - holds through Monday)."""
    following = day  # the working day after the one it was set on (the day before [day])
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return asked <= following


def rate_text(text, rates, day, today, tomorrow=False):
    """What the Central Bank's [rates] (set for [day]) say to [text]: the rate, rubles for so many of a
    currency, or so much of a currency for the rubles; [tomorrow]: asked "на завтра"."""
    text = to_digits(text)
    named = [c for c in currencies(text) if c[0] in rates]
    if not named:
        return "Такой валюты в курсах ЦБ нет."
    ahead = tomorrow and (day > today or rate_in_force(day, today + timedelta(days=1)))
    head = "Курс ЦБ на завтра ещё не установлен. Сейчас " if tomorrow and not ahead else ""
    counted = COUNTED.search(text)
    in_rubles = IN_RUBLES.search(text)
    if counted and _currency(counted.groups()[2:])[1] in rates:  # "100 долларов в рублях"
        _, code, forms, _ = _currency(counted.groups()[2:])
        n = _amount(counted)
        said = "%s — %s" % (units(n, forms), rubles(n * rates[code].unit))
    elif in_rubles:  # "сколько долларов на 10 000 рублей"
        code, forms, _ = named[0]
        rub = _amount(in_rubles)
        said = "%s — примерно %s" % (rubles(rub), units(rub / rates[code].unit, forms))
    else:
        parts = []
        for code, forms, name in named:
            rate = rates[code]
            if rate.unit < 1:  # "100 иен — 55 рублей 12 копеек"
                parts.append("%s %s — %s" % (calc.number(rate.nominal), plural(rate.nominal, *forms), rubles(rate.value, exact=True)))
            else:
                parts.append("%s — %s" % (name, rubles(rate.unit, exact=True)))
        said = "; ".join(parts)
        if head:
            return "%s%s." % (head, said)
        if ahead:
            return "Курс ЦБ на завтра: %s." % said
        return "По курсу ЦБ %s." % said
    if head:
        return "%s%s по курсу ЦБ." % (head, said)
    return "%s по курсу ЦБ%s." % (_cap(said), " на завтра" if ahead else "")


EXISTS = re.compile(r"(?<!\w)(есть|будет|нет)\s+ли(?!\w)", re.I)
# "какой день на этой неделе у меня самый загруженный?" is counted, not guessed (the model named Tuesday, one
# training, with four things on Monday in front of it)
BUSIEST = re.compile(r"сам\w*\s+(?:загруж|занят|плотн|насыщен|напряж)|больше\s+всего\s+(?:дел|событий|задач|планов)", re.I)
FREEST = re.compile(r"сам\w*\s+(?:свобод|л[её]гк|пуст|спокойн)|меньше\s+всего\s+(?:дел|событий|задач|планов)", re.I)
DURATION = re.compile(r"сколько\s+(?:\w+\s+)?(?:длит|продлит|ид[её]т|займ[её]т)|(?<!\w)длительност", re.I)
# "какая сейчас инфляция?" is searched; "какая сейчас у меня задача?" is not the internet's
MINE = re.compile(r"(?<!\w)(?:у\s+меня|мне|меня|мой|моя|моё|мое|мои|моих|моему|я)(?!\w)", re.I)
NOTE_TOPIC = re.compile(r"^(?:про|о|об|на тему|насчет|насчёт|по поводу)\s+", re.I)


# a pet or a relative told of: "его зовут Мурзик" goes to it
PET = r"(?:собак|кот|кошк|пес|пёс|щен|хомяк|попуга|черепах|брат|сестр)"
# the parts of a day: "что у меня вечером"
PART_WORDS = re.compile(r"(?<!\w)(?:утром|утра|днем|днём|вечером|вечер|ночью)(?!\w)", re.I)
PARTS = {"утр": ("утром", 6, 12), "дне": ("днём", 12, 18), "днё": ("днём", 12, 18), "веч": ("вечером", 17, 24), "ноч": ("ночью", 0, 6)}


def _upper_first_s(text):
    return text[:1].upper() + text[1:]


# a request dropped: "забей", "проехали", "не надо"
DISMISS = re.compile(r"(?<!\w)(?:забей|проехали|неважно|не\s+важно|не\s+надо|не\s+нужно|отмена)(?!\w)", re.I)
# a thing to do, said with "надо", "не забыть": "купить молоко", "отнести книги", "позвонить маме"
DUTY = re.compile(r"(?:купить|сделать|позвонить|написать|отправить|оплатить|заплатить|сдать|забрать|отнести|принести|записаться|"
                  r"подготовить|выучить|повторить|помыть|убрать|сходить|съездить|зайти|заехать|забронировать|заказать|"
                  r"починить|постирать|погладить|вынести|отдать|вернуть|полить|покормить|выгулять|проверить|поздравить)(?!\w)", re.I)


def TOLD_PLAN(text):
    """A day and a time and what, said as a statement: "в понедельник в 16:40 занятие по физике"; "у меня в
    субботу турнир" with no time too (a task of that day)."""
    if re.search(r"свобод|(?<!\w)занят[аы]?(?!\w)|перешел|перешла|перешёл|или\s+(?:дня|ночи|утра|вечера)|(?<!\w)класс", text, re.I):
        return False  # "я свободен завтра в 15", "я перешёл в 12", "в три дня или ночи": no plan told (each became one)
    repeats = repeat_days(text) is not None or REPEAT_WEEK.search(text) is not None  # "по будням в 8 утра английский"
    return ((_has_day(text) or repeats and _has_time(text)) and (_has_time(text) or re.match(r"^\W*(?:(?:ну|а|кстати|слушай|короче)\W+)*у\s+(?:меня|нас)\s", text, re.I)
                                and not re.search(r"рожден|днюх|выходн|праздник|каникул|отпуск|свобод|занят|дел(?:а|о)?\b|план", text, re.I))
            and not QUESTION.search(text)
            and not when_only(text)  # "завтра в 12." - no what
            and not re.search(r"(?<!\w)(?:был|была|было|были|вчера|позавчера|прошл\w*|не\s+надо|не\s+нужно)(?!\w)", text, re.I)
            # "я завтра в 10 не смогу прийти", "он сказал, что перенесёт встречу на завтра": said to someone, or
            # about someone else - not a plan to add («Я в не смогу прийти» was added)
            and not re.search(r"(?<!\w)не\s+(?!забудь|забыть)\w+|(?<!\w)(?:сказал\w*|говорит|говорил\w*|пишет|написал\w*|"
                              r"обещал\w*|думаю|думает|кажется)(?!\w)", text, re.I))


def _minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _clock(text, now, prep="в|к"):
    """"в 18", "в 6 вечера", "в 18:30" (after [prep]) -> "18:00": the time a phrase names, or None."""
    clean = clean_quick(to_digits(text), now)
    m = re.search(r"(?<!\w)(?:%s)\s+([01]?\d|2[0-3])(?::([0-5]\d))?(?![\d:.,])" % prep, clean, re.I) or \
        re.search(r"(?<![\d.])([01]?\d|2[0-3]):([0-5]\d)(?!\d)", clean)
    return "%02d:%s" % (int(m.group(1)), m.group(2) or "00") if m else None


def _cap(text):
    text = text.strip()
    return text[:1].upper() + text[1:]


def _short(text, words=8):
    parts = text.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _day_text(d):
    return "%s, %d %s" % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1])


class MemoryNotes:
    """Notes in Orpheus's own memory: the ones of «Личное» (they never go to the Planner)."""

    def __init__(self, memory):
        self.memory = memory

    def _note(self, n):
        return Note(n["id"], n["title"], n["text"], n["updated"], n)

    def notes_list(self):
        return [self._note(n) for n in self.memory.recent_notes(1000)]

    def note_add(self, title, body=""):
        text, title = (body, title) if body else (title, "")
        return self._note(self.memory.note(self.memory.add_note(text, title)))

    def note_delete(self, note):
        self.memory.delete_note(note.id)

    def note_restore(self, note):
        self.memory.add_note(note.raw["text"], note.raw["title"])

    def note_update(self, note, body):
        self.memory.update_note(note.id, body)
        return self._note(self.memory.note(note.id))

    def notes_find(self, query, limit=3, strict=True):
        found = [self._note(n) for n in self.memory.find_notes(query, limit)] if query.strip() else []
        for item in self.memory.recall(query, limit=limit + 2):
            if item["kind"] == "note" and all(n.id != item["id"] for n in found):
                n = self.memory.note(item["id"])
                if n:
                    found.append(self._note(n))
        return found[:limit]


class Skills:
    def __init__(self, brain, planner=None, intents=None, rng=None, weather=None, search=None):
        self.brain = brain
        self.planner = planner
        self.weather = weather
        self.search = search
        self.intents = intents or Intents.load()
        self.rng = rng or random.Random()
        self.pending = None  # (turn, action) - "Удалить …?" waiting for a yes or a no
        self.awaiting = None  # (turn, what) - "На когда поставить «Встречу»?" waiting for a day and a time
        self.wanted = None  # (turn, when) - "Что напомнить?" waiting for what, the day and time said already
        self.wanted_day = None  # (turn, "удали всё %s …") - "За какой день удалить всё?" waiting for the day
        self.pending_kept = -1
        self.last_fact = None  # (turn, id) of the fact just told: "на самом деле математика" changes it
        self.last_weather = None  # (turn, scenario, phrase) of the last weather question: its city goes on for three turns  # the turn after which a question still waiting for "да" was kept (see handle)
        self.rewritten = None  # the phrase as the scenarios read it, when that differs ("напомни, как …" -> "как …")
        self.undo_stack = []  # (action) - what "верни" does, the last one first
        self.turn = 0
        self.heard = ""
        self.handled = None  # the scenario that answered, for the log
        self.last = None  # (scenario, phrase) answered last, when "а завтра?" may go on from it
        self.side_turn = -1  # the last turn a scenario other than the plans' answered
        self.last_day = None  # the day "какое число будет в пятницу?" was about

    # ---------------------------------------------------------------------------------- the entry

    def handle(self, text, now=None):
        """-> what to say (str), Context for the model, Lookup (over the network), None (the model's),
        or a list of these. A question asked ("Удалить …?") still waits for its "да" after one phrase the model
        answered ("какое?" in between left the "да" to fall on nothing, and the model said "удалил")."""
        pending = self.pending
        result = self._handle(text, now)
        if result is None and self.pending is None and pending and pending[0] == self.turn - 1:
            self.pending = pending
            self.pending_kept = self.turn
        return result

    def _handle(self, text, now=None):
        """-> what to say (str), Context for the model, Lookup (over the network), None (the model's),
        or a list of these: two requests in one phrase, answered in turn."""
        now = now or datetime.now()
        self.turn += 1
        text = spoken(text)
        asks = REMIND_ASKS.match(text)
        # "напомни, какое сегодня число": a question whatever it names; "напомни, что я говорил про Виктора" too,
        # but "напомни, что завтра в 10 врач" is a reminder
        self.rewritten = None
        if asks and (asks.group(1) or REMIND_PAST.match(text) or not (_has_day(text) or _has_time(text))):
            text = self.rewritten = text[asks.end():]  # the model gets it so too: a question, not about its memory
        self.heard = text
        self.handled = None
        if self.planner:
            self.planner.new_turn(text)
        pending, self.pending = self.pending, None
        last, self.last = self.last, None
        if pending and (pending[0] == self.turn - 1 or pending[0] == self.turn - 2 and self.pending_kept == self.turn - 1):
            rest = re.sub(r"^\W*(?:(?:нет|не|неа|подожди|погоди|стоп|стой|лучше|ой|а)(?!\w)\W*)+", "", text, flags=re.I)
            if NO.search(text) and len(rest.split()) >= 2 and self._specific(rest) is not None:
                # "нет, подожди, русский лучше удали" to "Удалить физику?": no to that, and the new request done
                return self._dispatch(rest, now)
            if NO.search(text):
                self.handled = "confirm"
                other = self._other_day(pending[3] if len(pending) > 3 else None, text, now)
                if other is not None:  # "нет, завтрашнее"
                    return self.confirm_delete(other)
                return pending[2] if len(pending) > 2 else "Хорошо, не удаляю."
            if YES.search(text):
                self.handled = "confirm"
                return pending[1]()
        awaiting, self.awaiting = self.awaiting, None
        if awaiting and awaiting[0] == self.turn - 1 and when_only(text):
            return self._dispatch("добавь %s %s" % (text.strip(" .!"), awaiting[1]), now)  # "На когда?" - "Завтра в 12."
        wanted_day, self.wanted_day = self.wanted_day, None
        if wanted_day and wanted_day[0] == self.turn - 1 and not DISMISS.search(text) and not NO.search(text):
            if when_only(text):
                return self._dispatch(wanted_day[1] % text.strip(" .!"), now)
            if YES.search(text) and len(text.split()) <= 2:
                self.wanted_day = (self.turn, wanted_day[1])
                self.handled = "plan_delete"
                return "За какой день?"  # "да" to "за какой день?" was silence
        wanted, self.wanted = self.wanted, None
        if wanted and wanted[0] == self.turn - 1 and not NO.search(text) and not DISMISS.search(text) \
                and self._specific(text) is None and "?" not in text:
            # "напомни через 20 минут" - "Что напомнить?" - "позвонить бабушке": the model got it and said
            # "напомню через 20 минут", with nothing set
            return self._dispatch("напомни %s %s" % (wanted[1], re.sub(r"^(?:до|к)\s+", "", text.strip(" .!"), flags=re.I)), now)
        reaction = REACTION.match(text)
        if reaction:
            text = self.heard = text[reaction.end():]
        if HESITATION.match(text):  # "э-э", "хм": thinking aloud, not a request
            self.handled = "ack"
            return ""
        # "а завтра?" right after "какая погода в Казани?": the same question about another day
        # (not "а что у меня в среду?": a question of its own)
        text = self._there(text)  # "а сегодня там", "а там дождь будет": the city of the last weather question
        if not last and self.last_weather and self.turn - self.last_weather[0] <= 3:
            last = self.last_weather[1:]  # "а в Твери" after a turn the model answered, the weather talked of before
        follow = self._follow_up(last, text) if last and self._specific(text) is None else None
        if follow:
            result = self._dispatch(follow, now)
            if result is not None:
                return result
        fix = self._correction(text)
        if fix is not None:
            return fix
        both = self._split(text, now)
        if both is not None:
            return both
        result = self._dispatch(self._carry_last(last, text), now)
        if result is None and TOLD_PLAN(text):
            # "В понедельник в 16:40 занятие по физике." - a plan told, not asked about: the model answered
            # "У вас запланировано…" and nothing was added. Added; "верни" undoes it
            result = self._dispatch("добавь " + text.strip(), now)
        return result

    def _dispatch(self, text, now):
        """The first scenario whose template [text] matches and that takes it."""
        self.heard = text
        self.handled = None
        if self.planner:
            self.planner.heard = text
        for m in self.intents.match(text):
            if self.brain.personal and m.intent in SMALL_TALK:
                continue
            handler = getattr(self, "do_" + m.intent, None)
            if handler is None:
                continue
            try:
                result = handler(m, now)
            except Unavailable:
                result = "Планировщик сейчас не отвечает."
            except ZeroDivisionError:
                result = "На ноль делить нельзя."
            except ValueError as exc:  # the planner said no (an empty title and such)
                result = "Не получилось: %s." % str(exc).rstrip(".")
            if result is not None:
                self.handled = m.intent
                if not m.intent.startswith("plan") and m.intent not in ("confirm", "undo"):
                    self.side_turn = self.planner.turn if self.planner else self.turn  # "нет, на 15" after it: no correction
                if m.intent in FOLLOWED:
                    self.last = (m.intent, text)
                if m.intent in WEATHER_ALL:
                    self.last_weather = (self.turn, m.intent, text)
                return result
        return None

    def _specific(self, text):
        """The best scenario for [text] with words of its own in the template (not a bare slot, not a
        "да" or "спасибо"): what makes a half of a phrase a request by itself."""
        for m in self.intents.match(text):
            # not "какая …", "что …" alone (the planner's catch-all question): no words of its own
            if m.score >= 4 and m.intent not in NOT_A_HALF and not m.template.startswith("[а] (что|чего|какие"):
                return m
        return None

    def _split(self, text, now):
        """"Что у меня завтра и какая будет погода?", "Запомни, что я люблю кофе, и сделай заметку …":
        two requests in one phrase, each a scenario's, answered in turn. None when it is not two."""
        for sep in SPLIT.finditer(text):
            left, right = text[:sep.start()].strip(" ,"), text[sep.end():].strip(" ,")
            if not re.search(r"\w\w", left) or not re.search(r"\w\w", right):
                continue
            first = self._specific(left)
            if first is None:
                continue
            if first.intent == "days_until" and re.search(r"(?i)день\s+недели|какой\s+это\s+(?:будет\s+)?день", right):
                return self._dispatch(left, now)  # "…и какой это будет день недели?": the answer names it already
            if first.intent == "rate" and currencies(right) and self._specific(right) is None:
                return self._dispatch(text, now)  # "какой курс доллара и евро?": one answer with both
            right = self._second(first.intent, left, right)
            if right is None:
                continue
            one = self._dispatch(left, now)
            if one is None:
                return None  # the first half is not a request after all: the phrase is one
            name = self.handled
            last = self.last
            two = self._dispatch(right, now)
            self.handled = "%s+%s" % (name, self.handled or "модель")
            self.last = self.last or last
            if two is None:
                return [one, None]  # the model answers the rest, knowing what was said
            if isinstance(one, str) and one.rstrip().endswith("?"):
                return [two, one]  # a question goes last: the next phrase answers it
            return self._joined(one, two)
        return None

    @staticmethod
    def _joined(one, two):
        """Two answers to one phrase without the same said twice: "Добавил на завтра, …: в 12:00 Созвон; в 18:00
        Тренировка.", "Сегодня воскресенье, 27 сентября. По плану: …" (not "Сегодня, воскресенье, 27 сентября: …")."""
        if not (isinstance(one, str) and isinstance(two, str)):
            return [one, two]
        head1, sep1, rest1 = one.partition(": ")
        head2, sep2, rest2 = two.partition(": ")
        if sep1 and sep2 and head1 == head2 and head1.startswith("Добавил"):
            return ["%s: %s; %s" % (head1, rest1.rstrip(". "), rest2)]
        day = set(re.findall(r"\w+", normalize_low(head2)))
        if sep2 and len(day) >= 3 and day <= set(re.findall(r"\w+", normalize_low(one))):
            return [one, "В планах ничего нет." if rest2.startswith("в планах ничего нет") else "По плану: " + rest2]
        return [one, two]

    def _second(self, intent, left, right):
        """The second half as a request by itself: the day or the city it leaves out is the first
        half's ("что у меня завтра и какая погода" -> "какая погода завтра"); a bare "и в Питере",
        "и тренировку в 18" repeats the first request. None: it is no request."""
        m = self._specific(right)
        if m is None:
            follow = self._follow_up((intent, left), right)
            if follow:
                return follow
            # "добавь на завтра созвон в 12 и тренировку в 18"
            if intent == "plan_add" and _has_time(left) and _has_time(right) and not re.match(r"\W*\w+(?:ть|ти|сь)\b", right):
                day = DAY_EXPR.search(to_digits(left))
                return "добавь %s%s" % (day.group(0) + " " if day and not _has_day(right) else "", right)
            return None
        if m.intent in WEATHER | {"plan_ask"} and not _has_day(right):
            day = DAY_EXPR.search(to_digits(left))
            if day:
                right = "%s %s" % (right.rstrip(" ?.!"), day.group(0))
        if m.intent in WEATHER and intent in WEATHER and not _city(right):
            city = _city(left)
            if city:
                right = "%s %s" % (right.rstrip(" ?.!"), city.group(0))
        return right

    def _follow_up(self, last, text):
        """"а завтра?", "а в Сочи?", "а на выходных?" right after a question of [last]: that question
        again, about the new day or city, as a phrase of its own. None when [text] is not such a bit."""
        if not last or last[0] not in FOLLOWED:
            return None
        intent, prev = last
        frag = FOLLOW_START.sub("", text).strip(" ?!.,")
        if not frag or len(frag.split()) > 6:
            return None
        low = normalize_low(frag)
        if intent == "year_month":
            return "какой сейчас %s" % low if re.fullmatch(r"год|месяц|время года", low) else None
        if intent == "days_until":
            return "сколько дней %s" % frag if re.match(r"до\s+\w", low) else None
        if intent == "rate":  # "какой курс доллара?" - "а евро?"
            return "какой курс %s" % frag if currencies(frag) and len(frag.split()) <= 3 else None
        if intent == "fresh" and NEWS.search(prev) and not QUESTION.search(text) and len(frag.split()) <= 5 \
                and not TO_ME.search(text) and not ABOUT_THAT.search(frag):
            return "новости про %s" % re.sub(r"^(?:а\s+)?(?:про|о|об|по)\s+", "", frag, flags=re.I)  # not "про про технологии"
        if intent in ("web_search", "fresh"):
            # "найди, когда выйдет GTA 6" - "а сколько она будет стоить?": the model, asked this, said it
            # could not find it (searching nothing); searched again, with what the last search was about
            if len(frag.split()) > 8 or TO_ME.search(text) or not QUESTION.search(text) or \
                    not (re.match(r"\W*(?:а|и)(?!\w)", text, re.I) or ABOUT_THAT.search(text)):
                return None
            return "найди в интернете %s %s" % (search_query(prev), re.sub(r"^\W*(?:а|и)\W+", "", text, flags=re.I).strip(" ?.!"))
        if intent == "date_of" and re.fullmatch(r"(?:в|на)?\s*следующ\w*(?:\s+\w+)?", low) and self.last_day:
            d = self.last_day + timedelta(days=7)  # "какое число в пятницу?" - "а в следующую?"
            return "какое число будет %d.%02d.%d" % (d.day, d.month, d.year)
        if intent in ("time_until", "days_until") and re.match(r"(?:а\s+)?до\s+\S", low):
            return "сколько времени %s" % re.sub(r"^а\s+", "", frag, flags=re.I)  # "сколько до русского" - "а до физики"
        if intent == "time_in":  # "сколько времени в Токио" - "а в Лондоне": the other city's time
            city = _city(to_digits(frag))
            return "сколько времени %s" % city.group(0) if city and len(frag.split()) <= 3 else None
        if intent in WEATHER_ALL and re.fullmatch(r"(?:а\s+)?(?:дома|у\s+нас|у\s+меня|здесь|тут)", low):
            # "а дома": the same question about home (the model said "дома +13", Petersburg's number)
            base = to_digits(prev)
            old = _city(base)
            return " ".join((base.replace(old.group(0), " ") if old else base).split()) if old else None
        if intent == "weather_degrees" and (DAY_EXPR.search(to_digits(frag)) or _city(to_digits(frag))):
            # "в Питере сколько градусов" - "а завтра", "а в Твери": the forecast's (the model made up "+15", "+18")
            prev = "какая погода " + (_city(to_digits(prev)).group(0) if _city(to_digits(prev)) else "")
            intent = "weather"
        f = to_digits(frag)
        day = DAY_EXPR.search(f)
        city = _city(f) if intent in WEATHER_ALL else None
        part = PART_EXPR.search(f) if intent in WEATHER_ALL else None  # "а ночью?" after "погода вечером"
        rest = f
        for found_ in (day, city, part):
            if found_:
                rest = rest.replace(found_.group(0), " ")
        words = FOLLOW_WORDS_WEATHER if intent in WEATHER_ALL else FOLLOW_WORDS_PLANS if intent.startswith("plan") else FOLLOW_WORDS
        if not (day or city or part) or any(w not in words for w in re.findall(r"\w+", normalize_low(rest))):
            return None
        base = to_digits(prev)
        if part:
            base = PART_EXPR.sub(" ", base)
        if day:
            base = DAY_EXPR.sub(" ", base)
        if city:
            old = _city(base)
            if old:
                base = base.replace(old.group(0), " ")
        if intent == "plan_next":
            base = "что у меня"
        return " ".join(("%s %s" % (base.strip(" ?.!,"), f)).split())

    def _carry_last(self, last, text):
        if (not last or last[0] not in WEATHER) and self.last_weather and self.turn - self.last_weather[0] <= 3:
            last = self.last_weather[1:]  # "нужен ли зонт" a turn or two after "погода в Казани", the model's between
        return self._carry(last, text)

    def _there(self, text):
        """"А там дождь будет?", "а сегодня там": "там" is the city of the last weather question."""
        if not self.last_weather or self.turn - self.last_weather[0] > 3 or not re.search(r"(?<!\w)там(?!\w)", text, re.I):
            return text
        if len(re.findall(r"\w+", text)) > 4 and not WEATHER_WORDS.search(text):
            return text  # "что там у меня завтра": the plans' "там", not a city's
        if _city(to_digits(text)):
            return text  # "как там в Сочи": the city is named (it became "как в Питере в Сочи")
        city = _city(to_digits(self.last_weather[2]))
        return re.sub(r"(?<!\w)там(?!\w)", city.group(0), text, count=1, flags=re.I) if city else text

    def _carry(self, last, text):
        """"Какая погода в Казани завтра?" and then "а дождь будет?", "а зонт нужен?": the city and the
        day of the last question go on to such a follow-up, unless it names its own. A question of its
        own ("какая погода?", "сколько градусов на улице?") is about here and now again."""
        if not last or last[0] not in WEATHER:
            return text
        m = self._specific(text)
        if m is None or m.intent not in ("weather_rain", "weather_clothes", "weather_wind", "weather_humidity", "weather_degrees"):
            return text
        prev = to_digits(last[1])
        extra = []
        city = _city(prev)
        if city and not _city(text):
            extra.append(city.group(0))
        day = DAY_EXPR.search(prev)
        if day and not _has_day(text) and m.intent != "weather_degrees":  # "сколько сейчас градусов" is now's
            extra.append(day.group(0))
        return " ".join([text.rstrip(" ?.!")] + extra) if extra else text

    def _ask(self, question, action, no="Хорошо, не удаляю.", item=None):
        self.pending = (self.turn, action, no, item)
        return question

    def _can_undo(self, action):
        self.undo_stack = (self.undo_stack + [action])[-10:]

    @property
    def notebook(self):
        """Where notes go: the Planner (seen on the phone and the desktop), or in «Личное» and
        without a Planner the memory of this laptop."""
        if self.planner is not None and not self.brain.personal:
            return self.planner
        return MemoryNotes(self.brain.active)

    # ---------------------------------------------------------------------------------- time, date

    def do_time(self, m, now):
        return "Сейчас %s." % now.strftime("%H:%M")

    def do_time_until(self, m, now):
        """"Сколько времени осталось до полуночи?", "…до 18:00": the hours and minutes left (the model: 10 s)."""
        when = m.slots.get("when", "") or ("конца " + m.slots["end"] if m.slots.get("end") else "")
        holiday = next(((mo, d, said) for pattern, mo, d, said in HOLIDAYS if re.search(pattern, to_digits(when), re.I)), None)
        if re.search(r"полуноч|полноч|конца\s+(?:дня|суток)|^\s*завтра\s*$", when, re.I):
            target, name = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()), "полуночи"
        elif holiday:  # "сколько часов до нового года" (the model: "около 194 дней", "22479 часов")
            target = datetime(now.year, holiday[0], holiday[1])
            if target <= now:
                target = target.replace(year=now.year + 1)
            name = holiday[2]
        else:
            name = _clock("в " + when, now)
            if not name and self.planner is not None and not _has_day(when):
                # "через сколько часов хакатон", "сколько до конца хакатона": a plan's start or end (the model
                # counted 15 hours for 10)
                end = re.match(r"(?:конца|окончания)\s+", when, re.I)
                item, _ = self.planner._find(when[end.end():] if end else when, strict=True)
                if item is None:
                    return None
                item = self.planner.next_of(item)
                clock = (item.get("end_time") if end else None) or item.get("start_time")
                if not clock:
                    return self.do_days_until(dataclasses.replace(m, slots={"when": when}), now)
                target = datetime.combine(date.fromisoformat(item["date"]), datetime.strptime(clock, "%H:%M").time())
                if target <= now:
                    return "«%s» %s." % (item["title"], "уже закончился" if end else "уже начался")
                name = "%s«%s»" % ("конца " if end else "", item["title"])
            elif not name or _has_day(when):
                return None  # "сколько осталось до пятницы": the days'
            else:
                target = now.replace(hour=int(name[:2]), minute=int(name[3:]), second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
        hours, minutes = divmod(int((target - now).total_seconds() // 60), 60)
        days, hours = divmod(hours, 24)  # "31 час" is "1 день 7 часов"
        left = ["%d %s" % (days, plural(days, "день", "дня", "дней"))] if days else []
        left += ["%d %s" % (hours, plural(hours, "час", "часа", "часов"))] if hours else []
        if (minutes or not (hours or days)) and not days:
            left.append("%d %s" % (minutes, plural(minutes, "минута", "минуты", "минут")))
        return "До %s осталось %s." % (name, " ".join(left))

    def do_plan_end(self, m, now):
        """"Когда закончится хакатон?": the end in the plans (plan_ask said its start)."""
        if self.planner is None:
            return None
        item, _ = self.planner._find(m.slots.get("what", ""), strict=True)
        if item is None:
            return None
        item = self.planner.next_of(item)
        day = spoken_day(date.fromisoformat(item["date"]), now.date())
        if item.get("end_time"):
            return "«%s» заканчивается %s в %s." % (item["title"], day, item["end_time"])
        at = " начинается в %s," % item["start_time"] if item.get("start_time") else ""
        return "«%s»%s %s; конец в планах не указан." % (item["title"], at, day)

    def do_age(self, m, now):
        """"Сколько мне лет, если я родился в 2008?" - the model said 16. "Я 2008 года рождения" is kept too."""
        year = int(re.sub(r"\D", "", to_digits(m.slots.get("year", ""))) or 0)
        if not 1900 < year <= now.year:
            return None
        age = now.year - year
        birthday = self._birthday(now.date())
        if m.template.startswith("я {year}"):
            memory = self.brain.active
            memory.remember("Я родился в %d году" % year)
            self.brain.refresh_facts()
        if birthday is not None:  # the day is known: exact
            return "Тебе %d." % (age if birthday.year > now.year or birthday == now.date() else age - 1 if birthday > now.date() else age)
        return "Тебе %d, если день рождения в этом году уже был, иначе %d." % (age, age - 1)

    def do_date(self, m, now):
        return "Сегодня %s." % _day_text(now.date())

    def do_date_of(self, m, now):
        when = m.slots.get("when", "")
        today = now.date()
        span = period(when, today)
        if span is None or span[0] != span[1] or not re.search(r"\w", when):
            return None
        d = span[0]
        self.last_day = d
        near = {0: "Сегодня", 1: "Завтра", 2: "Послезавтра", -1: "Вчера", -2: "Позавчера"}.get((d - today).days)
        if near:
            # "позавчера был четверг", "вчера была пятница", "было воскресенье"
            past = ["был", "был", "была", "был", "была", "была", "было"][d.weekday()]
            verb = "" if near == "Сегодня" else " будет" if (d - today).days > 0 else " " + past
            return "%s%s %s." % (near, verb, _day_text(d))
        if re.search(r"\d", to_digits(when)) and not re.search(r"через", when, re.I):  # "1 октября" -> the weekday
            return "%d %s — %s." % (d.day, MONTHS[d.month - 1], WEEKDAYS[d.weekday()])
        return "%s — %d %s." % (_cap(when), d.day, MONTHS[d.month - 1])

    def do_year_month(self, m, now):
        unit = m.slots.get("unit", "").lower()
        if unit == "год":
            return "Сейчас %d год." % now.year
        if unit == "месяц":
            return "Сейчас %s." % MONTHS_NOM[now.month - 1]
        return "Сейчас %s." % SEASONS[now.month - 1]

    def do_leap_year(self, m, now):
        leap = lambda y: y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)  # noqa: E731
        if re.search(r"следующ", self.heard, re.I):
            y = now.year + 1
            while not leap(y):
                y += 1
            return "Следующий високосный год — %d." % y
        digits = re.sub(r"\D", "", to_digits(m.slots.get("year", "")))
        year = int(digits) if digits else now.year
        if not 1 <= year <= 9999:
            return None
        return "%d год %sвисокосный." % (year, "" if leap(year) else "не ")

    def do_days_until(self, m, now):
        when = m.slots.get("when", "")
        today = now.date()
        target, name = None, None
        for pattern, month, day, said in HOLIDAYS:
            if re.search(pattern, to_digits(when), re.I):
                target, name = date(today.year, month, day), said
                break
        if target is None and re.search(r"конц\w*\s+(?:этого\s+)?год", when, re.I):
            target, name = date(today.year, 12, 31), "конца года"  # "сколько до конца года месяцев" got "Это сегодня"
        if target is None and re.search(r"конц\w*\s+недел", when, re.I):
            # "сколько осталось до конца недели" got "Это сегодня"
            left = 6 - today.weekday()
            if left == 0:
                return "Неделя кончается сегодня."
            end = today + timedelta(days=left)
            return "До конца недели %d %s: воскресенье, %d %s." % (left, plural(left, "день", "дня", "дней"), end.day, MONTHS[end.month - 1])
        if target is None and re.search(r"рожден", when, re.I):  # "до моего дня рождения": from the memory
            target = self._birthday(today)
            if target is None:
                return None
            name = "твоего дня рождения" if re.search(r"мо(его|й)|у меня", when, re.I) else when
        if target is None:
            span = period(when, today) if _has_day(when) or re.search(r"\d", when) else None
            if span is None:
                # "до кр по алгебре", "до пробника", "до ЕГЭ": a plan's day (the model counted from the wrong day
                # and swapped one plan for another)
                if self.planner is None or _has_time(when) or _clock("в " + when, now) or not re.search(r"[а-яё]{2,}", when, re.I):
                    return None  # "сколько до 18:00": the clock's (it found the physics at 16:00)
                if re.match(r"(?:конца|окончания)\s", when, re.I):
                    return self.do_time_until(m, now)  # "сколько до конца хакатона": the hours'
                item, _ = self.planner._find(re.sub(r"\s+дн\w*$", "", when), strict=True)
                if item is None:
                    return None
                item = self.planner.next_of(item)
                target = date.fromisoformat(item["date"])
                if target < today:
                    return "«%s» уже прошло: было %s." % (item["title"], spoken_day(target, today))
                days = (target - today).days
                when_ = spoken_day(target, today)
                at = " в %s" % item["start_time"] if item.get("start_time") else ""
                if days == 0 and item.get("start_time"):
                    return self.do_time_until(dataclasses.replace(m, slots={"when": when}), now)  # hours, not "сегодня в 12"
                if days == 0:
                    return "«%s» сегодня%s." % (item["title"], at)
                if days == 1:
                    return "«%s» уже завтра%s." % (item["title"], at)
                return "До «%s» %d %s: %s." % (item["title"], days, plural(days, "день", "дня", "дней"), when_)
            target = span[0]
            name = "%d %s" % (target.day, MONTHS[target.month - 1])
        if target < today:
            target = target.replace(year=target.year + 1)
        days = (target - today).days
        if days == 0:
            return "Это сегодня."
        if days == 1:
            return "Это уже завтра."
        weekday = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][target.weekday()]
        if re.search(r"недел", self.heard, re.I):
            weeks, rest = divmod(days, 7)
            return "До %s %d %s — %s%d %s%s, это будет %s." % (
                name, days, plural(days, "день", "дня", "дней"), "" if rest else "ровно ", weeks,
                plural(weeks, "неделя", "недели", "недель"),
                " и %d %s" % (rest, plural(rest, "день", "дня", "дней")) if rest else "", weekday)
        return "До %s %d %s, это будет %s." % (name, days, plural(days, "день", "дня", "дней"), weekday)

    def _birthday(self, today):
        """The owner's birthday, as they told it: a fact about "день рождения" with a date in it."""
        for _, fact in self.brain.memory.facts():
            if not re.search(r"рожд", fact, re.I):
                continue
            span = period(fact, today)
            if span is not None:
                return span[0]
        return None

    def do_time_in(self, m, now):
        """"Который час в Токио?" - by the city's time zone."""
        city = m.slots.get("city", "").strip(" ?.!")
        if self.weather is None or not re.search(r"\w{2}", city):
            return None

        def fetch():
            from zoneinfo import ZoneInfo
            try:
                place = self.weather.place("в " + city)
            except Offline:
                return None  # the model says it can't know without the internet
            if place is None:
                return "Такого города я не нашёл."
            here = now.astimezone()  # a naive time is this machine's local one
            there = here.astimezone(ZoneInfo(place.tz))
            diff = round((there.utcoffset() - here.utcoffset()).total_seconds() / 3600)
            shift = "" if diff == 0 else ", на %d %s %s, чем здесь" % (abs(diff), plural(abs(diff), "час", "часа", "часов"),
                                                                  "больше" if diff > 0 else "меньше")
            return "%s сейчас %s%s." % (_cap(place.where), there.strftime("%H:%M"), shift)
        return Lookup("", fetch)

    # ---------------------------------------------------------------------------------- sums

    def do_calc(self, m, now):
        found = calc.evaluate(m.slots.get("expr", ""))
        if found is None:
            return None
        value, said = found
        return "%s — %s." % (_cap(said), calc.number(value))

    # ---------------------------------------------------------------------------------- notes

    def do_note_add(self, m, now):
        text = m.slots.get("text", "")
        if not re.search(r"\w", text):
            return None
        said_note = re.search(r"заметк|заметочк|памятк|запис[ьи]\b|записочк", normalize_low(self.heard))
        span = planner_time_span(to_digits(text)) is not None  # "запиши на завтра футбол с 16 до 18" became a note
        if not said_note and self.planner is not None and (_has_time(text) or span or _has_day(text) and len(text.split()) <= 6):
            # "запиши меня к врачу на пятницу в 10", "запиши на пятницу стрижку" are for the planner; a long text
            # with a day in it ("в субботу приедет мастер…, нужно освободить проход…") is a note
            return None
        title, body = self._split_note(text)
        book = self.notebook
        where = "в личные заметки" if self.brain.personal else "в заметки"
        try:
            note = book.note_add(title, body)
        except Unavailable:  # a note must not be lost: kept on the laptop until the Planner is back
            book = MemoryNotes(self.brain.active)
            note = book.note_add(title, body)
            where = "в память ноута, Planner сейчас не отвечает"
        self._can_undo(lambda: (book.note_delete(note), "Убрал заметку «%s»." % note.display)[1])
        if note.title and note.body:  # "Записал в заметки «Ремонт»: купить краску и валик."
            return "Записал %s «%s»: %s." % (where, note.title, _short(note.body[:1].lower() + note.body[1:], 10).rstrip("."))
        return "Записал %s: %s." % (where, _short(note.display, 10).rstrip("."))

    def _split_note(self, text):
        """"про ремонт, купить краску" -> ("Ремонт", "Купить краску"); a short note is all title,
        a long one all text (the Planner shows its first line)."""
        text = re.sub(r"^(?:что|то,?\s+что)\s+", "", text.strip(), flags=re.I).strip()
        # "мне бы не забыть купить батарейки, запиши": the note is what not to forget
        text = re.sub(r"^(?:мне\s+)?(?:бы\s+|надо\s+бы\s+|нужно\s+)?(?:не\s+забыть|чтобы\s+не\s+забыть)\s+", "", text,
                      flags=re.I).strip(" ,.")
        topic = NOTE_TOPIC.match(text) or re.search(
            r"(?:заметк|заметочк|запис|памятк)\w*\s+(?:про|о|об|на тему|насчет|по поводу)\s", normalize_low(self.heard))
        if topic:
            rest = NOTE_TOPIC.sub("", text)
            head = re.match(r"([^,:;.—–-]{1,40})[,:;.—–-]\s*(.+)", rest)
            if head and len(head.group(1).split()) <= 5 and head.group(1).strip().lower() not in ("то", "это", "том"):
                return _cap(head.group(1)), _cap(head.group(2))
            text = rest
        head = re.match(r"([^:]{1,40}):\s*(.+)", text)
        if head:
            return _cap(head.group(1)), _cap(head.group(2))
        if len(text) <= 60:
            return _cap(text), ""
        return "", _cap(text)

    def do_note_last(self, m, now):
        notes = self.notebook.notes_list()
        if not notes:
            return "Заметок пока нет."
        return "Последняя заметка: %s." % notes[0].spoken().rstrip(".")

    def do_note_list(self, m, now):
        notes = self.notebook.notes_list()
        if not notes:
            return "Заметок пока нет."
        names = "; ".join(_short(n.display, 8).rstrip(".") for n in notes[:5])
        if len(notes) > 5:
            return "Заметок %d. Последние: %s." % (len(notes), names)
        if len(notes) == 1:
            return "Одна заметка: %s." % notes[0].spoken().rstrip(".")
        return "В заметках: %s." % names

    def do_note_find(self, m, now):
        query = m.slots.get("query", "")
        found = self.notebook.notes_find(query, 3, strict=m.template.startswith("<прочитай> [мне] {query}"))
        if not found:
            if m.template.startswith("<прочитай> [мне] {query}"):
                return None  # "прочитай стих": not a note
            return "Не нашёл заметок про %s." % query
        answer = "%s." % _cap(found[0].spoken()).rstrip(".")
        if len(found) > 1:
            answer += " Ещё похожие: %s." % "; ".join(_short(n.display, 6).rstrip(".") for n in found[1:])
        return answer

    def do_note_delete(self, m, now):
        query = m.slots.get("query", "")
        book = self.notebook
        if re.search(r"(?<!\w)вс[её](?!\w)", normalize_low(self.heard)) and not query:
            return self._delete_all_notes(book)
        if re.search(r"последн", normalize_low(self.heard)) and not query:
            notes = book.notes_list()
            note = notes[0] if notes else None
        elif not query:
            return "Какую заметку удалить?"
        else:
            found = book.notes_find(query, 1, strict=False)
            note = found[0] if found else None
        if note is None:
            return "Не нашёл такой заметки."

        def delete():
            book.note_delete(note)
            self._can_undo(lambda: (book.note_restore(note), "Вернул заметку «%s»." % note.display)[1])
            return "Удалил заметку «%s»." % note.display

        return self._ask("Удалить заметку «%s»?" % _short(note.display, 8), delete)

    def _delete_all_notes(self, book):
        notes = book.notes_list()
        if not notes:
            return "Заметок и так нет."

        def delete():
            for n in notes:
                book.note_delete(n)
            self._can_undo(lambda: ([book.note_restore(n) for n in notes],
                                    "Вернул %d %s." % (len(notes), plural(len(notes), "заметку", "заметки", "заметок")))[1])
            return "Удалил %d %s." % (len(notes), plural(len(notes), "заметку", "заметки", "заметок"))

        names = "; ".join(_short(n.display, 5).rstrip(".") for n in notes[:5])
        return self._ask("Удалить все заметки — %d %s: %s%s?" % (
            len(notes), plural(len(notes), "штуку", "штуки", "штук"), names, " и другие" if len(notes) > 5 else ""), delete)

    def do_note_count(self, m, now):
        notes = self.notebook.notes_list()
        if not notes:
            return "Заметок пока нет."
        names = "; ".join(_short(n.display, 5).rstrip(".") for n in notes[:5])
        return "%s %d %s: %s%s." % ("В личных заметках" if self.brain.personal else "Всего", len(notes),
                                     plural(len(notes), "заметка", "заметки", "заметок"), names,
                                     " и другие" if len(notes) > 5 else "")

    def do_note_append(self, m, now):
        """"Добавь в заметку про ремонт ещё кисти": the note named by the first words, the rest added to it."""
        words = m.slots.get("rest", "").split()
        book = self.notebook
        notes = book.notes_list()
        if notes and re.search(r"(?<!\w)(?:последн|эт[уо]й?\s+заметк)", normalize_low(self.heard)):
            words = [notes[0].display] + words  # "допиши к последней заметке ещё удлинитель": the newest note (list: newest first)
            notes = notes[:1]
        for k in range(min(3, len(words) - 1), 0, -1):
            stems = _stems(" ".join(words[:k]))
            hit = [n for n in notes if stems and all(st in n.display.lower().replace("ё", "е") for st in stems)]
            if hit:
                note = hit[0]
                text = re.sub(r"^(?:ещ[её]|также|тоже|что|чтобы|:)\s*", "", " ".join(words[k:]), flags=re.I).strip(" .")
                if not re.search(r"\w", text):
                    return None
                old = note.body
                body = "%s%s%s" % (old.rstrip(" ."), ", " if old.strip() else "", text) if old.strip() else _cap(text)
                updated = book.note_update(note, body)
                self._can_undo(lambda: (book.note_update(updated, old), "Вернул заметку «%s» как было." % note.display)[1])
                return "Дописал в заметку «%s»: %s." % (note.display, text)
        return "Не нашёл такой заметки." if notes else "Заметок пока нет."

    # ---------------------------------------------------------------------------------- facts

    def do_remember(self, m, now):
        fact = re.sub(r"^(?:что|то,?\s+что)\s+", "", m.slots.get("fact", ""), flags=re.I).strip()
        if not re.search(r"\w{2}", fact):
            return None
        if self.planner is not None and _has_time(fact) and re.search(
                r"сегодня|завтра|послезавтра|понедельн|вторник|сред[уа]|четверг|пятниц|суббот|воскресен", fact, re.I):
            return self.planner.add(fact)  # "запомни, что завтра в 10 у меня врач" is a plan
        memory = self.brain.active
        fact_id = memory.remember(_cap(fact))
        self.brain.refresh_facts()
        self._can_undo(lambda: (memory.forget(fact_id), self.brain.refresh_facts(), "Хорошо, это я забыл.")[2])
        return "Запомнил."

    def do_tell_fact(self, m, now):
        """"Моего друга зовут Тимур", "моя любимая еда — пельмени", "я не ем острое": said about oneself, to stay.
        Told this, the model said "запомнил" and saved nothing (and its claim was then dropped: "Этого я не сделал")."""
        heard = self.heard.strip()
        timed = _has_time(re.sub(r"(?<!\w)в\s+\S+\s+класс\w*", " ", heard, flags=re.I))  # "в 11 классе" is no time
        if re.search(r"(?<!\w)(?:какой|какая|какое|какие|кто|что|как|где|помнишь|знаешь)(?!\w)", " ".join(
                m.slots.get(k, "") for k in ("value", "name", "what", "where")), re.I):
            return None  # "мой любимый цвет какой" asks; it was saved as the colour «какой»
        wish = re.search(r"(?<!\w)(?:если|когда|хотел)", heard, re.I) or (
            re.search(r"(?<!\w)хочу(?!\w)", heard, re.I) and not re.search(r"хочу\s+поступить", heard, re.I))
        if "?" in heard or TO_ME.search(heard) or timed or wish:
            return None  # "я люблю тебя", "я люблю по субботам в 10 бегать", "я не люблю, когда …": not such a fact
        words = re.findall(r"\w+", m.slots.get("what", "") or m.slots.get("name", "") or m.slots.get("where", "") or "x")
        if len(words) > 6:
            return None
        if "(мой|моя) [лучший" in m.template and (len(words) > 2 or re.search(r"(?:л|ла|ли|ет|ит|ут|ят)$", words[-1], re.I)):
            return None  # "мой друг сказал …", "моя подруга уехала": about them, not their name
        if m.slots.get("age") and not re.fullmatch(r"\d{1,3}", to_digits(m.slots["age"]).strip()):
            return None  # "мне сто лет в обед" is no age
        memory = self.brain.active
        facts = memory.facts()
        stop = re.search(r"(?<!\w)больше\s+не\s+(\w+)\s+(.+)$", heard, re.I)
        if stop:
            # "я больше не играю на гитаре": the fact goes (the model said "понял, ты перестал" and kept it)
            verb, obj = stop.group(1)[:4].lower(), _stems(stop.group(2))
            gone = [(i, t) for i, t in facts if verb in t.lower() and (not obj or any(o in t.lower() for o in obj))]
            if not gone:
                return "Этого я о тебе и не помнил."
            for i, _ in gone:
                memory.forget(i)
            self.brain.refresh_facts()
            return "Понял, убрал из памяти."
        fact = _cap(re.sub(r"^\W*(?:(?:ну|слушай|кстати|короче|вот|а|теперь|поправка|нет)\W+)*", "", to_digits(heard) if m.slots.get("age")
                           else heard).rstrip(" .!"))
        fact = re.sub(r"(?<!\w)теперь\s+", "", fact, flags=re.I)
        if m.slots.get("old"):  # "лучший друг у меня не Егор а Костя"
            fact = "Мой лучший друг %s" % m.slots["name"].strip(" .!")
        old = None
        if re.match(r"(?i)лучш\w+\s+(?:друг|подруга)", fact):  # "нет, лучший друг Влад"
            fact = ("Моя " if re.match(r"(?i)лучшая", fact) else "Мой ") + fact[:1].lower() + fact[1:]
            fact = re.sub(r"(?i)\s+у\s+меня\s+", " ", fact)
        friend = re.match(r"(?i)(мой|моя)\s+(?:лучш\w+\s+)?(друг|подруга|парень|девушка)(?!\w)", fact)
        verb = re.match(r"(?i)я\s+(\w+)", fact)
        if friend:
            # "теперь мой лучший друг Егор": replaces the friend saved before
            kind = friend.group(2).lower()[:4]
            old = next((i for i, t in facts if re.match(r"(?i)(мой|моя)\s+(?:лучш\w+\s+)?%s" % kind, t)), None)
        elif verb and re.search(r"(?<!\w)теперь(?!\w)", heard, re.I):
            # "я теперь играю на барабанах": the same verb's fact before ("я играю на гитаре") replaced
            stem = verb.group(1).lower()[:4]
            old = next((i for i, t in facts if re.match(r"(?i)я\s+%s" % re.escape(stem), t)), None)
        # a newer word for the same thing replaces the old one: "моего друга зовут …", "моя любимая еда …", "мне 17 лет";
        # "теперь друга зовут Миша" said without "моего" (told so, the model said "обновляю" and kept the old one)
        who = m.slots.get("who", "")
        if m.template.startswith("(ей|ему)"):
            # "ей 12" after "у меня сестра Маша": the age goes to her (the model said "Этого я не сделал")
            who = next(((i, t) for i, t in reversed(facts) if re.search(PET, t, re.I) and not re.search(r"\d+\s+(?:лет|год)", t)), None)
            if who is None or not re.fullmatch(r"\d{1,3}", to_digits(m.slots["age"]).strip()):
                return None
            n = int(to_digits(m.slots["age"]))
            said = "ему" if re.match(r"\W*ему", self.heard, re.I) else "ей"  # the word said, not the template's first
            memory.update_fact(who[0], "%s, %s %d %s" % (who[1].rstrip(" ."), said, n, plural(n, "год", "года", "лет")))
            self.brain.refresh_facts()
            return "Запомнил."
        if m.template.startswith("[а] (его|ее|её) (зовут"):
            # "его зовут Мурзик" after "у меня есть кот": the pet's name added to it (the model said "записал")
            pet = next(((i, t) for i, t in reversed(facts) if re.search(PET, t, re.I) and not re.search(r"зовут|кличк|имени", t, re.I)), None)
            if pet is None:
                return None
            old, fact = pet[0], "%s по имени %s" % (pet[1].rstrip(" ."), m.slots["name"].strip(" .!"))
        if old is not None:
            pass
        elif who and m.slots.get("name"):
            stem = normalize_low(who).split()[-1][:4]
            old = next((i for i, t in facts if re.search(r"(?<!\w)%s\w*\s+(?:зовут|звать)" % re.escape(stem), t, re.I)), None)
            if old is not None:
                fact = re.sub(r"((?:зовут|звать)\s+).+$", lambda x: x.group(1) + m.slots["name"].strip(" .!"),
                              dict(facts)[old])
        elif m.slots.get("value"):
            stem = normalize_low(m.slots.get("what", "")).split()[-1][:4] if m.slots.get("what") else ""
            old = next((i for i, t in facts if stem and re.search(r"любим\w*\s+%s" % re.escape(stem), t, re.I)), None)
            if old is not None and not fact.lower().startswith("мо"):
                fact = re.sub(r"^(?:мой|моя|мое|моё|мои)?\s*", "", dict(facts)[old], flags=re.I)
                fact = _cap(re.sub(r"(любим\w*\s+\w+)\s+(?:это\s+|—\s*)?.+$", lambda x: "%s — %s" % (x.group(1), m.slots["value"].strip(" .!")),
                                   dict(facts)[old]))
        elif m.slots.get("age"):
            old = next((i for i, t in facts if re.match(r"мне\s+\d+\s+(?:лет|год)", t, re.I)), None)
        if old is not None:
            memory.update_fact(old, fact)
            self.last_fact = (self.turn, old)
        else:
            fact_id = memory.remember(fact)
            self.last_fact = (self.turn, fact_id)
            self._can_undo(lambda: (memory.forget(fact_id), self.brain.refresh_facts(), "Хорошо, это я забыл.")[2])
        self.brain.refresh_facts()
        return "Запомнил."

    def do_fact_fix(self, m, now):
        """"Нет, не синий, а зелёный", "на самом деле математика", "брату исполнилось 21": the saved fact changed
        (the model said "понял, обновляю" and the old one stayed)."""
        memory = self.brain.active
        facts = memory.facts()
        if not facts:
            return None
        new = m.slots.get("new", "").strip(" .!")
        if m.slots.get("age"):
            n = re.search(r"\d{1,3}", to_digits(m.slots["age"]))
            key = {"брат": "брат", "сест": "сестр", "мам": "мам", "пап": "пап", "дру": "друг", "под": "подруг"}.get(
                normalize_low(self.heard)[:4].strip(), "")
            who = next(((i, t) for i, t in reversed(facts) if re.search(r"\d+\s+(?:лет|год)", t)
                        and (not key or key in t.lower())), None)
            if who is None or n is None:
                return None
            fact = re.sub(r"\d+\s+(?:лет|года|год)", "%s %s" % (n.group(0), plural(int(n.group(0)), "год", "года", "лет")), who[1])
        elif m.slots.get("old"):
            old = _stems(m.slots["old"])
            who = next(((i, t) for i, t in reversed(facts) if old and re.search(r"(?<!\w)%s" % re.escape(old[0][:4]), t, re.I)), None)
            if who is None or not re.search(r"\w", new):
                return None
            fact = re.sub(r"(?<!\w)%s\w*" % re.escape(old[0][:4]), new, who[1], count=1, flags=re.I)
        else:  # "на самом деле математика": the fact just told
            if self.last_fact is None or self.turn - self.last_fact[0] > 2 or len(new.split()) > 3:
                return None
            who = next(((i, t) for i, t in facts if i == self.last_fact[1]), None)
            if who is None:
                return None
            head = re.match(r"(?i)(.*?(?:любим\w*\s+\w+|друг|подруга|зовут|кличке|имени)\s+(?:это\s+|—\s*)?)", who[1])
            if not head:
                return None
            fact = head.group(1) + new
        memory.update_fact(who[0], fact)
        self.brain.refresh_facts()
        return "Запомнил: %s." % to_you(fact).rstrip(".")

    def do_forget(self, m, now):
        query = m.slots.get("fact", "")
        memory = self.brain.active
        fact = self._best_fact(memory, query)
        if fact is None:
            # "забудь про Тимура", told of in the talk but never saved: the talk forgets it (the model named him after
            # "Такого я не помню")
            stems = [w[:max(4, len(w) - 2)] for w in re.findall(r"\w{4,}", query) if not re.fullmatch(r"(?:всё|все|это|того|этого|пожалуйста)", w, re.I)]
            gone = sum(self.brain.forget_said(s) + memory.forget_turns(s) for s in stems[:3])
            return "Хорошо, забыл." if gone else "Такого я не помню."
        fact_id, text = fact
        memory.forget(fact_id)
        # the facts the model reads are the memory's again: told "забудь", it kept saying it for the rest of
        # the conversation, from the snapshot it had
        self.brain.refresh_facts()
        self._can_undo(lambda: (memory.remember(text), self.brain.refresh_facts(),
                                "Вернул в память: %s." % text.rstrip("."))[2])
        return "Забыл: %s." % to_you(text).rstrip(".")  # "у тебя есть кот", not "у меня" as if Orpheus's

    def do_about_me(self, m, now):
        """"Что ты обо мне знаешь?" - the facts themselves, for the model to retell and add nothing to:
        asked again after "забудь …", it once made up "вы любите чай" from what was said before."""
        facts = self.brain.active.facts()
        if not facts:
            return "Пока я ничего о тебе не знаю. Расскажи — запомню."
        if not self.brain.personal:
            # all of it, said by the program: the model dropped half of six facts, took 20-30 s, and mixed in old ones
            return "Я помню: %s." % "; ".join(to_you(t).rstrip(" .") for _, t in facts[:15])
        # (told "перескажи на «вы»", Gemma 4 began "Вы знаете, что у вас аллергия…")
        return Context("всё, что ты помнишь о собеседнике, — только это: %s. Скажи от себя — «Я помню, что ты…» — "
                       "пересказав это на «ты», коротко, ничего не добавляя" % "; ".join(t.rstrip(".") for _, t in facts))

    def do_moved(self, m, now):
        place = re.sub(r"[,.]?\s*(?:запомни|запиши|учти)\b.*$", "", m.slots.get("place", ""), flags=re.I).strip(" ,.!")
        if not re.search(r"\w{2}", place):
            return None
        if m.template.startswith("я [сейчас] живу") and not place[:1].isupper():
            return None  # "я живу в своё удовольствие": no place
        place = " ".join(w[:1].upper() + w[1:] if w.islower() and len(w) > 2 else w for w in place.split())
        moved = re.search(r"переехал|перебрал", self.heard, re.I) is not None
        fact = ("Я переехал в %s" if moved else "Я живу в %s") % place
        memory = self.brain.active
        old = [(i, t) for i, t in memory.facts() if re.search(r"(?i)живу|живёт|живет|переехал|перебрал|проживаю", t)]
        for i, _ in old[1:]:
            memory.forget(i)
        if old:
            memory.update_fact(old[0][0], fact)
        else:
            memory.remember(fact)
        self.brain.refresh_facts()
        return "Запомнил: ты %s в %s." % ("переехал" if moved else "живёшь", place)

    def do_my_birthday(self, m, now):
        """"Мой день рождения 15 марта": kept as a fact with the date, what "сколько до моего дня
        рождения?" is counted to."""
        when = m.slots.get("when", "")
        if not when:  # "когда у меня день рождения?"
            said = next((t for _, t in self.brain.memory.facts() if re.search(r"(?i)день рождения", t)), None)
            if not said:
                return "Ты мне ещё не говорил, когда у тебя день рождения."
            return "Ваш %s." % re.sub(r"(?i)^(?:мой|у меня)\s+", "", said).rstrip(".")
        span = period(when, now.date())
        if span is None or span[0] != span[1]:
            return None
        d = span[0]
        said = "%d %s" % (d.day, MONTHS[d.month - 1])
        memory = self.brain.memory
        old = next(((i, t) for i, t in memory.facts() if re.search(r"(?i)рожд", t)), None)
        if old:
            memory.update_fact(old[0], "Мой день рождения %s" % said)
        else:
            memory.remember("Мой день рождения %s" % said)
        self.brain.refresh_facts()
        return "Запомнил: твой день рождения %s." % said

    def do_forget_all(self, m, now):
        memory = self.brain.active
        facts = memory.facts()
        if not facts:
            return "Я и так ничего о тебе не помню."

        def forget():
            for fid, _ in facts:
                memory.forget(fid)
            self.brain.refresh_facts()
            self._can_undo(lambda: ([memory.remember(t) for _, t in facts], self.brain.refresh_facts(),
                                    "Вернул в память всё, что забыл.")[2])
            return "Забыл всё, что помнил о тебе."

        return self._ask("Забыть всё, что я о тебе помню — %d %s?" % (len(facts), plural(len(facts), "факт", "факта", "фактов")),
                         forget, no="Хорошо, всё помню как было.")

    def do_my_name(self, m, now):
        name = re.sub(r"[^\w\s-]", "", m.slots.get("name", "")).strip()
        words = name.split()
        if not words or len(words) > 2 or re.search(r"\d", name) or words[0].lower() in NOT_A_NAME:
            return None  # "меня зовут на работу" is not a name
        name = " ".join(w[:1].upper() + w[1:] for w in words)  # the recogniser may write it small
        memory = self.brain.memory
        old = next(((i, t) for i, t in memory.facts() if re.match(r"(?i)меня зовут\s", t)), None)
        if old:
            memory.update_fact(old[0], "Меня зовут %s" % name)
        else:
            memory.remember("Меня зовут %s" % name)
        self.brain.refresh_facts()
        return "Запомнил: тебя зовут %s." % name if old else "Приятно познакомиться, %s." % name

    def do_ask_name(self, m, now):
        name = self.owner_name()
        return "Тебя зовут %s." % name if name else None  # else the model may know it from the conversation

    def _best_fact(self, memory, query):
        facts = memory.facts()
        if not facts:
            return None
        stems = _stems(query)
        scored = [(sum(1 for st in stems if st in text.lower().replace("ё", "е")), (fid, text)) for fid, text in facts]
        best = max(scored, key=lambda x: x[0])
        if best[0]:
            return best[1]
        if memory.embedder is not None:
            vecs = memory.embedder.embed([t for _, t in facts])
            q = memory.embedder.embed([query])[0]
            scores = vecs @ q
            baseline = float((memory.embedder.anchors() @ q).mean())
            i = int(np.argmax(scores))
            if scores[i] - baseline >= RECALL_MARGIN:
                return facts[i]
        return None

    # ---------------------------------------------------------------------------------- planner

    def do_plan_left(self, m, now):
        if self.planner is None:
            return None
        return self.planner.left_today()

    def do_plan_next(self, m, now):
        if self.planner is None:
            return None
        return self.planner.next_item()

    def do_plan_ask(self, m, now):
        if self.planner is None:
            return None
        t = self.heard
        if BUSIEST.search(t) or FREEST.search(t):  # "какой день самый загруженный?" (not a question of the date)
            today = now.date()
            span = period(t, today) if _has_day(t) else None
            if span is None or span[0] == span[1]:
                span = (today, today + timedelta(days=6))
            return self._busiest(*span, today, freest=FREEST.search(t) is not None)
        if ABOUT_DATE.search(t) or re.search(r"заметк|памят", t, re.I):
            return None
        if m.template.startswith(("[а] у меня", "[а] {q}", "напомни")) and "?" not in t \
                and not re.search(r"(?:когда|во\s+сколько)(?:\s+(?:у\s+меня|будет))?\s*$", normalize_low(t)):
            return None  # "у меня завтра день рождения." tells, "напомни, что завтра врач" asks to add
        today = now.date()
        year = re.search(r"(?<!\d)(20\d\d)(?!\d)", t)
        if year and int(year.group(1)) != today.year:
            return None  # "когда будет ЕГЭ по физике в 2027": the world's, not the plans'
        # "что у меня вечером", "что у меня завтра утром", "какие у меня задачи", "что по задачам на завтра": the program's
        # (the model got them and, with the claim check of the time, said "Этого я не сделал")
        part = PART_WORDS.search(t)
        bare = normalize_low(re.sub(r"(?i)(?:сегодня|завтра|послезавтра)|" + PART_WORDS.pattern, " ", t))
        if part and re.fullmatch(r"\s*(?:а\s+)?(?:что|чего|есть\s+что\s+нибудь|что\s+нибудь\s+есть)\s+(?:у\s+меня\s*)?(?:есть\s*)?(?:по\s+планам\s*)?", bare):
            return self._part_listing(t, part, today)
        if re.search(r"(?<!\w)задач", t, re.I) and re.fullmatch(
                r"\s*(?:а\s+)?(?:какие|что)\s+(?:у\s+меня\s+)?(?:еще\s+)?(?:по\s+)?задач\w*\s*(?:на\s+(?:сегодня|завтра|послезавтра|неделю|неделе))?\s*", normalize_low(t)):
            return self._tasks(t, today)
        if _has_day(t):
            span = period(t, today) or (today, today)
            if re.search(r"(?<!\w)сколько(?!\w)", t, re.I) and _just_listing(re.sub(r"(?i)сколько", " ", t)):
                return self._count(t, *span)
            if _just_listing(t):
                return _say(self.planner.plans(t, spoken=True))
            return self._plan_answer(t, *span) or Context("из планировщика: " + self.planner.about(t, *span))
        if DURATION.search(t):  # "сколько длится ЕГЭ?": the one in the plans, if there is one
            answer = self._duration(t, today)
            if answer:
                return answer
        if WHEN.search(t) or EXISTS.search(t):  # "когда у меня пробник?" - no day named: look around
            span = (today - timedelta(days=7), today + timedelta(days=60))
            answer = self._plan_answer(t, *span, near=False)
            if answer:
                return answer
            about = self.planner.about(t, *span, only=True)
            if about:
                return Context("из планировщика: " + about)
        return None

    def _busiest(self, first, last, today, freest=False):
        """"Какой день на неделе самый загруженный / свободный?" -> the day, counted, and what is on it."""
        items = [i for i in self.planner.planner.items(first, last) if not i["done"]]
        days = [first + timedelta(days=k) for k in range((last - first).days + 1)]
        on = {d: [i for i in items if i["date"] == d.isoformat()] for d in days}
        day = (min if freest else max)(days, key=lambda d: len(on[d]))  # of equal ones, the nearest
        n = len(on[day])
        self.planner.focus = on[day]
        said = "%s: %s" % (spoken_day(day, today), "; ".join(spoken_item(i) for i in on[day]) if n else "ничего не запланировано")
        if not items:
            return "%s в планах ничего нет." % _cap(self._when(first, last, today))
        if freest:
            return "Самый свободный день — %s." % said
        return "Самый загруженный день — %s, %d %s: %s." % (spoken_day(day, today), n, plural(n, "дело", "дела", "дел"),
                                                            "; ".join(spoken_item(i) for i in on[day]))

    def _duration(self, t, today):
        """"Сколько длится ЕГЭ?" -> "ЕГЭ — 1 час 40 минут: завтра, …, с 22:00 до 23:40." (the model, told the
        plans are there only if asked about, once explained how long exams are in general)."""
        span = period(t, today) if _has_day(t) else (today - timedelta(days=1), today + timedelta(days=60))
        timed = [i for i in self.planner.matching(t, *span) or [] if i.get("start_time") and i.get("end_time")]
        if not timed:
            return None
        ahead = [i for i in timed if i["date"] >= today.isoformat()] or timed
        item = min(ahead, key=lambda i: (i["date"], i["start_time"]))
        hours, minutes = divmod(_minutes(item["end_time"]) - _minutes(item["start_time"]), 60)
        took = " ".join(["%d %s" % (hours, plural(hours, "час", "часа", "часов"))] * bool(hours) +
                        ["%d %s" % (minutes, plural(minutes, "минута", "минуты", "минут"))] * bool(minutes or not hours))
        self.planner.focus = [item]
        return "%s — %s: %s." % (item["title"], took, self.planner.describe(item))

    def _plan_answer(self, t, first, last, near=True):
        """"есть ли завтра русский в 10?" -> "Да: в 10:00 Занятие по русскому."; "во сколько завтра
        русский?", "когда пробник?" -> the item itself; "есть ли завтра физика?" with nothing even like
        it -> "Нет". None: the model's (with the facts)."""
        exists = EXISTS.search(t)
        if not exists and not WHEN.search(t):
            return None
        found_ = self.planner.matching(t, first, last)
        if not found_:
            if exists and near and self.planner.related(t, first, last) == []:
                # not a word of it in the plans of those days: a plain "no", the day's plans after it
                items = self.planner.planner.items(first, last)
                self.planner.focus = items
                when = self._when(first, last, now_date=self.planner.today())
                answer = "Нет, %s такого в планах нет." % when
                if items and len(items) <= 4:
                    answer += " Есть: %s." % "; ".join(spoken_item(i) for i in items)
                return answer
            return None  # "занятие по физике" and there is "Занятие по русскому": the model sorts it out
        if re.search(r"следующ|ближайш", t, re.I):  # "когда следующая тренировка?": the first one still ahead
            now = self.planner.now()
            ahead = [i for i in found_ if i["date"] > now.date().isoformat() or i["date"] == now.date().isoformat() and
                     (i.get("start_time") or "99:99") >= now.strftime("%H:%M")]
            found_ = sorted(ahead, key=lambda i: (i["date"], i.get("start_time") or ""))[:1] or found_
        self.planner.focus = found_
        one_day = first == last
        said = "; ".join(spoken_item(i) if one_day else self.planner.describe(i) for i in found_[:3])
        return ("Да: %s." if exists else "%s.") % (said if exists else _cap(said))

    def do_plan_free(self, m, now):
        """"Я завтра вечером свободен?" - what is planned for that part of that day (the model once
        answered "да, свободны с 22:00" with the exam at 22:00 right in front of it)."""
        if self.planner is None:
            return None
        when = m.slots.get("when", "") or self.heard  # "а у меня вечером что-нибудь есть?": the part of the day is in the phrase
        if not _has_day(when) and _has_day(self.heard):
            when = self.heard  # "а в 15 я свободен завтра": the day after the time (it looked at today)
        today = now.date()
        span = period(when, today) if _has_day(when) else (today, today)
        if span is None or span[0] != span[1]:
            return None
        day = span[0]
        low = normalize_low(when)
        part = next(((name, lo, hi) for key, name, lo, hi in (("утр", "утром", 6, 12), ("дн", "днём", 12, 18), ("обед", "в обед", 12, 15),
                                                                ("вечер", "вечером", 18, 24), ("ноч", "ночью", 0, 6))
                     if re.search(r"(?<!\w)%s" % key, low)), None)
        items = self.planner.planner.items(day, day)
        events = [i for i in items if i.get("start_time") and not i["done"]]

        def minutes(t):
            h, mm = map(int, t.split(":"))
            return h * 60 + mm
        # "я свободен завтра в три?": that hour, not the whole day (15:00 got "Нет" for the physics at 12)
        _, at = self.planner._named(to_digits(when), heard=False) if _has_time(when) else (None, None)
        if at and minutes(at) < 8 * 60 and not re.search(r"утр|ноч", when, re.I):
            at = "%02d:%s" % (int(at[:2]) + 12, at[3:])  # "в три": 15:00, as the day is asked about
        if at:
            lo_m, hi_m = minutes(at), minutes(at) + 60
            part = ("в %s" % at, lo_m / 60, hi_m / 60)
        if part:
            _, lo, hi = part
            busy = [i for i in events if minutes(i["start_time"]) < hi * 60 and
                    minutes(i.get("end_time") or i["start_time"]) + (0 if i.get("end_time") else 60) > lo * 60]
        else:
            busy = events
        self.planner.focus = busy
        when_said = self._when(day, day, today) + (" " + part[0] if part else "")
        asked_busy = re.search(r"(?<!\w)занят", self.heard, re.I) is not None  # "занят ли я": the other way round
        if busy:
            return "%s, %s: %s." % ("Да" if asked_busy else "Нет", when_said, "; ".join(spoken_item(i) for i in busy))
        tasks = [i for i in items if i["kind"] == "task" and not i["done"]]
        answer = "%s, %s ничего не запланировано." % ("Нет" if asked_busy else "Да", when_said)
        if tasks:
            answer += " Из задач на день: %s." % "; ".join("«%s»" % i["title"] for i in tasks)
        return answer

    def _part_listing(self, t, part, today):
        span = period(t, today) if _has_day(t) else (today, today)
        day = span[0] if span else today
        name, lo, hi = PARTS[part.group(0).lower()[:3]]
        events = [i for i in self.planner.planner.items(day, day) if i.get("start_time") and not i["done"]
                  and lo * 60 <= _minutes(i["start_time"]) < hi * 60]
        self.planner.focus = events
        when = "%s %s" % (_upper_first_s(self._when(day, day, today)), name)
        return "%s: %s." % (when, "; ".join(spoken_item(i) for i in events)) if events else "%s ничего не запланировано." % when

    def _tasks(self, t, today):
        span = period(t, today) if _has_day(t) or re.search(r"недел", t, re.I) else (today, today + timedelta(days=7))
        tasks = [i for i in self.planner.planner.items(*span) if i["kind"] == "task" and not i["done"]]
        self.planner.focus = tasks
        if not tasks:
            return "Открытых задач там нет."
        return "Задачи: %s." % "; ".join("%s — «%s»" % (self._when(date.fromisoformat(i["date"]), date.fromisoformat(i["date"]), today),
                                                        i["title"]) for i in tasks)

    def _count(self, t, first, last):
        """"сколько у меня завтра дел?" -> the number and the list."""
        items = self.planner.planner.items(first, last)
        if re.search(r"задач", t, re.I):
            items = [i for i in items if i["kind"] == "task"]
        elif re.search(r"встреч|событи|мероприяти", t, re.I):
            items = [i for i in items if i["kind"] == "event"]
        self.planner.focus = items
        when = self._when(first, last, now_date=self.planner.today())
        if not items:
            return "%s в планах ничего нет." % _cap(when)
        n = len(items)
        return "%s %d %s: %s." % (_cap(when), n, plural(n, "дело", "дела", "дел"), "; ".join(spoken_item(i) for i in items))

    @staticmethod
    def _when(first, last, now_date):
        """How the days of an answer are said: "завтра", "в понедельник, 28 сентября", "с … по …"."""
        if first != last:
            return "с %s по %s" % (spoken_day(first, now_date), spoken_day(last, now_date))
        near = {0: "сегодня", 1: "завтра", 2: "послезавтра", -1: "вчера"}.get((first - now_date).days)
        if near:
            return near
        return "%s %s" % ("во" if first.weekday() == 1 else "в", spoken_day(first, now_date, acc=True))

    def do_plan_add(self, m, now):
        if self.planner is None:
            return None
        what = m.slots.get("what", "")
        # "можешь добавить встречу завтра в 12?", "добавишь …?": a request asked as a question
        polite = m.template.startswith(("(можешь", "(добавишь"))
        if ("?" in self.heard and not polite) or re.search(r"заметк|памят", what, re.I):
            return None  # "напомни, что у меня завтра?" asks; "добавь в заметки" is a note
        if m.template.startswith("запиши") and not (_has_day(what) or _has_time(what)):
            return None
        what = re.sub(r"^(?:что|чтобы)\s+", "", what, flags=re.I)
        before = re.fullmatch(r"\s*за\s+((?:\S+\s+)?(?:час\w*|минут\w*|полчаса))\s*", what, re.I)
        if before and len(self.planner.focus) != 1:
            self.wanted = (self.turn, "за %s до" % before.group(1))
            return "За %s до чего напомнить?" % before.group(1)
        if before and len(self.planner.focus) == 1:
            # "и напомни за час" after a plan was talked of: its own reminder (it made a task «За час»)
            return self.do_plan_remind(dataclasses.replace(m, slots={"before": before.group(1),
                                                                     "what": self.planner.focus[0]["title"]}), now)
        bare = re.sub(r"(?<!\w)(?:напоминание|задачу|задача|дело|событие|мне)(?!\w)", " ", what, flags=re.I).strip(" .,!")
        if not bare or when_only(bare):
            # "напомни", "добавь задачу", "напомни через 20 минут", "поставь напоминание на завтра в 8": what is asked for
            self.wanted = (self.turn, bare)
            return "Что напомнить?" if re.search(r"напомн|напомин", self.heard, re.I) else "Что добавить?"
        if re.match(r"\W*(?:поставь|переставь)", self.heard, re.I) and (_has_day(what) or _has_time(what)):
            # "поставь хакатон на завтра на 19" with a hackathon in the plans: that one, moved (it made a second one)
            item, _ = self.planner._find(what, strict=True)
            if item is not None and all(st in normalize_low(what) for st in _stems(item["title"])):
                answer = self.planner.move_item(item, what)
                self._undo_move(item, answer)
                self.handled = "plan_move"
                return _say(answer)
        pronoun = re.match(r"^(?:его|её|ее|их)\s+(.+)$", what.strip(), re.I)
        if pronoun and self.planner.focus and len(self.planner.focus) == 1 and when_only(pronoun.group(1)):
            # "поставь его на 9 утра": the one just talked of, moved (it made an event «Его»)
            before = self.planner.focus[0]
            answer = self.planner.move_item(before, pronoun.group(1))
            self._undo_move(before, answer)
            self.handled = "plan_move"
            return _say(answer)
        if m.template.startswith(("[мне] (надо|", "не (забудь|")) and re.search(
                r"(?<!\w)(?:было|вчера|позавчера|прошл\w*)(?!\w)", self.heard, re.I):
            return None  # "мне надо было вчера в 5 позвонить": regret, not a plan
        if m.template.startswith(("[мне] (надо|", "не (забудь|")) and not (_has_day(what) or _has_time(what)) \
                and not DUTY.match(re.sub(r"^(?:не\s+забыть|мне|что|надо|нужно)\s+", "", what.strip(), flags=re.I)):
            return None  # "мне надо отдохнуть", "надо подумать": said, not a task
        if m.template.startswith(("(добавь|", "(можешь", "(добавишь", "{what} (добавь|")) and not (_has_day(what) or _has_time(what)) \
                and not PLAN_MARKED.search(self.heard) and not PLAN_LIKE.search(what) \
                and not re.search(r"(?<!\w)(?:запланируй|запланировать|назначь|забронируй)(?!\w)", self.heard, re.I):
            # "добавь сахар в чай", "поставь чайник", "добавь громкости" (each became a task for today): the
            # model's, which can still add a plan by its own tool if it is one
            return None
        # "добавь задачу купить молоко", "внеси в план ...": not part of the title
        what = " ".join(ADD_WORDS.sub(" ", what).split())
        # "добавь, короче, на послезавтра созвон" («Эээ добавь короче созвон»)
        what = " ".join(SPOKEN_FILLERS.sub(" ", what).split()).strip(" ,:;")
        if not re.search(r"\w", what):
            return None
        # "через час и пятнадцать минут": 75 minutes (it was an hour, and «И 15 минут выпить воду»)
        hour_and = r"через\s+(?:1\s+|один\s+)?час\s+и\s+(\d+)\s+минут\w*"
        if re.search(hour_and, to_digits(what), re.I):
            what = re.sub(hour_and, lambda x: "через %d минут" % (60 + int(x.group(1))), to_digits(what), flags=re.I)
        # "напомни завтра утром сдать долг": a time of day, not a word of the title (it became «Утром сдать долг»)
        if not _has_time(what):
            what = PART_OF_DAY.sub(lambda p: " в %s " % PART_TIME[p.group(1).lower()[:3]], what, count=1).strip()
        pieces = re.split(r"\s*[,;]\s*(?=(?:в|к)\s+\d)|\s+и\s+(?=(?:в|к)\s+\d)", to_digits(what))
        if len(pieces) > 1 and all(re.search(r"(?:^|\s)(?:в|к)\s+\d", p) for p in pieces) and not REPEAT_WEEK.search(what):
            return self._add_several(pieces)
        if not (_has_day(what) or _has_time(what)) and EVENT_NOUN.search(what) and not PLAN_MARKED.search(self.heard) \
                and not m.template.startswith("напомни"):
            # "добавь встречу": an event with no day or time - asked for, and the answer completes it (a task
            # «Встреча» for today, and then «Завтра в 12» said "пустое название")
            self.awaiting = (self.turn, what)
            return "На когда поставить «%s»?" % tidy_title(what)
        repeat = None
        counted = REPEAT_COUNT.search(to_digits(what))
        if counted:  # "с повтором 10 недель": how many times; without it half a year of weeks, a year of months
            what = REPEAT_COUNT.sub(" ", to_digits(what))
        days, what = repeat_days(what)
        planner = self.planner
        if days:
            return self._add_weekly(" ".join(what.split()), days, counted)
        days, rest = several_days(what)
        if days and len(days) > 1:
            if counted or REPEAT_WEEK.search(rest):
                return self._add_weekly(" ".join(REPEAT_WEEK.sub(" ", rest).split()), days, counted)
            return self._add_on_days(" ".join(rest.split()), days)
        if REPEAT_WEEK.search(what):
            repeat, what = ("week", 26), REPEAT_WEEK.sub(" ", what)
        elif REPEAT_MONTH.search(what):
            repeat, what = ("month", 12), REPEAT_MONTH.sub(" ", what)
        if repeat and counted:
            n, unit = int(counted.group(1)), counted.group(2).lower()
            repeat = (repeat[0], min(52 if repeat[0] == "week" else 24,
                                     n * 4 if unit.startswith("месяц") and repeat[0] == "week" else n))
        what = " ".join(what.split())
        answer = planner.add(what)
        added = planner.last_added
        if added is not None and repeat:
            answer = planner.repeat_item(added, *repeat)
            added = planner.last_added
        if added is not None:
            if added.get("series"):
                self._can_undo(lambda: (planner.planner.delete_series(added["series"]), "Убрал всю серию «%s»." % added["title"])[1])
            else:
                self._can_undo(lambda: (planner.planner.delete(added["id"]), "Убрал из планов: %s." % planner.describe(added))[1])
        return answer

    def _add_on_days(self, what, days):
        """"Тренировку в понедельник, среду и пятницу в 6 утра": one item on each of the coming days."""
        planner = self.planner
        made = []
        for d in days:
            planner.add("%s %s" % (DAYS_ACC[d], what))
            if planner.last_added is not None:
                made.append(planner.last_added)
        if not made:
            return "Не получилось добавить."
        planner.focus = made
        self._can_undo(lambda: ([planner.planner.delete(i["id"]) for i in made], "Убрал добавленное: %d." % len(made))[1])
        dates = sorted(date.fromisoformat(i["date"]) for i in made)
        return "Добавил %s: %s." % ("; ".join("%s, %d %s" % (DAYS_ACC[d.weekday()], d.day, MONTHS[d.month - 1]) for d in dates),
                                    spoken_item(made[0]))

    def _add_several(self, pieces):
        """"Добавь на завтра: в 9 пробежка, в 13 обед с Никитой": each its item, on the day the first names."""
        planner = self.planner
        pieces = [" ".join(p.replace(":", " ", 1 if re.match(r"^\D*:", p) else 0).split()) for p in pieces]  # "завтра: в 9 …"
        head = re.match(r"^(.*?)(?=(?:^|\s)(?:в|к)\s+\d)", pieces[0]).group(1).strip(" :,")
        made = []
        for i, piece in enumerate(pieces):
            planner.add(piece if i == 0 or not head else "%s %s" % (head, piece))
            if planner.last_added is not None:
                made.append(planner.last_added)
        if not made:
            return "Не получилось добавить."
        planner.focus = made
        self._can_undo(lambda: ([planner.planner.delete(i["id"]) for i in made], "Убрал добавленное: %d." % len(made))[1])
        days = {i["date"] for i in made}
        if len(days) == 1:
            return "Добавил %s: %s." % (spoken_day(date.fromisoformat(made[0]["date"]), self.planner.today(), acc=True),
                                        "; ".join(spoken_item(i) for i in made))
        return "Добавил: %s." % "; ".join(planner.describe(i) for i in made)

    def _add_weekly(self, what, days, counted):
        """"Тренировку по понедельникам, средам и пятницам в 6 утра, 10 недель": a weekly series for each day."""
        weeks = 26
        if counted:
            n, unit = int(counted.group(1)), counted.group(2).lower()
            weeks = n * 4 if unit.startswith("месяц") else n
        weeks = max(1, min(weeks, 52))
        planner = self.planner
        made = []
        for d in days:
            planner.add("%s %s" % (DAYS_ACC[d], what))
            if planner.last_added is None:
                continue
            planner.repeat_item(planner.last_added, "week", weeks)
            made.append(planner.last_added)
        if not made:
            return "Не получилось добавить."
        planner.focus = made
        self._can_undo(lambda: (
            [planner.planner.delete_series(i["series"]) if i.get("series") else planner.planner.delete(i["id"]) for i in made],
            "Убрал все серии «%s»." % made[0]["title"])[1])
        first = min(date.fromisoformat(i["date"]) for i in made)
        return "Добавил %s, %d %s, с %d %s: %s." % (days_said(days), weeks, plural(weeks, "неделю", "недели", "недель"),
                                                  first.day, MONTHS[first.month - 1], spoken_item(made[0]))

    def _item(self, what, strict=False):
        """The planner item [what] names -> (item, None) or (None, what to say). "его", "это",
        "второе", "последнее" are the items the last answer was about (planner.focus)."""
        what = _strip(what)
        low = normalize_low(what).strip()
        focus = self.planner.focus
        if not low or PRONOUN.match(low):
            if len(focus) == 1:
                return focus[0], None
            if focus:
                return None, "Какое именно: %s?" % "; ".join(spoken_item(i) for i in focus[:4])
            return None, None
        if re.match(r"^последн\w*\s+(?:добавленн|созданн|записанн)\w*", low) and self.planner.last_added:
            return self._fresh(self.planner.last_added), None
        ordinal = ORDINAL.match(low)
        if ordinal:
            listed = self.planner.listed or focus
            k = next(v for key, v in ORDINALS.items() if ordinal.group(1).startswith(key))
            if listed and -len(listed) <= k < len(listed):
                return self._fresh(listed[k]), None
            return None, None
        item, problem = self.planner._find(what, strict=strict)
        return item, (_say(problem) if problem else None)

    def _fresh(self, item):
        """The item as it is now (a list read out before may have been changed since: "перенеси
        третье", then "а первое удали")."""
        d = date.fromisoformat(item["date"])
        now = next((i for i in self.planner.planner.items(d - timedelta(days=60), d + timedelta(days=400))
                    if i["id"] == item["id"]), None)
        return now or item

    def _done(self, item):
        answer = self.planner.done_item(item)
        planner = self.planner
        self._can_undo(lambda: planner.done_item(item, done=False))
        return answer

    def do_plan_done(self, m, now):
        if self.planner is None:
            return None
        if ALL_OF.match(normalize_low(_strip(m.slots.get("what", "")))):
            return self._done_all(m.slots.get("what", ""), now)
        # "отметь, что я купил хлеб": the title is "хлеб", not "что я купил"
        what = re.sub(r"^(?:что|то,?\s+что)\s+(?:я\s+|мы\s+)?(?:уже\s+)?(?:\w+(?:л|ла|ли)\s+)?", "",
                      m.slots.get("what", "").strip(), flags=re.I)
        # "отметь, что хлеб купил": the verb after it
        what = re.sub(r"\s+(?:уже\s+)?\w+(?:ил|ал|ял|ел|ыл|ул|ла|ли)$", "", what, flags=re.I) if len(what.split()) > 1 else what
        item, problem = self._item(what, strict=True)
        if item is None:
            # "ужин готов" is not about the plans when there is nothing like it there
            return None if m.template.startswith("{what}") else problem
        return self._done(item)

    def do_plan_did(self, m, now):
        """"я купил хлеб": the open task of this week with those words is done."""
        if self.planner is None:
            return None
        what = _strip(re.sub(r"\W+(отметь|вычеркни|отмечай)\b.*$", "", m.slots.get("what", ""), flags=re.I))
        what = re.sub(r"^(?:с|со)\s+", "", what, flags=re.I)  # "с рефератом закончил"
        item, problem = self.planner._find(what, strict=True)
        if item and not item["done"] and (re.search(r"\bотмет|вычеркн", self.heard, re.I) or
                                          abs((date.fromisoformat(item["date"]) - now.date()).days) <= 7):
            return self._done(item)
        return None

    def _done_all(self, what, now):
        """"Отметь все задачи на сегодня выполненными": each open one of that day; "верни" takes it back."""
        today = now.date()
        span = period(what, today) if _has_day(what) else (today, today)
        items = [i for i in self.planner.planner.items(*span) if not i["done"]]
        if re.search(r"задач", what, re.I):
            items = [i for i in items if i["kind"] == "task"]
        if not items:
            return "Там отмечать нечего: всё уже сделано или ничего нет."
        planner = self.planner
        planner.done_all(items)
        self._can_undo(lambda: (planner.done_all(items, done=False), "Снял отметки: %d." % len(items))[1])
        return "Отметил сделанными: %s." % "; ".join("«%s»" % i["title"] for i in items)

    def do_plan_rename(self, m, now):
        if self.planner is None:
            return None
        item, problem = self._item(m.slots.get("what", ""), strict=True)
        if item is None:
            return None if m.template.startswith("[а] {what} теперь") else problem  # "он теперь называется": not a plan's
        old = item["title"]
        planner = self.planner
        answer = planner.rename_item(item, m.slots.get("name", ""))
        renamed = planner.focus[0]
        self._can_undo(lambda: (planner.rename_item(renamed, old), "Вернул название «%s»." % old)[1])
        return answer

    def do_plan_repeat(self, m, now):
        """"Сделай повтор тренировки 10 недель": an item already there repeats; "его", or nothing, is the one just
        added or talked about, "зарядки" by the days is all of its days."""
        if self.planner is None:
            return None
        said = to_digits(" ".join(m.slots.get(k, "") for k in ("what", "rest")))
        counted = REPEAT_COUNT.search(said)
        # "повторяй за мной", "повтори, что ты сказал", "повтори два раза": not about the plans unless one is named
        loose = m.template.startswith(("повтори ", "(повторяй", "[а] {what} (повторяй"))
        if m.template.startswith("повтори ") and not counted:
            return None
        unit = "month" if REPEAT_MONTH.search(said) or REPEAT_MONTH.search(self.heard) else "week"
        count = 26 if unit == "week" else 12
        if counted:
            n, per = int(counted.group(1)), counted.group(2).lower()
            count = n * 4 if per.startswith("месяц") and unit == "week" else n
        count = max(1, min(count, 52 if unit == "week" else 24))
        what = REPEAT_MONTH.sub(" ", REPEAT_WEEK.sub(" ", REPEAT_COUNT.sub(" ", said)))
        what = re.sub(r"(?<!\w)(?:каждую\s+неделю|каждый\s+месяц)(?!\w)", " ", what, flags=re.I)
        what = re.sub(r"^\s*(?:для|у|на|к)\s+|\s+(?:на|по)\s*$", " ", what.strip(), flags=re.I).strip()
        planner = self.planner
        # "для этих тренировок", "этим": the ones just talked about (a word after it only names them)
        these = re.match(r"^(?:эт(?:и|их|им|ими|у|от|ой|ого|ом)|всех|все|них|ним|им|их)(?!\w)\s*", normalize_low(what))
        if these and planner.focus:
            what = ""
        if not what or PRONOUN.match(normalize_low(what).strip()):
            if loose:
                return None
            targets = list(planner.focus)
            if not targets:
                return "Какое событие повторять?"
            if len({(i["title"], i.get("start_time")) for i in targets}) > 1:  # a list read out: which of them?
                return "Какое именно: %s?" % "; ".join(spoken_item(i) for i in targets[:4])
        else:
            item, problem = self._item(what)
            if item is None:
                return None if loose else (problem or "Не нашёл в планах «%s»." % what)
            targets = [item]
        heads = {}
        for t in targets:
            for h in planner.twins(t):
                heads.setdefault(h.get("series") or h["id"], h)
        made = planner.set_repeat(sorted(heads.values(), key=lambda i: i["date"]), unit, count)
        if not made:
            return "Не получилось."
        self._undo_series(made, planner.replaced, "Вернул повтор как было: «%s»." % made[0]["title"])
        first = min(date.fromisoformat(i["date"]) for i in made)
        days = sorted({date.fromisoformat(i["date"]).weekday() for i in made})
        how = ("каждую неделю" if len(days) == 1 else days_said(days)) if unit == "week" else "каждый месяц"
        return "Теперь «%s» повторяется %s, %d %s, с %d %s." % (
            made[0]["title"], how, count, plural(count, "раз", "раза", "раз") if unit == "month" or len(days) == 1
            else plural(count, "неделю", "недели", "недель"), first.day, MONTHS[first.month - 1])

    def _undo_series(self, made, replaced, said):
        """"Верни" after a repeat was changed: the new items go, the old ones come back as they were."""
        planner = self.planner
        made, replaced = list(made), list(replaced)

        def undo():
            for i in made:
                if i.get("series"):
                    planner.planner.delete_series(i["series"])
                else:
                    planner.planner.delete(i["id"])
            planner.restore_all(replaced)
            return said

        self._can_undo(undo)

    def do_plan_repeat_off(self, m, now):
        if self.planner is None:
            return None
        what = m.slots.get("what", "")
        if not what.strip() or PRONOUN.match(normalize_low(what).strip()):
            if not self.planner.focus:
                return "У чего убрать повтор?"
            item = self.planner.focus[0]
        else:
            item, problem = self._item(what)
            if item is None:
                return problem or "Не нашёл в планах «%s»." % what
        if not item.get("series"):
            return "«%s» и так не повторяется." % item["title"]
        kept = self.planner.stop_repeat(item)
        self._undo_series(kept, self.planner.replaced, "Вернул повтор «%s»." % item["title"])
        return "Повтор убрал, остался ближайший раз: %s." % "; ".join(self.planner.describe(i) for i in kept)

    def do_plan_move(self, m, now):
        if self.planner is None:
            return None
        what, to = m.slots.get("what", ""), m.slots.get("to", "")
        # "перенеси её лучше на четверг", "а Хакатон тогда на 14": not a part of what is moved
        what = " ".join(re.sub(r"(?<!\w)(?:лучше|тогда|пожалуй|уж|же|тоже|все\s+же|всё\s+же)(?!\w)", " ", what, flags=re.I).split())
        if m.template.startswith("[а|и] {what} (тогда") and not when_only(to):
            return None  # "а я тогда на работу": no time
        if m.template.startswith("[а|и|нет|не] (ее|") and not (when_only(to) or shift_of(to_digits(to))):
            return None  # "а его зовут Тимур": no day or time
        no_verb = m.template.startswith(("[а|и] {what} (давай", "[а|и] {what} не (в", "сделай {what}", "[а] {what} {to}",
                                         "[а|и] {what} на {to}", "[а] {what} (переезжает"))
        if m.template.startswith("[а] {what} (переезжает"):
            part = re.match(r"^\s*(?:на\s+)?(вечер|утро|день)\s+(?:часов\s+)?на\s+(\S+)", to, re.I)
            if part:  # "на вечер часов на 7": 19:00, not 07:00
                to = "на %s %s" % (part.group(2), {"вечер": "вечера", "утро": "утра", "день": "дня"}[part.group(1).lower()])
            to = re.sub(r"^\s*(?:на\s+)?вечер\s*$", "на 19:00", to)
        if m.template.startswith("[а|и] {what} на {to}"):
            to = "на " + to
        named = re.sub(r"(?<!\w)(?:нет|не|ой|да|ну|блин|лучше|давай|хотя|а|и|вот|так)(?!\w)", " ", what, flags=re.I)
        if no_verb and ("?" in self.heard or not re.search(r"\w{2,}", named)
                        or not when_only(re.sub(r"\s+(?:перенес[её]м|поставим)$", "", to))):
            return None  # "а ЕГЭ на завтра?" asks; "сделай музыку громче" is no time
        to = re.sub(r"\s+(?:перенес[её]м|поставим)$", "", to)
        if m.slots.get("span"):
            to = "с " + m.slots["span"]
            if planner_time_span(to_digits(to)) is None:
                return None  # "сделай перевод с английского"
        if m.template.startswith(("(измени|поменяй) {what}",)) and not (
                _has_day(to) or _has_time(to) or shift_of(to_digits(to)) or re.search(r"\d", to_digits(to))
                or re.search(r"(?<!\w)(?:утр|вечер|обед|полдень|полночь|ноч|днём|днем)", to, re.I)):
            # "поменяй тренировку на пробежку": a new name; "на 15", "на семь", "на вечер" are a time
            return self.do_plan_rename(dataclasses.replace(m, slots={"what": what, "name": to}), now)
        # "перенеси зарядку на 7": all of a repeat; "перенеси завтрашнюю зарядку на 8": that day only
        whole = not (_has_day(what) or re.search(r"(?:сегодняшн|завтрашн|послезавтрашн|ближайш|следующ|это[тй]|эту)\w*", what, re.I))
        low = normalize_low(_strip(what)).strip()
        if not low or PRONOUN.match(low) or ORDINAL.match(low) or re.match(r"^последн\w*\s+добавленн", low):
            item, problem = self._item(what)
            if item is None:
                return problem
        else:
            item, problem = self.planner._find(_strip(what), when=what, heard=False, strict=True)
            if problem:
                # "сделай перевод с английского", "давай разговор на потом отложим": not about the plans
                loose = m.slots.get("span") or no_verb or m.template.startswith(("[давай] {what}", "(перекинь|", "[а|и] {what}"))
                if problem.startswith("уточни, какое именно"):
                    return _say(problem)  # several of that name: asked, not a second one added ("русский перенесли на завтра")
                return None if loose else _say(problem)
        planner = self.planner
        if re.match(r"\W*(?:отложи|отодвинь|сдвинь|передвинь)", self.heard, re.I) and re.fullmatch(
                r"\s*(?:на\s+)?(?:\d+\s+)?(?:час|часа|часов|полчаса|минут\w*)\s*", to_digits(to), re.I):
            to = to + " позже"  # "отложи русский на час": later by that, not "на какое время?"
        by_days = re.fullmatch(r"\s*(?:на\s+)?(?:(\d+)\s+)?(день|дня|дней|сутки|суток|неделю|недели|недель)\s*", to_digits(to), re.I)
        if by_days:
            # "отложи хакатон на 2 дня", "отложи ЕГЭ на неделю": the day moves ("на 2" was read as 14:00)
            n = int(by_days.group(1) or 1) * (7 if by_days.group(2).lower().startswith("недел") else 1)
            new = date.fromisoformat(item["date"]) + timedelta(days=n)
            moved = {k: v for k, v in dict(item, date=new.isoformat()).items() if k != "updated_at"}
            planner.planner.save(moved)  # the date itself: "на 3 октября" was read as 03:00
            planner.focus, planner.last_write = [moved], planner.turn
            answer = "Перенёс на %s: %s." % (spoken_day(new, planner.today(), acc=True), spoken_item(moved))
        else:
            answer = planner.move_item(item, to, whole=whole)
        self._undo_move(item, answer)
        if planner.replaced and answer.startswith("Перенёс") and "все повторы" in answer:
            before = list(planner.replaced)
            self._can_undo(lambda: ([planner.planner.save({k: v for k, v in i.items() if k != "updated_at"}) for i in before],
                                    "Вернул время «%s»." % item["title"])[1])
        return _say(answer)

    def _fixable(self):
        """One item added or moved in the last three turns, with nothing else talked of since (the model's turns
        in between were such corrections, not taken)."""
        planner = self.planner
        return planner is not None and len(planner.focus) == 1 and 0 < planner.turn - planner.last_write <= 3 \
            and self.side_turn < planner.last_write

    def _correction(self, text):
        """"Не в 12, а в 14", "блин, не в три, в четыре", "ой, в три", "не, на четверг", "нет, в час" soon after
        adding or moving one item: it goes there. Such bits went to the model, which said "переношу на 15:00"
        four times over and moved nothing."""
        planner = self.planner
        if not self._fixable():
            return None
        fixed = CORRECTION.match(text)
        rest = text[fixed.end():].strip(" .!?,") if fixed else ""
        if not fixed or not fixed.group(0).strip() or not when_only(rest):
            return None
        rest = re.sub(r"^(в|к)\s+час(?:\s+дня)?$", r"\1 13:00", rest, flags=re.I)  # "в час": 13:00, as said of the day
        self.handled = "plan_fix"
        before = planner.focus[0]
        answer = planner.move_item(before, rest)
        self._undo_move(before, answer)
        return _say(answer)

    def do_plan_remind(self, m, now):
        """"Напомни завтра за час до хакатона": the plan's own reminder, that much before it (it made a task
        «За час до хакатона»)."""
        if self.planner is None:
            return None
        before = to_digits(m.slots.get("before", "")).lower()
        n = re.search(r"\d+", before)
        minutes = 30 if "полчас" in before else (int(n.group(0)) if n else 1) * (60 if "час" in before else 1)
        if not re.search(r"час|минут", before):
            return None
        item, problem = self.planner._find(m.slots.get("what", ""), strict=True)
        if item is None:
            return _say(problem)
        item = self.planner.next_of(item)
        if not item.get("start_time"):
            return "У «%s» нет времени начала: напомнить не к чему." % item["title"]
        saved = {k: v for k, v in dict(item, remind=minutes).items() if k != "updated_at"}
        self.planner.planner.save(saved)
        self.planner.focus = [saved]
        return "Напомню за %d %s до «%s»: %s в %s." % (minutes, plural(minutes, "минуту", "минуты", "минут"), item["title"],
                                                     spoken_day(date.fromisoformat(item["date"]), now.date()), item["start_time"])

    def do_plan_fix(self, m, now):
        """"нет, лучше на 15" right after adding or moving: that item goes there instead. Only a day and a time:
        "в пятницу в 8 вечера иду на концерт" said right after adding is a plan of its own (it moved the
        item added before to Friday)."""
        planner = self.planner
        if m.slots.get("new"):
            # "нет, не с Димой, а с Аней": the item just added, the other person in its title
            planner = self.planner
            if planner is None or not planner.focus or len(planner.focus) != 1:
                return None
            item = planner.focus[0]
            old = _stems(m.slots.get("old", ""))
            title = item["title"]
            word = next((w for w in title.split() if old and normalize_low(w).startswith(old[0][:3])), None)
            if word is None:
                return None
            answer = planner.rename_item(item, title.replace(word, m.slots["new"].strip(" .!")))
            return answer
        if not self._fixable():
            return None
        if not when_only(m.slots.get("to", "")):
            return None
        before = planner.focus[0]
        answer = planner.move_item(before, m.slots.get("to", ""))
        self._undo_move(before, answer)
        return _say(answer)

    def do_plan_delete(self, m, now):
        if self.planner is None:
            return None
        what = _strip(m.slots.get("what", ""))
        if not re.search(r"\w", what) and not self.planner.focus:
            return None
        if m.template.startswith(("[а] {what} (отменяется", "я [сегодня|завтра] не", "у меня [сегодня|завтра] нет")):
            # "хакатон отменяется": told as news; the plan of that name, if there is one, is asked about
            item, _ = self.planner._find(what, strict=True)
            return self.confirm_delete(item) if item is not None else None
        if re.search(r"напоминани", what, re.I):
            return self._reminders_off(now)  # "отмени все напоминания": the reminders, not the plans ("за какой день удалить всё?")
        if ALL_OF.match(normalize_low(what)) or m.template.startswith("(очисти|"):
            return self._delete_all(what, now)
        if NOT_PLANS.search(what):
            return None  # "отмени, я передумал", "удали это из головы, забудь" ("не нашёл в планах «я передумал»")
        whole = re.match(r"^(?:вс[юе]\s+)?(?:сери[юи]|повтор\w*|все\s+повтор\w*)\s+", normalize_low(what))
        if whole:  # "удали всю серию английский": every repeat (it deleted one, the next)
            series = self._series_named(what[whole.end():])
            if series:
                return series
        item, problem = self._item(what)
        if item is None:
            return problem
        return self.confirm_delete(item)

    def _reminders_off(self, now):
        today = now.date()
        items = [i for i in self.planner.planner.items(today, today + timedelta(days=60)) if i.get("remind") is not None and not i["done"]]
        if not items:
            return "Напоминаний и так нет."
        planner = self.planner

        def off():
            for i in items:
                planner.planner.save({k: v for k, v in dict(i, remind=None).items() if k != "updated_at"})
            self._can_undo(lambda: ([planner.planner.save({k: v for k, v in i.items() if k != "updated_at"}) for i in items],
                                    "Вернул напоминания.")[1])
            return "Снял напоминания у %d %s; сами дела остались." % (len(items), plural(len(items), "дела", "дел", "дел"))
        return self._ask("Снять напоминания у %d %s: %s?" % (len(items), plural(len(items), "дела", "дел", "дел"),
                                                            ", ".join("«%s»" % i["title"] for i in items[:5])), off)

    def confirm_delete(self, item):
        """"Удалить …?" - and the next phrase's yes or no is taken here, not by the model (it once took
        "нет, не удаляй" for a yes)."""
        planner = self.planner

        def delete():
            answer = planner.remove(item)
            self._can_undo(planner.restore)
            return answer

        # one of a repeat: said so, and how the whole of it goes ("удали зарядку" took the next one, silently)
        also = " Это один из повторов; чтобы убрать все, скажи «удали все повторы %s»." % item["title"].lower() if item.get("series") else ""
        return self._ask("Удалить %s?%s" % (planner.describe(item), also), delete, item=item)

    def _other_day(self, asked, text, now):
        """"Удалить сегодня — занятие по русскому?" - "нет, завтрашнее": the item of that title on the day now named,
        or None ("Хорошо, не удаляю", and the one meant was never offered)."""
        if asked is None or self.planner is None:
            return None
        low = normalize_low(text)
        rel = next((n for stem, n in (("сегодняшн", 0), ("послезавтрашн", 2), ("завтрашн", 1)) if stem in low), None)
        today = now.date()
        if rel is not None:
            first = last = today + timedelta(days=rel)
        elif _has_day(text):
            span = period(text, today)
            if span is None:
                return None
            first, last = span
        else:
            return None
        same = [i for i in self.planner.planner.items(first, last)
                if i["title"] == asked["title"] and i["id"] != asked["id"]]
        return same[0] if same else None

    def _delete_all(self, what, now):
        """"удали все дела на завтра": the day's list, and all of it goes after a yes; "кроме русского" stays."""
        today = now.date()
        if re.search(r"что\s+(?:я\s+|мы\s+)?(?:\w+\s+)?(?:менял|изменил|поменял|натворил|добавил|переносил|перен[её]с)\w*", what, re.I):
            # "отмени всё, что я сегодня менял" was taken for "delete all of today" (asked, but still)
            return "Возвращаю по одному шагу, с последнего: скажи «верни», и ещё раз, если нужно."
        # "удали всё на сегодня кроме русского": the exception was ignored, and the Russian lesson deleted too
        but = re.split(r"\s*,?\s*(?:кроме|за\s+исключением|но\s+не|только\s+не)\s+", what, maxsplit=1, flags=re.I)
        what, keep = but[0], (but[1] if len(but) > 1 else "")
        day_in_keep = DAY_EXPR.search(to_digits(keep)) if keep and not _has_day(what) else None
        if day_in_keep:  # "удали всё кроме физики на сегодня": the day came after the exception
            what, keep = what + " " + day_in_keep.group(0), to_digits(keep).replace(day_in_keep.group(0), " ")
        span = period(what, today) if _has_day(what) else None
        if span is None:
            series = self._series_named(what)
            if series:
                return series
            # "удали всё кроме физики": the day is asked, and the answer ("сегодня") completes it (it went to the model)
            self.wanted_day = (self.turn, "удали всё %%s%s" % (" кроме " + keep if keep else ""))
            return "Скажи, за какой день удалить всё."
        items = self.planner.planner.items(*span)
        kept = []
        if keep:
            stems = _stems(keep)
            kept = [i for i in items if stems and any(st in i["title"].lower().replace("ё", "е") for st in stems)]
            if not kept:
                return "Не нашёл там «%s», так что не удаляю ничего: скажи ещё раз, что оставить." % keep.strip(" .?!")
            items = [i for i in items if i not in kept]
        if not items:
            return "Там и так ничего нет." if not kept else "Кроме этого, там ничего нет."
        planner = self.planner

        def delete():
            answer = planner.remove_all(items)
            self._can_undo(lambda: planner.restore_all(items))
            return answer

        when = self._when(span[0], span[1], today)
        besides = ", кроме «%s»," % "», «".join(i["title"] for i in kept) if kept else ""
        return self._ask("Удалить всё %s%s — %s?" % (when, besides, "; ".join(spoken_item(i) for i in items)), delete)

    def _series_named(self, what):
        """"удали все зарядки": every repeat of it (and of its other days), after a yes; None when it names no repeat."""
        rest = re.sub(r"^(?:вс[её]|весь)\s+", "", normalize_low(what).strip())
        if not rest:
            return None
        item, _ = self._item(rest)
        if item is None or not item.get("series"):
            return None
        planner = self.planner
        items = [i for h in planner.twins(item) for i in planner.series_items(h)]

        def delete():
            answer = planner.remove_all(items)
            self._can_undo(lambda: planner.restore_all(items))
            return answer

        first = min(date.fromisoformat(i["date"]) for i in items)
        return self._ask("Удалить все повторы «%s» с %d %s — %d %s?" % (
            item["title"], first.day, MONTHS[first.month - 1], len(items), plural(len(items), "раз", "раза", "раз")), delete)

    def _undo_move(self, before, answer):
        """A move is undone by "верни как было" too ("нет, верни обратно" after one got "я ничего не удалял")."""
        if not answer.startswith(("Перенёс на", "Теперь")) or "все повторы" in answer:
            return  # a series' own undo is kept by do_plan_move
        planner = self.planner
        old = {k: v for k, v in before.items() if k != "updated_at"}

        def back():
            planner.planner.save(old)
            planner.focus = [old]
            return "Вернул как было: %s — %s." % (spoken_day(date.fromisoformat(old["date"]), planner.today()), spoken_item(old))
        self._can_undo(back)

    def do_undo(self, m, now):
        what = m.slots.get("what", "")
        if what and self.planner is not None and self.planner.deleted:  # "верни русский"
            return _say(self.planner.restore(what))
        if self.undo_stack:
            return self.undo_stack.pop()()
        if self.planner is not None and self.planner.deleted:  # deleted by the model's own tool
            return _say(self.planner.restore())
        if DISMISS.search(self.heard):
            return "Хорошо."  # "не надо" with nothing to undo: no more than that
        return "Возвращать нечего: я ничего не менял."

    # ---------------------------------------------------------------------------------- talk

    def do_repeat(self, m, now):
        return self.brain.last_reply or "Я пока ничего не говорил."

    def owner_name(self):
        """The owner's name as the memory has it ("Меня зовут Иван"), or ""."""
        for _, t in self.brain.memory.facts():
            found_ = re.match(r"(?i)меня зовут\s+(.+?)\.?$", t)
            if found_:
                return found_.group(1)
        return ""

    def do_greet(self, m, now):
        h = now.hour
        hello = "Доброе утро" if 5 <= h < 12 else "Добрый день" if 12 <= h < 17 else "Добрый вечер" if 17 <= h < 23 else "Доброй ночи"
        name = self.owner_name()
        hello += ", %s" % name if name else ""
        # the first greeting of the day (a day starts at 4 in the morning) brings the day's summary
        day = (now - timedelta(hours=4)).date().isoformat()
        memory = self.brain.memory
        if memory.meta("summary_day") != day:
            memory.meta("summary_day", day)
            return "%s! %s" % (hello, self._day_summary(now))
        return "%s. %s" % (hello, self.rng.choice(["Чем займёмся?", "Слушаю.", "Все системы в порядке. Что на повестке?"]))

    def _day_summary(self, now):
        """"Сегодня понедельник, 28 сентября. По плану: …. <погода>": what the day is, what is still ahead in it
        (the past hours' plans are not read out), the weather at home."""
        said = ["Сегодня %s." % _day_text(now.date())]
        if self.planner is not None:
            try:
                running, ahead, tasks = self.planner.left_items()
                parts = [spoken_item(i) for i in running + ahead] + ["задача «%s»" % i["title"] for i in tasks]
                said.append("По плану: %s." % "; ".join(parts) if parts else "Планов на сегодня нет.")
            except Unavailable:
                said.append("Планировщик сейчас не отвечает.")
                running, ahead = [], []
        else:
            running, ahead = [], []
        if self.weather is not None:
            try:
                said.append(self.weather.day_text(self.weather.home, now.date()))
                # "Завтра дождь, а у тебя пробежка": what is done outside, when rain is expected
                outside = [i for i in running + ahead if OUTDOOR.search(i.get("title") or "")]
                if outside and self.weather.precipitation(self.weather.home, now.date()).startswith("Да"):
                    said.append("Возьми зонт: дождь, а у тебя %s." % spoken_item(outside[0]))
            except Offline:
                pass
        return " ".join(said)

    def do_thanks(self, m, now):
        return self.rng.choice(["Всегда пожалуйста.", "Рад стараться.", "Для этого я здесь."])

    def do_bye(self, m, now):
        if re.search(r"ночи", self.heard, re.I) or now.hour >= 22 or now.hour < 4:
            return "Спокойной ночи."
        return self.rng.choice(["До связи.", "Хорошего дня." if now.hour < 17 else "Хорошего вечера."])

    def do_stop(self, m, now):
        if not re.search(r"стоп|хватит", normalize_low(self.heard)) and self.brain.last_reply.rstrip().endswith("?"):
            return None  # "сто" to "сколько?" is an answer, not a mis-heard "стоп"
        return ""  # the server tells the phone to stop waiting for more (server.py, "listen": false)

    def do_pause(self, m, now):
        return "Хорошо, не слушаю. Чтобы я снова слушал, нажми «Слушать снова» или «Говорить»."

    def do_ack(self, m, now):
        if re.search(r"спасибо|благодар", self.heard, re.I):  # "ладно, спасибо": "спасибо" went as a filler
            return self.do_thanks(m, now)
        if DISMISS.search(self.heard):  # "ладно, забей": whatever was asked, dropped
            return "Хорошо."
        if self.brain.last_reply.rstrip().endswith("?"):
            return None  # the model asked something: "да"/"нет" is its answer, the model's to take
        # "нет" to "Скажи, за какой день удалить всё": silence sounded like nothing was heard
        return "Хорошо." if NO.search(self.heard) else ""

    def do_how_are_you(self, m, now):
        return self.rng.choice(["Все системы в норме, спасибо. А у тебя?", "Работаю исправно, жалоб не поступало. Как ты?",
                                "Отлично, спасибо, что спросил. Как твой день?", "Лучше не бывает — по крайней мере, у программ."])

    def do_who(self, m, now):
        return "Я Орфей, твой личный голосовой ассистент."

    def do_capabilities(self, m, now):
        if self.weather is None:
            return ("Веду твой планировщик и заметки, помню то, что ты просишь запомнить, называю время и дату, считаю. "
                    "А обо всём остальном можно просто поговорить.")
        return ("Веду твой планировщик и заметки, помню то, что ты просишь запомнить, подскажу время, дату и погоду, "
                "посчитаю и поищу в интернете. А обо всём остальном можно просто поговорить.")

    def do_reset(self, m, now):
        self.brain.reset()
        return "Хорошо, начнём с чистого листа."

    def do_coin(self, m, now):
        return self.rng.choice(["Орёл.", "Решка."])

    def do_dice(self, m, now):
        return "Выпало %d." % self.rng.randint(1, 6)

    def do_random_number(self, m, now):
        a = to_digits(m.slots.get("a", "") or "1")
        b = to_digits(m.slots.get("b", "") or "100")
        try:
            lo, hi = sorted((int(re.sub(r"\D", "", a) or 1), int(re.sub(r"\D", "", b) or 100)))
        except ValueError:
            return None
        return "Пусть будет %d." % self.rng.randint(lo, hi)

    # ---------------------------------------------------------------------------------- not yet

    # ---------------------------------------------------------------------------------- weather

    def _weather_ask(self, m, now, answer):
        """Common to the weather scenarios: the city and the day from the phrase; the forecast
        comes over the network only when the city is not the home one (that one is kept fresh)."""
        if self.weather is None:
            return "Погоды у меня нет: выход в интернет выключен."
        rest = m.slots.get("rest", "") + " " + self.heard
        city = _city(self.heard)
        written_small = city is not None and not city.group(1)[:1].isupper()
        today = now.date()
        span = period(rest, today) if _has_day(rest) else None
        first, last = span if span else (today, today)
        said_day = span is not None

        def fetch():
            try:
                place = self.weather.place(city.group(1)) if city else self.weather.home
                if place is None:
                    if not written_small:
                        return "Такого города я не нашёл."
                    place = self.weather.home  # "в городе", "в деревне": not a name
                return answer(place, first, last, said_day)
            except Offline:
                return "Погоду сейчас не получить: нет связи с интернетом."

        return Lookup("", fetch)

    def weather_text(self, when="", city="", now=None):
        """The model's weather tool: the same answers, for a day ("завтра", "выходные") and a city."""
        if self.weather is None:
            return "ошибка: погоды нет, выход в интернет выключен"
        today = (now or datetime.now()).date()
        try:
            place = self.weather.place("в " + city) if re.search(r"\w", city or "") else self.weather.home
            if place is None:
                return "нет такого города: %s" % city
            if place != self.weather.home:  # the model names it "Казань": "в городе Казань", not "в Казань"
                place = dataclasses.replace(place, where="в городе " + place.name)
            span = period(when, today) if _has_day(when or "") else None
            if span is None:
                return "%s %s" % (self.weather.now_text(place), self.weather.day_text(place, today))
            if span[0] == span[1]:
                return self.weather.day_text(place, span[0])
            return self.weather.days_text(place, *span)
        except Offline:
            return "ошибка: нет связи с интернетом"

    def _part_of_day(self, now):
        """"вечером", "завтра утром": (word, from hour, to hour, days on) - "ночью" is the night after the day,
        but asked at 2 at night, it is this one."""
        part = next(((word, lo, hi, shift) for key, word, lo, hi, shift in PARTS_OF_DAY
                     if re.search(r"(?<!\w)%s" % key, self.heard, re.I)), None)
        if part and part[3] and now.hour < 6 and not _has_day(self.heard):
            part = part[:3] + (0,)
        return part

    def do_weather(self, m, now):
        # "какая погода вечером?", "завтра утром": from the hourly forecast
        part = self._part_of_day(now)

        def answer(place, first, last, said_day):
            if part and first == last:
                word, lo, hi, shift = part
                text = self.weather.hours_text(place, first + timedelta(days=shift), lo, hi, word, named=first)
                if text:
                    return text
            if not said_day:
                return self.weather.now_text(place)
            if first != last:
                return self.weather.days_text(place, first, last)
            return self.weather.day_text(place, first)
        return self._weather_ask(m, now, answer)

    def do_weather_degrees(self, m, now):
        if m.template.startswith(("[а] (в|во) {rest}", "[а] {rest} сколько")) and len(m.slots.get("rest", "").split()) > 3:
            return None  # "стоит ли в субботу ехать за город, будет тепло?": no city before "тепло"
        part = self._part_of_day(now)

        def answer(place, first, last, said_day):
            if part:  # "сколько градусов ночью", "ночью будет холодно": that part of the day, not now
                word, lo, hi, shift = part
                text = self.weather.hours_text(place, first + timedelta(days=shift), lo, hi, word, named=first)
                if text:
                    return text
            return self.weather.day_text(place, first) if said_day and first != now.date() else self.weather.degrees(place)
        return self._weather_ask(m, now, answer)

    def do_weather_wind(self, m, now):
        return self._weather_ask(m, now, lambda place, first, last, said_day:
                                 self.weather.wind_text(place, first if said_day else None))

    def do_weather_humidity(self, m, now):
        return self._weather_ask(m, now, lambda place, first, last, said_day:
                                 self.weather.humidity_text(place, first if said_day else None))

    def do_weather_pressure(self, m, now):
        if self.weather is None or not re.search(r"(?<!\w)давлени", self.heard, re.I) or re.search(
                r"артериальн|кров|у\s+меня|высок|низк|голов|давит", self.heard, re.I):  # "давление у меня высокое": health
            return None
        return "Давления в моём прогнозе нет: только температура, осадки и ветер."

    def do_weather_sun(self, m, now):
        rise, sets = re.search(r"восход|рассвет", self.heard, re.I), re.search(r"закат|заход|темне", self.heard, re.I)
        which = "sunrise" if rise and not sets else "sunset" if sets and not rise else None
        return self._weather_ask(m, now, lambda place, first, last, said_day: self.weather.sun_text(place, first, which))

    def do_weather_rain(self, m, now):
        if re.search(r"гроз", self.heard, re.I):
            return self._weather_ask(m, now, lambda place, first, last, said_day: self.weather.thunder(place, first, last))
        snow = re.search(r"снег|снегопад", self.heard, re.I) is not None
        return self._weather_ask(m, now, lambda place, first, last, said_day:
                                 self.weather.precipitation_days(place, first, last, snow))

    def do_weather_if(self, m, now):
        """"Если завтра будет дождь, напомни взять зонт": the forecast is looked at, and the reminder
        goes into the plans only when rain (or snow) is expected or likely."""
        cond = normalize_low(m.slots.get("cond", ""))
        if self.weather is None or self.planner is None or not re.search(r"дожд|снег|ливен|гроз|осадк", cond):
            return None
        snow = "снег" in cond
        today = now.date()
        span = period(self.heard, today) if _has_day(self.heard) else (today, today)
        day = DAY_EXPR.search(to_digits(self.heard))
        city = _city(self.heard)
        what = m.slots.get("what", "")
        negated = re.search(r"(?<!\w)не\s+(?:будет|пойд[её]т|обещают|ожидается|польет|польёт)", self.heard, re.I) is not None

        def fetch():
            try:
                place = (self.weather.place(city.group(1)) if city else None) or self.weather.home
                forecast = self.weather.precipitation_days(place, span[0], span[1], snow)
            except Offline:
                return "Погоду сейчас не узнать, нет связи с интернетом, так что напоминание не ставлю."
            dry = forecast.startswith("Нет")
            if dry != negated:  # "если будет дождь" and none, "если не будет дождя" and it will
                return "%s Так что %s." % (forecast, "напоминание не ставлю" if re.search(r"напомни", self.heard, re.I)
                                            else "не добавляю")
            added = self.planner.add(("%s %s" % (day.group(0), what)) if day else what)
            item = self.planner.last_added
            if item is not None:
                planner = self.planner
                self._can_undo(lambda: (planner.planner.delete(item["id"]), "Убрал из планов: %s." % planner.describe(item))[1])
            return "%s %s" % (forecast, added)
        return Lookup("", fetch)

    def do_plan_if(self, m, now):
        """"Посмотри, что у меня в четверг, и если там пусто — добавь тренировку в 18", "если я свободен в пятницу,
        запиши кино в 19": the day is looked at by the program, and the plan goes in only when it is empty (or,
        "свободен", when that time is free); else what is there is said and "добавить всё равно?" asked.
        The model did it right, but in 21 s."""
        if self.planner is None:
            return None
        what = m.slots.get("what", "").strip(" ,.!?—-")
        said = to_digits("%s %s" % (m.slots.get("day", ""), what))
        today = now.date()
        span = period(said, today) if _has_day(said) else None
        if not re.search(r"\w{2}", what) or span is None or span[0] != span[1]:
            return None
        day = span[0]
        items = [i for i in self.planner.planner.items(day, day) if not i["done"]]
        # the time may be with the day: "если я свободен завтра в 18, запиши кино"
        at, till = _clock(said, now, "в|к|с"), _clock(said, now, "до")
        when_said = self._when(day, day, today)
        if at and re.search(r"свобод|не\s+занят", normalize_low(self.heard)):  # that time free, not the whole day
            lo = _minutes(at)
            hi = _minutes(till) if till and _minutes(till) > lo else lo + 60
            busy = [i for i in items if i.get("start_time") and _minutes(i["start_time"]) < hi and
                    _minutes(i.get("end_time") or i["start_time"]) + (0 if i.get("end_time") else 60) > lo]
            free = "%s в %s свободно." % (_cap(when_said), at)
        else:
            busy = items
            free = "%s пусто." % _cap(when_said)
        planner = self.planner

        def add():
            added = planner.add(what if _has_day(what) else said)
            item = planner.last_added
            if item is not None:
                self._can_undo(lambda: (planner.planner.delete(item["id"]), "Убрал из планов: %s." % planner.describe(item))[1])
            return added
        if not busy:
            return "%s %s" % (free, add())
        self.planner.focus = busy
        return self._ask("%s: %s. Добавить всё равно?" % (_cap(when_said), "; ".join(spoken_item(i) for i in busy)),
                         add, "Хорошо, не добавляю.")

    def do_rate(self, m, now):
        """"Какой курс доллара?", "100 евро в рублях", "сколько долларов на 10 000 рублей": the Central Bank's
        rates (the search's snippets name the sites, not the numbers: "посмотрите на cbr.ru"). Not a
        currency ("курс биткоина"): the search's."""
        if not currencies(to_digits(self.heard)):
            # "курс биткоина": not the Central Bank's - the search's (the model said it had no rate, searching nothing)
            if self.search is not None and not self.brain.personal and re.search(r"курс", self.heard, re.I) \
                    and not any(x.intent == "fresh" for x in self.intents.match(self.heard)):
                return self._search_lookup(search_query(self.heard))
            return None
        if self.search is None:
            return "Курс сейчас не узнать: выход в интернет выключен."
        heard, today = self.heard, now.date()
        tomorrow = re.search(r"(?<!\w)завтра", heard, re.I) is not None

        def fetch():
            try:
                rates, day = self.search.rates(today + timedelta(days=1) if tomorrow else today)
            except Offline:
                return "Курс сейчас не получить: ЦБ не отвечает."
            return rate_text(heard, rates, day, today, tomorrow)
        return Lookup("", fetch)

    def do_fresh(self, m, now):
        """"Какие новости?", "кто выиграл …", "сколько стоит биткоин", "какая сейчас инфляция": what the model
        cannot know - searched at once (asked by itself, it once answered "в найденном нет" having searched
        nothing, answered the inflation from its memory, and Gemma 4 writes its search now and then so that
        Ollama drops it: nothing said at all)."""
        if self.search is None:
            return None
        if m.template.startswith("[а] (какой|какая") and MINE.search(self.heard):
            return None  # "какая сейчас у меня задача?": not the internet's
        if "(прилетит|" in m.template and self.planner is not None:
            item, _ = self.planner._find(m.slots.get("query", ""), strict=True)
            if item is not None:
                return None  # "когда начнётся тренировка?": the owner's own, plan_ask's
        if currencies(to_digits(self.heard)) and re.search(r"курс|сто[ия]т|рубл|цен|стоимост", self.heard, re.I):
            return self.do_rate(m, now)  # "выясни, какой курс доллара": the Central Bank's, with the numbers
        if self.brain.personal:
            return "В личном разделе я не ищу в интернете."
        return self._search_lookup(" ".join(re.sub(r"[^\w\s-]", " ", self.heard).split()))

    def do_weather_clothes(self, m, now):
        part = self._part_of_day(now)  # "нужна ли куртка завтра утром?": by the morning, not the day's warmest hour
        return self._weather_ask(m, now, lambda place, first, last, said_day: self.weather.clothes(
            place, first + timedelta(days=part[3]) if part else first, (part[1], part[2], part[0]) if part else None,
            named=first))

    # ---------------------------------------------------------------------------------- search

    def do_wiki(self, m, now):
        subject = re.sub(r"^(?:такой|такая|такое|такие)\s+", "", m.slots.get("subject", ""), flags=re.I).strip()
        if self.search is None or self.brain.personal or not re.search(r"\w{2}", subject):
            return None
        own = [i for i in self.brain.active.recall(subject, facts=True) if i["kind"] in ("fact", "note")]
        if own or (self.planner is not None and self.planner.notes_recall(subject)):
            return None  # "кто такой Виктор?" - someone from the owner's own memory: the model's
        if SPECIFIC.search(self.heard):
            # "кто был первым президентом России": the article is about the office, not the answer
            return self._search_lookup(re.sub(r"^\W+|\W+$", "", self.heard))
        person = re.match(r"\W*кто", self.heard, re.I) is not None

        def fetch():
            try:
                return self.search.wikipedia(subject, person=person)
            except Offline:
                return None  # the model answers as it can
        return Lookup("", fetch)

    def do_web_search(self, m, now):
        query = m.slots.get("query", "").strip()
        if self.search is None:
            return "Искать в интернете я не могу: выход в интернет выключен."
        if self.brain.personal:
            return "В личном разделе я не ищу в интернете."
        if not re.search(r"\w{2}", query):
            return "Что поискать?"
        if re.search(r"курс|сто[ия]т|рубл", query, re.I) and currencies(query):
            return self.do_rate(m, now)  # "найди в интернете курс доллара": the Central Bank's, with the numbers
        if m.template.startswith("(найди|поищи|подбери) [мне] (рецепт"):  # "найди рецепт борща": not "борща" alone
            query = re.sub(r"^\W*(?:найди|поищи|подбери)\s+(?:мне\s+)?", "", self.heard, flags=re.I).strip(" .!?")
        return self._search_lookup(query)

    def offer_search(self, query):
        """The model asked "могу поискать в интернете — хотите?": a "да" to it makes that search."""
        self.pending = (self.turn, lambda: self._search_lookup(query), "Хорошо, не ищу.")

    def _search_lookup(self, query):
        def fetch():
            try:
                results = self.search.web_results(query)
            except Offline:
                # (DuckDuckGo shuts the exit node out with a captcha for hours now and then): what the model knows,
                # said as such - "кто был первым президентом России" is not left at "не отвечает"; its "не могу
                # найти" after it is no reason to search once more (17 s)
                def down():
                    context = Context(SEARCH_DOWN)  # not as a search's result: "поиск показал…", it said to that
                    context.tried = True
                    return context
                return Lookup("Поиск сейчас не отвечает.", down)
            if not results:
                return "Ничего не нашёл."
            return found(found_online(query, results), "web_search", query=query)
        return Lookup("Сейчас поищу.", fetch)

    def do_music(self, m, now):
        return "Музыку включать я пока не умею."

    def do_call(self, m, now):
        if re.search(r"такси", self.heard, re.I):
            return "Такси вызывать я пока не умею."
        return "Звонить я пока не умею."

    def do_message(self, m, now):
        return "Отправлять сообщения я пока не умею."

    def do_devices(self, m, now):
        return "Управлять устройствами я пока не умею."

    def do_timer(self, m, now):
        return "Таймеров у меня пока нет, но могу добавить напоминание в планы: скажи «напомни через десять минут …»."

    def do_alarm(self, m, now):
        return "Будильников у меня пока нет, но могу добавить напоминание в планы."


def normalize_low(text):
    return text.lower().replace("ё", "е")
