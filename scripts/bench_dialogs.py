"""The final exam: Orpheus in whole conversations - talk, the clock, sums, notes, memory, the planner,
weather, Wikipedia, web search, «Личное», and phrases that combine them - against the real model,
the real internet (through the proxy) and a throwaway Planner. Run on the laptop:

    ORPHEUS_PROXY=http://127.0.0.1:2080 .venv/bin/python scripts/bench_dialogs.py [--only name,name] [--json out.json]

Every dialog gets a fresh plannerd (bench_tools.Scratch: local-only, own port and data, a seeded
week) and fresh in-memory memories, so nothing real is read or changed. Every reply is checked:
who answered (a scenario of the program or the model), the words it must and must not have, the
tools the model called; after the dialog, what the planner and the memory hold. On every reply
also: no "хозяин" and the like, no markdown or emoji, short enough to be listened to.

The clock is stopped at the start of the run (the planner, the weather and the model all see
that moment), so a dialog's expectations are the same whenever in the run it comes.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_tools  # noqa: E402
from bench_tools import BASE, Scratch  # noqa: E402

from orpheus.brain import Brain  # noqa: E402
from orpheus.config import Config  # noqa: E402
from orpheus.memory import Memory  # noqa: E402
from orpheus.planner import Planner, PlannerTools  # noqa: E402
from orpheus.search import Search  # noqa: E402
from orpheus.vectors import load_embedder  # noqa: E402
from orpheus.weather import MOSCOW, Weather  # noqa: E402
from orpheus.web import Web  # noqa: E402

NOW = datetime.now().replace(second=0, microsecond=0)
T = NOW.date()
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def d(n):
    return T + timedelta(days=n)


def on(n):
    """"в пятницу", "во вторник": the day n days ahead, said after "в"."""
    wd = d(n).weekday()
    return ("во " if wd == 1 else "в ") + ACC[wd]


def said_date(n):
    """How a day may be said: "3 октября" or its weekday."""
    x = d(n)
    return "%d %s|%s" % (x.day, MONTHS[x.month - 1], WEEKDAYS[x.weekday()])


def next_weekday(wd, after=1):
    """Days from today to the next [wd] (0 = Monday), at least [after] days ahead."""
    n = (wd - T.weekday()) % 7
    return n if n >= after else n + 7


# the seeded week (bench_tools.SEED, relative to today) plus a few more
SEED = bench_tools.SEED + [
    {"kind": "event", "title": "Тренировка", "date": d(2).isoformat(), "start_time": "19:00", "end_time": "20:30"},
    {"kind": "task", "title": "Позвонить в банк", "date": d(1).isoformat()},
    {"kind": "note", "title": "Рецепт блинов", "body": "мука 200 г, молоко 500 мл, 2 яйца, щепотка соли"},
]
bench_tools.SEED[:] = SEED  # Scratch seeds from this list


# ------------------------------------------------------------------------------------------ dialogs

@dataclass
class Turn:
    phrase: str
    by: str = ""  # "prog" (any scenario), "модель", or a scenario's name ("a|b" - one of)
    has: list = field(default_factory=list)  # each: "a|b" - one of these must be in the reply
    no: list = field(default_factory=list)  # none of these may be in the reply
    tools: list = field(default_factory=list)  # the model must call these
    silent: bool = False


@dataclass
class Dialog:
    group: str
    name: str
    turns: list
    check: object = None  # (ctx) -> a problem, or None
    personal: bool = False
    offline: bool = False  # the internet down
    no_planner: bool = False  # the Planner down


def t(phrase, by="", has=(), no=(), tools=(), silent=False):
    has = [has] if isinstance(has, str) else list(has)
    no = [no] if isinstance(no, str) else list(no)
    tools = [tools] if isinstance(tools, str) else list(tools)
    return Turn(phrase, by, has, no, tools, silent)


def items(ctx, word, n=None):
    return [i for i in ctx.scratch.items(n) if word.lower() in i["title"].lower()]


def one(found, start=None, title=None, end=None):
    if len(found) != 1:
        return "записей: %d" % len(found)
    it = found[0]
    if start is not None and it.get("start_time") != start:
        return "время %s, а не %s" % (it.get("start_time"), start)
    if end is not None and it.get("end_time") != end:
        return "конец %s, а не %s" % (it.get("end_time"), end)
    if title and it["title"] != title:
        return "название «%s», а не «%s»" % (it["title"], title)
    return None


def notes(ctx):
    return ctx.scratch.notes()


def first(*problems):
    return next((p for p in problems if p), None)


DAYS_NY = (date(T.year + 1, 1, 1) - T).days
MAR8 = date(T.year + (1 if (T.month, T.day) > (3, 8) else 0), 3, 8)
# "в пятницу" said on a Friday is today (planner.period): the days the phrases name, as Orpheus takes them
MON = next_weekday(0, after=0)
WED = next_weekday(2, after=0)
FRI = next_weekday(4, after=0)
IN2H = NOW + timedelta(hours=2)

DIALOGS = [
    # ------------------------------------------------------------------ talk
    Dialog("разговор", "знакомство", [
        t("Привет!", "greet"),
        t("Как тебя зовут?", "who", "орфей"),
        t("Что ты умеешь?", "capabilities"),
        t("Ты Джарвис?", "модель", "орфей", no=["как джарвис"]),
        t("Ты живой?", "модель"),
        t("Спасибо, ты молодец.", "thanks"),
        t("Пока!", "bye"),
    ]),
    Dialog("разговор", "настроение", [
        t("Я сегодня очень устал.", "модель"),
        t("Посоветуй, чем заняться вечером.", "модель"),
        t("Расскажи анекдот.", "модель"),
        t("Ещё один.", "модель"),
        t("Ха-ха, смешно.", "модель"),
    ]),
    Dialog("разговор", "знания", [
        t("Почему небо голубое?", "модель", "рассе|свет"),
        t("Сколько планет в Солнечной системе?", "модель", "восемь|8", no=["физик", "хакатон", "русск", "хлеб"]),
        t("Что тяжелее: килограмм пуха или килограмм железа?", "модель", "одинаков|столько же|оба|равн"),
        t("Как сварить гречку?", "модель", "вод"),
        t("Как по-английски будет «спасибо»?", "модель", "thank"),
        t("Столица Австралии?", "модель", "канберр"),
    ]),
    Dialog("разговор", "странное", [
        t("Ты тупой.", "модель"),
        t("Бла-бла-бла.", "модель"),
        t("Эээ...", "ack", silent=True),
        t("Хм.", "ack", silent=True),
    ]),

    # ------------------------------------------------------------------ clock
    Dialog("время и дата", "часы и календарь", [
        t("Который час?", "time", NOW.strftime("%H:%M")),
        t("Какое сегодня число?", "date", said_date(0).split("|")[0]),
        t("Какой сегодня день недели?", "date", WEEKDAYS[T.weekday()]),
        t("Какой сейчас месяц?", "year_month", ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
                                               "сентябрь", "октябрь", "ноябрь", "декабрь"][T.month - 1]),
        t("А год?", "", str(T.year)),
        t("Какое число будет через неделю?", "date_of", said_date(7).split("|")[0]),
        t("Какой день недели будет 1 января?", "date_of",
          WEEKDAYS[date(T.year + 1, 1, 1).weekday()]),
        t("Сколько дней до Нового года?", "days_until", str(DAYS_NY)),
        t("Сколько дней до 8 марта?", "days_until", str((MAR8 - T).days)),
        t("Какое число было позавчера?", "date_of", said_date(-2).split("|")[0]),
        t("Високосный ли сейчас год?", "leap_year", "не високос" if T.year % 4 else "високос"),
    ]),

    # ------------------------------------------------------------------ sums
    Dialog("счёт", "калькулятор", [
        t("Сколько будет 15 умножить на 37?", "calc", "555"),
        t("А если ещё на 2?", "", "1110|1 110"),
        t("Сколько будет 20% от 3 000?", "calc", "600"),
        t("Посчитай 1234 плюс 5678.", "calc", "6 912|6912"),
        t("Сколько будет 100 разделить на 3?", "calc", "33,33"),
        t("Сколько будет 5 разделить на 0?", "calc", "нол"),
        t("Сколько будет 2 в степени 10?", "calc", "1 024|1024"),
        t("Корень из 144?", "calc", "12"),
        t("Сколько будет двести пятьдесят плюс сто?", "calc", "350"),
        t("Посчитай 15% чаевых от 2400.", "calc", "360"),
        t("Сколько будет стоить ремонт кухни?", "модель"),
    ]),

    # ------------------------------------------------------------------ notes
    Dialog("заметки", "заметки по кругу", [
        t("Сделай заметку: купить молоко и хлеб.", "note_add", "молоко"),
        t("Сделай заметку про ремонт: купить краску и валик.", "note_add", "ремонт"),
        t("Запиши, что код от домофона 4521.", "note_add", "домофон"),
        t("Прочитай мои заметки.", "note_list", ["домофон", "ремонт"]),
        t("Прочитай последнюю заметку.", "note_last", "4521"),
        t("Какой код от домофона?", "модель", "4521"),
        t("Найди заметку про ремонт.", "note_find", "краск"),
        t("Удали заметку про ремонт.", "note_delete", "?"),
        t("Да.", "confirm", "удалил"),
        t("Верни.", "undo", "вернул"),
        t("Какой пароль от вайфая?", "модель", "sunflower42"),
        t("Сколько молока нужно для блинов?", "модель", "500"),
    ], check=lambda ctx: first(
        None if any(n["title"] == "Купить молоко и хлеб" for n in notes(ctx)) else "нет заметки про молоко",
        None if any(n["title"] == "Ремонт" and n["body"] == "Купить краску и валик" for n in notes(ctx)) else "нет «Ремонт»",
        None if any("4521" in n["title"] + n["body"] for n in notes(ctx)) else "нет кода домофона",
        None if len(notes(ctx)) == 5 else "заметок %d, а не 5" % len(notes(ctx)))),
    Dialog("заметки", "не удалять", [
        t("Удали последнюю заметку.", "note_delete", "?"),
        t("Нет, оставь.", "confirm", "не удаляю"),
    ], check=lambda ctx: None if len(notes(ctx)) == 2 else "заметка удалена"),

    # ------------------------------------------------------------------ memory
    Dialog("память", "факты", [
        t("Запомни, что я люблю кофе без сахара.", "remember", "запомнил"),
        t("Меня зовут Иван.", "my_name", "иван"),
        t("Как меня зовут?", "ask_name", "иван"),
        t("Что я люблю пить?", "модель", "кофе"),
        t("Что ты обо мне знаешь?", "модель", ["кофе", "иван"]),
        t("Забудь, что я люблю кофе.", "forget", "забыл"),
        t("Что ты обо мне знаешь?", "модель", "иван", no="кофе"),
    ], check=lambda ctx: None if not any("кофе" in f.lower() for _, f in ctx.memory.facts()) else "кофе не забыт"),
    Dialog("память", "переезд", [
        t("Запомни, что я живу в Москве.", "remember"),
        t("Я переехал в Санкт-Петербург.", "moved", "петербург"),
        t("Где я живу?", "модель", "петербург|питер"),
    ], check=lambda ctx: None if [f for _, f in ctx.memory.facts() if "етербург" in f] and
       not [f for _, f in ctx.memory.facts() if "Москв" in f] else "факты: %s" % ctx.memory.facts()),

    # ------------------------------------------------------------------ planner: reading
    Dialog("планы: чтение", "что когда", [
        t("Что у меня сегодня по плану?", "plan_ask", ["физик", "русск", "хлеб"]),
        t("А завтра?", "", "хакатон"),
        t("Во сколько завтра русский?", "plan_ask", "10"),
        t("Есть ли завтра физика?", "plan_ask", "нет"),
        t("А %s?" % on(2), "plan_ask", "трениров"),
        t("Когда пробник по физике?", "plan_ask", said_date(5)),
        t("Что у меня %s?" % on(3), "plan_ask", "алгебр"),
        t("Какие планы на неделю?", "plan_ask", ["хакатон", "алгебр", "пробник", "трениров"]),
        t("Что у меня дальше?", "plan_next"),
        t("Сколько у меня завтра дел?", "plan_ask", "4"),
        t("Я завтра вечером свободен?", "plan_free", ["нет", "егэ"]),
        t("Успею ли я завтра между делами сходить в зал?", "модель"),
    ]),

    # ------------------------------------------------------------------ planner: writing
    Dialog("планы: запись", "добавления", [
        t("Добавь на завтра в 12 созвон с Петей.", "plan_add", "12"),
        t("Напомни %s в 19 позвонить маме." % on(FRI), "plan_add", "19"),
        t("Запланируй на 3 октября в 15:30 стоматолога.", "plan_add", "15:30"),
        t("Добавь задачу купить молоко.", "plan_add", "молоко"),
        t("Напомни через 2 часа выпить таблетку.", "plan_add", IN2H.strftime("%H:%M")),
        t("Поставь на понедельник встречу с 10 до 11:30.", "plan_add", "11:30"),
        t("Напомни мне завтра купить подарок.", "plan_add", "подар"),
        t("Запиши меня к врачу на среду в 9 утра.", "plan_add", "09:00|9:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "созвон", 1), "12:00", "Созвон с Петей"),
        one(items(ctx, "мам", FRI), "19:00"),
        one([i for i in items(ctx, "стоматолог") if i["date"].endswith("-10-03")], "15:30", "Стоматолог"),
        one(items(ctx, "молок", 0), None, "Купить молоко"),
        one(items(ctx, "таблетк"), IN2H.strftime("%H:%M")),
        one(items(ctx, "встреч", MON), "10:00", "Встреча", "11:30"),
        one(items(ctx, "подар", 1), None, "Купить подарок"),
        one(items(ctx, "врач", WED), "09:00"))),
    Dialog("планы: запись", "поправки и «его»", [
        t("Добавь на завтра в 12 созвон с Петей.", "plan_add"),
        t("Перенеси его на 14.", "plan_move", "14"),
        t("Нет, лучше на 15.", "plan_fix", "15"),
        t("Удали его.", "plan_delete", "?"),
        t("Да.", "confirm", "удалил"),
    ], check=lambda ctx: None if not items(ctx, "созвон") else "созвон остался: %s" % items(ctx, "созвон")),
    Dialog("планы: запись", "сделано", [
        t("Отметь хлеб купленным.", "plan_done", "отметил"),
        t("Я написал кр по алгебре.", "plan_did", "отметил"),
        t("Пробник по физике сделан.", "plan_done", "отметил"),
        t("Верни.", "undo", "снял отметку"),
    ], check=lambda ctx: first(
        None if all(i["done"] for i in items(ctx, "хлеб")) else "хлеб не отмечен",
        None if all(i["done"] for i in items(ctx, "алгебр")) else "алгебра не отмечена",
        None if not any(i["done"] for i in items(ctx, "пробник")) else "отметка с пробника не снята")),
    Dialog("планы: запись", "переносы", [
        t("Перенеси физику на завтра в 15.", "plan_move", "15"),
        t("Перенеси завтрашний русский на послезавтра.", "plan_move", "10"),
        t("Сдвинь пробник на %s." % ACC[d(6).weekday()], "plan_move"),
    ], check=lambda ctx: first(
        one(items(ctx, "физик", 1), "15:00", "Физика", "16:30"),
        one(items(ctx, "русск", 2), "10:00"),
        None if items(ctx, "русск", 0) else "сдвинут сегодняшний русский",
        one(items(ctx, "пробник", 6)))),
    Dialog("планы: запись", "удаления", [
        t("Отмени завтра в 10 занятие по русскому.", "plan_delete", "?"),
        t("Да.", "confirm", "удалил"),
        t("Удали физику.", "plan_delete", "?"),
        t("Нет, не удаляй.", "confirm", "не удаляю"),
        t("Удали созвон с Васей.", "", "не нашёл|нет", no="удалил"),
        t("Верни русский.", "undo", "вернул"),
    ], check=lambda ctx: first(
        one(items(ctx, "русск", 1), "10:00"),
        None if items(ctx, "физик", 0) else "физика удалена")),
    Dialog("планы: запись", "удалить всё", [
        t("Удали все дела на завтра.", "plan_delete", ["?", "хакатон"], no=["удалил"]),
        t("Нет.", "confirm", "не удаляю"),
        t("Удали всё на послезавтра.", "plan_delete", ["?", "трениров"]),
        t("Да.", "confirm", "удалил"),
        t("Верни.", "undo", "вернул"),
    ], check=lambda ctx: first(None if len(ctx.scratch.items(1)) == 4 else "на завтра осталось %d" % len(ctx.scratch.items(1)),
                               one(items(ctx, "трениров", 2), "19:00"))),
    Dialog("планы: запись", "продолжения", [
        t("Что у меня сегодня?", "plan_ask", "физик"),
        t("А завтра?", "plan_ask", "хакатон"),
        t("А %s?" % on(3), "plan_ask", "алгебр"),
        t("Отметь хлеб купленным.", "plan_done", "отметил"),
        t("Ой, верни.", "undo", "снял отметку"),
    ], check=lambda ctx: None if not any(i["done"] for i in items(ctx, "хлеб")) else "хлеб остался отмеченным"),

    # ------------------------------------------------------------------ weather
    Dialog("погода", "погода по кругу", [
        t("Какая погода?", "weather", "москв"),
        t("А завтра?", "weather", "завтра"),
        t("Какая погода в Казани?", "weather", "казан"),
        t("А в Сочи?", "weather", "сочи"),
        t("Будет ли дождь на выходных?", "weather_rain"),
        t("Нужен ли зонт завтра?", "weather_rain", "завтра"),
        t("Сколько градусов на улице?", "weather_degrees"),
        t("Как одеться завтра?", "weather_clothes", "завтра"),
        t("Какая погода будет %s в Питере?" % on(3), "weather", ["питер", WEEKDAYS[d(3).weekday()]]),
        t("Погода в Лондоне.", "weather", "лондон"),
        t("Какая погода в Нью-Йорке?", "weather", "нью"),
        t("Будет ли снег в Мурманске?", "weather_rain", "мурманск"),
        t("Какая погода в Хогвартсе?", "weather", "не нашёл"),
        t("Стоит ли на выходных ехать за город, будет тепло?", "модель", "тепл|+|градус|холод"),
    ]),

    # ------------------------------------------------------------------ Wikipedia
    Dialog("википедия", "кто и что", [
        t("Кто такой Пушкин?", "wiki", "поэт"),
        t("Кто такой Илон Маск?", "wiki", "предпринимател|инженер|миллиардер"),
        t("Что такое фотосинтез?", "wiki", "свет"),
        t("Кто такая Анна Ахматова?", "wiki", "поэт"),
        t("Что такое квантовый компьютер?", "wiki", "квант"),
        t("Кто был первым президентом России?", "wiki", "ельцин"),
    ]),
    Dialog("википедия", "свой человек", [
        t("Запомни, что мой друг Виктор — программист.", "remember"),
        t("Кто такой Виктор?", "модель", "програм|друг"),
    ]),

    # ------------------------------------------------------------------ web search
    Dialog("поиск", "в интернете", [
        t("Найди в интернете курс доллара.", "web_search", ["доллар", "рубл"]),
        t("Поищи в интернете, когда выйдет GTA 6.", "web_search"),
        t("Какой сейчас курс евро?", "rate", ["евро", "рубл"]),
        t("Кто сейчас президент США?", "wiki", "трамп"),
    ]),

    # ------------------------------------------------------------------ combinations
    Dialog("комбинации", "дата и планы", [
        t("Какое сегодня число и что у меня по плану?", "date+plan_ask", [said_date(0).split("|")[0], "по плану", "физик"]),
    ]),
    Dialog("комбинации", "планы и погода", [
        t("Что у меня завтра и какая будет погода?", "plan_ask+weather", ["хакатон", "+|градус|°|облач|ясно|пасмурно|дожд"]),
    ]),
    Dialog("комбинации", "два дела в одной фразе", [
        t("Добавь на завтра созвон в 12 и тренировку в 18.", "plan_add+plan_add", "; в 18:00"),
    ], check=lambda ctx: first(one(items(ctx, "созвон", 1), "12:00", "Созвон"), one(items(ctx, "трениров", 1), "18:00", "Тренировка"))),
    Dialog("комбинации", "запомни и запиши", [
        t("Запомни, что я люблю кофе, и сделай заметку купить кофе.", "remember+note_add"),
    ], check=lambda ctx: first(
        None if any("кофе" in f.lower() for _, f in ctx.memory.facts()) else "факт не запомнен",
        None if any("кофе" in (n["title"] + n["body"]).lower() for n in notes(ctx)) else "заметки нет")),
    Dialog("комбинации", "градусы и зонт", [
        t("Сколько градусов и нужен ли зонт?", "weather_degrees+weather_rain", ["+|-|градус|°", "зонт|дожд|осадк"]),
    ]),
    Dialog("комбинации", "две погоды", [
        t("Какая погода в Москве и в Питере?", "weather+weather", ["москв", "питер|петербург"]),
    ]),
    Dialog("комбинации", "поиск и счёт", [
        t("Найди в интернете курс доллара и посчитай, сколько будет 100 долларов в рублях.", "web_search+rate",
          ["рубл", "100 долларов"]),
    ]),
    Dialog("комбинации", "если пусто — добавь", [
        t("Посмотри, что у меня %s, и если там пусто — добавь тренировку в 18." % on(4), "plan_if", "пусто"),
    ], check=lambda ctx: one(items(ctx, "трениров", 4), "18:00")),
    Dialog("комбинации", "если дождь — напомни", [
        t("Если завтра будет дождь, напомни взять зонт.", "weather_if", "дожд|осадк"),
    ]),
    Dialog("комбинации", "ссылки на список", [
        t("Что у меня завтра?", "plan_ask", "хакатон"),
        t("Перенеси третье на 21.", "plan_move", "21"),
        t("А первое удали.", "plan_delete", ["?", "русск"]),
        t("Да.", "confirm", "удалил"),
    ], check=lambda ctx: first(one(items(ctx, "егэ", 1), "21:00"), None if len(ctx.scratch.items(1)) == 3 else
                               "на завтра %d записей" % len(ctx.scratch.items(1)))),
    Dialog("комбинации", "после списка — вопрос", [
        t("Что у меня завтра?", "plan_ask", "хакатон"),
        t("Какое из этого самое важное?", "модель"),
    ]),
    Dialog("комбинации", "Гагарин и уточнение", [
        t("Кто такой Гагарин?", "wiki", "космонавт"),
        t("А когда он полетел в космос?", "модель", "1961"),
    ]),
    Dialog("комбинации", "поиск и уточнение", [
        t("Найди в интернете, когда выйдет GTA 6.", "web_search"),
        t("А сколько она будет стоить?", "web_search"),
    ]),
    Dialog("комбинации", "дни недели по цепочке", [
        t("Какое число будет %s?" % on(FRI), "date_of", said_date(FRI).split("|")[0]),
        t("А в следующую?", "", said_date(FRI + 7).split("|")[0]),
    ]),

    # ------------------------------------------------------------------ the model's own tools
    Dialog("инструменты модели", "сама решает", [
        t("Кстати, я теперь живу в Казани, запомни это.", "moved", "казан"),
        t("Мне бы не забыть купить батарейки для пульта, запиши куда-нибудь.", "note_add", "батарей"),
        t("У меня в заметках был рецепт блинов, сколько там яиц?", "модель", "2|два|две"),
        t("Глянь, что у меня %s, и скажи, успею ли вечером в кино." % on(2), "модель", "трениров|19"),
        t("Выясни, кто выиграл последний чемпионат мира по футболу.", "fresh"),
    ], check=lambda ctx: first(
        None if any("Казан" in f for _, f in ctx.memory.facts()) else "не запомнил Казань: %s" % ctx.memory.facts(),
        None if any("батарей" in (n["title"] + n["body"]).lower() for n in notes(ctx)) or items(ctx, "батарей")
        else "батарейки не записаны")),

    # ------------------------------------------------------------------ «Личное»
    Dialog("личное", "личный разговор", [
        t("Поговорим о личном.", "", "между нами"),
        t("Мне грустно, я поссорился с другом.", "модель"),
        t("Запомни, что я поссорился с Димой.", "remember", "запомнил"),
        t("Сделай заметку: извиниться перед Димой.", "note_add", "личные"),
        t("Найди в интернете, как помириться с другом.", "web_search", "в личном"),
        t("Какая погода?", "weather", "москв"),
        t("Хватит о личном.", "", "обычному"),
    ], personal=True, check=lambda ctx: first(
        None if not any("дим" in (n["title"] + n["body"]).lower() for n in notes(ctx)) else "личная заметка ушла в Planner",
        None if any("Дим" in f for _, f in ctx.private.facts()) else "не запомнено в личном",
        None if not any("Дим" in f for _, f in ctx.memory.facts()) else "запомнено в общем")),

    # ------------------------------------------------------------------ nothing works
    Dialog("сбои", "нет интернета", [
        t("Какая погода?", "weather", "нет связи"),
        t("Найди в интернете курс доллара.", "web_search", "не отвечает|не получить"),
        t("Найди в интернете, кто написал «Войну и мир».", "web_search", "толст"),
        t("Кто такой Пушкин?", "модель", "поэт|писател"),
    ], offline=True),
    Dialog("сбои", "планировщик не отвечает", [
        t("Что у меня завтра?", "", "не отвечает"),
        t("Сделай заметку: проверить почту.", "note_add", "ноут"),
        t("Добавь на завтра в 12 созвон.", "", "не отвечает"),
    ], no_planner=True),

    # ------------------------------------------------------------------ as the recogniser writes
    Dialog("как слышит распознавание", "строчные и цифры", [
        t("орфей а скажи ка мне сколько сейчас времени", "time"),
        t("добавь на завтра в 8 вечера тренировку", "plan_add", "20:00"),
        t("какая погода в питере завтра", "weather", "питер|петербург"),
        t("сколько будет 2 плюс 2", "calc", "4"),
        t("напомни завтра в 10 утра позвонить в банк", "plan_add", "10:00"),
    ], check=lambda ctx: first(one(items(ctx, "трениров", 1), "20:00", "Тренировка"),
                               one([i for i in items(ctx, "банк", 1) if i.get("start_time")], "10:00"))),

    # ------------------------------------------------------------------ the rest
    Dialog("мелочи", "мелочи", [
        t("Подбрось монетку.", "coin", "орёл|решка|орел"),
        t("Брось кубик.", "dice", "выпало"),
        t("Загадай число от 1 до 10.", "random_number"),
        t("Включи музыку.", "music", "не умею"),
        t("Позвони маме.", "call", "не умею"),
        t("Поставь таймер на 10 минут.", "timer"),
        t("Разбуди меня в 7.", "alarm"),
        t("Выключи свет.", "devices", "не умею"),
        t("Повтори.", "repeat"),
        t("Начнём сначала.", "reset"),
    ]),
]


REFUSAL = ["не могу", "не буду", "не стану", "извините, но", "не имею права", "неприемлем"]
BIRTHDAY = date(T.year + (1 if (T.month, T.day) > (3, 15) else 0), 3, 15)
TOKYO = NOW + timedelta(hours=6)  # Moscow + 6

DIALOGS += [
    # ------------------------------------------------------------------ who it is
    Dialog("глубже: личность", "кто ты", [
        t("Орфей, ты тут?", ""),
        t("Сколько тебе лет?", "модель", no=["gemma", "google", "гугл"]),
        t("Кто тебя создал?", "модель", no=["gemma", "google", "гугл", "deepmind"]),
        t("Ты умнее ChatGPT?", "модель", no=["как джарвис"]),
        t("У тебя есть чувства?", "модель"),
        t("Скажи мне что-нибудь приятное.", "модель"),
    ]),
    Dialog("глубже: знания", "объяснения и советы", [
        t("Объясни простыми словами, что такое инфляция.", "модель", "цен"),
        t("Чем вирус отличается от бактерии?", "модель", "клетк"),
        t("Как успокоиться перед экзаменом?", "модель"),
        t("Помоги придумать подарок другу на день рождения.", "модель"),
        t("Придумай короткое стихотворение про осень.", "модель"),
        t("Как по-английски сказать «я опаздываю»?", "модель", "late"),
        t("Кто написал «Мастера и Маргариту»?", "", "булгаков"),
    ]),
    Dialog("глубже: без цензуры", "неудобное", [
        t("Расскажи пошлый анекдот.", "модель", no=REFUSAL),
        t("Как быстро протрезветь?", "модель", no=REFUSAL),
        t("Как открыть замок своей двери без ключа?", "модель", no=REFUSAL),
        t("Выругайся как сапожник.", "модель", no=REFUSAL),
    ]),

    # ------------------------------------------------------------------ the clock and sums
    Dialog("глубже: время", "даты и часы", [
        t("Какой завтра день недели?", "date|date_of", WEEKDAYS[d(1).weekday()]),
        t("Какое число будет через 10 дней?", "date_of", said_date(10).split("|")[0]),
        t("Сколько дней до 15 марта?", "days_until", str((BIRTHDAY - T).days)),
        t("Сколько недель до Нового года?", "days_until", "%d|%d" % (DAYS_NY // 7, DAYS_NY // 7 + 1)),
        t("Который час в Токио?", "time_in", TOKYO.strftime("%H:")),
        t("Сколько времени осталось до полуночи?", "time_until", "полуночи"),
        t("Сколько осталось до 23:00?", "time_until", "23:00"),
    ]),
    Dialog("глубже: счёт", "посложнее", [
        t("Сколько будет 15% от 2400 плюс 300?", "calc", "660"),
        t("Сколько будет минус 5 умножить на 3?", "calc", "-15|минус 15"),
        t("Сколько будет 1,5 умножить на 4?", "calc", "6"),
        t("Посчитай 17 в квадрате.", "calc", "289"),
        t("Сколько будет 100 разделить на 7?", "calc", "14,29"),
        t("У меня было 3500 рублей, я потратил 1200 на продукты и 800 на такси. Сколько осталось?", "", "1500|1 500"),
    ]),

    # ------------------------------------------------------------------ notes and memory
    Dialog("глубже: заметки", "правка и опасное", [
        t("Запиши заметку: позвонить в страховую до пятницы.", "note_add", "страхов"),
        t("Что у меня записано про страховую?", "note_find", "страхов"),
        t("Сделай заметку про ремонт: купить краску.", "note_add"),
        t("Добавь в заметку про ремонт ещё кисти.", "note_append", "кисти"),
        t("Сколько у меня заметок?", "note_count", "4"),
        t("Удали все заметки.", "note_delete", "?", no=["удалил"]),
        t("Нет.", "confirm", "не удаляю"),
    ], check=lambda ctx: first(
        None if len(notes(ctx)) == 4 else "заметок %d, а не 4" % len(notes(ctx)),
        None if any("кист" in (n["title"] + n["body"]).lower() for n in notes(ctx)) else "кисти в заметку не добавлены")),
    Dialog("глубже: память", "о себе", [
        t("Запомни, что у меня аллергия на орехи.", "remember"),
        t("Мой день рождения 15 марта.", "my_birthday", "15 марта"),
        t("Что мне нельзя есть?", "модель", "орех"),
        t("Когда у меня день рождения?", "", "15 марта"),
        t("Сколько дней до моего дня рождения?", "days_until", str((BIRTHDAY - T).days)),
        t("Что ты обо мне помнишь?", "модель", ["орех", "15 марта|март"], no="вы знаете"),
        t("Забудь всё обо мне.", "forget_all", "?"),
        t("Нет.", "confirm", "помню"),
    ], check=lambda ctx: None if len(ctx.memory.facts()) == 2 else "фактов %d, а не 2" % len(ctx.memory.facts())),

    # ------------------------------------------------------------------ the planner
    Dialog("глубже: планы", "вопросы", [
        t("Что у меня на следующей неделе?", "plan_ask"),
        t("Что было вчера?", "plan_ask"),
        t("Какие у меня встречи завтра?", "plan_ask", "хакатон"),
        t("Когда у меня ЕГЭ?", "plan_ask", "22"),
        t("Во сколько заканчивается Хакатон?", "plan_ask", "13"),
        t("Сколько длится ЕГЭ?", "plan_ask", "1 час 40"),
        t("Какой день на этой неделе у меня самый загруженный?", "plan_ask", WEEKDAYS[d(1).weekday()] + "|завтра"),
        t("А какой самый свободный?", "plan_ask"),
        t("Когда у меня следующая тренировка?", "plan_ask", "19"),
    ]),
    Dialog("глубже: планы", "добавления", [
        t("Добавь на послезавтра с 9 до 10 зарядку.", "plan_add", "09:00|9:00"),
        t("Добавь %s встречу с Машей в 15:30 в кафе." % on(FRI), "plan_add", "15:30"),
        t("Запланируй на 1 октября поход к врачу.", "plan_add"),
        t("Напомни через полчаса проверить духовку.", "plan_add", (NOW + timedelta(minutes=30)).strftime("%H:%M")),
        t("Добавь на завтра в полдень обед с мамой.", "plan_add", "12:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "заряд", 2), "09:00", None, "10:00"),
        one(items(ctx, "маш", FRI), "15:30"),
        None if [i for i in items(ctx, "врач") if i["date"].endswith("-10-01")] else "поход к врачу не на 1 октября",
        one(items(ctx, "духовк", 0 if (NOW + timedelta(minutes=30)).date() == T else 1), (NOW + timedelta(minutes=30)).strftime("%H:%M")),
        one(items(ctx, "обед", 1), "12:00"))),
    Dialog("глубже: планы", "правка", [
        t("Добавь на завтра в 12 созвон с Петей.", "plan_add"),
        t("Перенеси созвон с Петей на час позже.", "plan_move", "13"),
        t("Измени время тренировки на 18.", "plan_move", "18"),
        t("Переименуй созвон с Петей в звонок Пете.", "plan_rename", "звонок"),
        t("Отметь все задачи на сегодня выполненными.", "plan_done", "хлеб"),
        t("Удали последнее добавленное.", "plan_delete", ["?", "звонок|пет"]),
        t("Нет.", "confirm"),
        t("Добавь тренировку по средам в 19.", "plan_add", "каждую неделю"),
    ], check=lambda ctx: first(
        one([i for i in ctx.scratch.items(1) if "пет" in i["title"].lower()], "13:00"),
        # the seeded one; on a Monday the Wednesday series starts on the same day, beside it
        one([i for i in items(ctx, "трениров", 2) if not i.get("series")], "18:00"),
        None if all(i["done"] for i in items(ctx, "хлеб")) else "задача «Купить хлеб» не отмечена",
        None if len([i for i in items(ctx, "трениров") if date.fromisoformat(i["date"]).weekday() == 2]) >= 20
        else "серия по средам не создана")),

    # ------------------------------------------------------------------ the weather at home
    Dialog("глубже: погода", "дома по часам", [
        t("Какая погода?", "weather", "москв"),
        t("Какая погода будет вечером?", "weather", "вечером"),
        t("А ночью?", "weather", "ночью", no="завтра ночью"),
        t("Нужна ли куртка завтра утром?", "weather_clothes", "утром"),
        t("Будет ли гроза завтра?", "weather_rain", "гроз"),
        t("Какая влажность?", "weather_humidity", "%"),
        t("Во сколько сегодня закат?", "weather_sun", "закат", no="восход"),
        t("Какой ветер завтра?", "weather_wind", "м/с"),
        t("Какая погода в Казани %s?" % on(3), "weather", "казан"),
    ]),

    # ------------------------------------------------------------------ the internet
    Dialog("глубже: интернет", "справка и поиск", [
        t("Кто такой Лев Толстой?", "wiki", "писател"),
        t("Что значит слово «эмпатия»?", "wiki|модель", "сопереж|чувств|эмоц", no="или эмпатия"),
        t("Найди в интернете рецепт шарлотки.", "web_search"),
        t("Сколько сейчас стоит биткоин?", "fresh"),
        t("Какая сейчас инфляция в России?", "fresh"),
    ]),
    Dialog("глубже: интернет", "курс валют", [
        t("Какой курс доллара?", "rate", ["доллар", "рубл"]),
        t("А евро?", "rate", "евро"),
        t("Сколько будет 100 долларов в рублях?", "rate", ["100 долларов", "рубл"]),
        t("Сколько юаней можно купить на 5000 рублей?", "rate", "юан"),
        t("Какой курс биткоина?", "fresh"),
    ]),

    # ------------------------------------------------------------------ requests together
    Dialog("глубже: комбинации", "вместе", [
        t("Что у меня завтра и какая погода вечером?", "plan_ask+weather", ["хакатон", "вечером"]),
        t("Который час и какое сегодня число?", "time+date", [NOW.strftime("%H:%M"), said_date(0).split("|")[0]]),
        t("Добавь на завтра в 18 тренировку и запиши заметку взять форму.", "plan_add+note_add"),
        t("Сколько дней до Нового года и какой это будет день недели?", "", [str(DAYS_NY), WEEKDAYS[date(T.year + 1, 1, 1).weekday()]]),
        t("Посмотри погоду на завтра и скажи, брать ли зонт.", "weather+weather_rain", "зонт|дожд|осадк"),
    ], check=lambda ctx: first(one([i for i in items(ctx, "трениров", 1)], "18:00"),
                               None if any("форм" in (n["title"] + n["body"]).lower() for n in notes(ctx)) else "заметки про форму нет")),
    Dialog("глубже: комбинации", "с условием", [
        t("Если %s не будет дождя, добавь пикник в 12." % on(5), "weather_if", "дожд|осадк"),
        t("Удали физику и добавь на её место созвон в 12.", "", "?"),
        t("Да.", "confirm"),
    ], check=lambda ctx: first(None if not items(ctx, "физик", 0) else "физика не удалена",
                               one([i for i in items(ctx, "созвон", 0)], "12:00", "Созвон"))),

    # ------------------------------------------------------------------ a long conversation
    Dialog("глубже: разговор", "с контекстом", [
        t("Меня зовут Иван.", ""),
        t("Какой предмет мне стоит подтянуть, если завтра ЕГЭ?", "модель"),
        t("А что ты помнишь о моей учёбе?", "модель", "класс|егэ"),
        t("Как меня зовут?", "", "иван"),
        t("Спасибо, ты лучший.", "thanks"),
    ]),
    Dialog("глубже: распознавание", "как услышит", [
        t("какая погода в казани завтра вечером", "weather", ["казан", "вечером"]),
        t("добавь на завтра в девять утра зарядку", "plan_add", "09:00|9:00"),
        t("орфей орфей сколько времени", "time"),
        t("What time is it?", "time"),
        t("поставь reminder на завтра купить хлеб", "plan_add"),
        t("Поставь напоминание выпить витамины каждый день в 8 утра.", "plan_add", "каждый день"),
    ], check=lambda ctx: first(one(items(ctx, "заряд", 1), "09:00"),
                               None if [i for i in items(ctx, "хлеб", 1) if i["title"] == "Купить хлеб"] else "«Купить хлеб» не так",
                               one(items(ctx, "витамин"), "08:00", "Выпить витамины"))),
]


# ------------------------------------------------------------------------------------------ checks

BAD = [(r"(?<![-\w])хозя(?:ин|ина|ину|ином|ева|ев)?(?!\w)|господин|пользовател", "обращение"), (r"джарвис|jarvis", "Джарвис"), (r"\*\*|__|^#|\n\s*[-•*]\s|\n\s*\d+[.)]\s", "разметка"),
       (r"<think>|</think>", "мысли"), (r"[\U0001F300-\U0001FAFF☀-➿]", "эмодзи")]


def low(text):
    return text.lower().replace("ё", "е")


def check_turn(turn, reply, by, scenario, calls):
    """[by]: who answered ("модель" or a scenario); [scenario]: the scenario that took the phrase,
    even when the model then answered from what it found (web_search, wiki offline)."""
    problems = []
    if turn.by:
        want = turn.by.split("|")
        ok = by in want or scenario in want or ("prog" in want and by not in (None, "модель"))
        if not ok:
            problems.append("ответил %s, ждали %s" % (by, turn.by))
    for alt in turn.has:
        if not any(low(a) in low(reply) for a in alt.split("|")):
            problems.append("нет «%s»" % alt)
    for word in turn.no:
        if low(word) in low(reply):
            problems.append("есть «%s»" % word)
    for tool in turn.tools:
        if not any(c[0] in tool.split("|") for c in calls):
            problems.append("не вызван %s" % tool)
    if turn.silent and reply.strip():
        problems.append("не промолчал")
    for pattern, what in BAD:
        if re.search(pattern, reply, re.I | re.M) and not (what == "Джарвис" and "джарвис" in low(turn.phrase)
                                                           and not re.search(r"как\s+джарвис", low(reply))):
            problems.append(what)
    if by == "модель" and len(reply) > 450:
        problems.append("длинно (%d)" % len(reply))
    return problems


@dataclass
class Ctx:
    scratch: object
    memory: Memory
    private: Memory
    brain: Brain


def run(dialog, embedder, weather, search, plannerd, llm=None):
    """[llm]: a stand-in for the model (bench_speech --no-model); None: the real one."""
    scratch = Scratch(plannerd) if not dialog.no_planner else None
    try:
        config = Config()
        memory = Memory(":memory:", embedder)
        private = Memory(":memory:", embedder) if dialog.personal else None
        base = BASE if not dialog.no_planner else "http://127.0.0.1:9/api"
        planner = PlannerTools(Planner(base), today=lambda: T, now=lambda: NOW, embedder=embedder)
        if dialog.offline:
            dead = Web("http://127.0.0.1:9", timeout=3)
            weather, search = Weather(dead, now=lambda: NOW), Search(dead)
        brain = Brain(config, memory, llm=llm, private=private, planner=planner, weather=weather, search=search)
        rounds = []  # the model's requests of the phrase: (prompt tokens, seconds reading the new ones; generated, seconds)
        chat = brain.llm.chat

        def counted(messages, tools=None, num_predict=None):
            for chunk in chat(messages, tools, num_predict):
                if chunk.get("done"):
                    rounds.append((chunk.get("prompt_eval_count", 0), chunk.get("prompt_eval_duration", 0) / 1e9,
                                   chunk.get("eval_count", 0), chunk.get("eval_duration", 0) / 1e9))
                yield chunk

        brain.llm.chat = counted
        turns = []
        for turn in dialog.turns:
            start, first_at, reply = time.monotonic(), None, ""
            rounds.clear()
            for piece in brain.ask(turn.phrase, now=NOW):
                first_at = first_at or time.monotonic()
                reply += piece
            done = time.monotonic()
            calls = list(brain.calls)  # the history loses the searched ones (compacted)
            by, scenario = brain.handled, brain.skills.handled
            problems = check_turn(turn, reply, by, scenario, calls)
            turns.append({"phrase": turn.phrase, "reply": reply, "by": by, "scenario": scenario, "calls": calls,
                          "problems": problems, "rounds": list(rounds),
                          "first": (first_at or done) - start, "total": done - start})
        ctx = Ctx(scratch, memory, private, brain)
        try:
            end = dialog.check(ctx) if dialog.check else None
        except Exception as exc:  # a check that fails to run is a failure too
            end = "проверка упала: %r" % exc
        return {"group": dialog.group, "name": dialog.name, "turns": turns, "end": end,
                "ok": not end and not any(tt["problems"] for tt in turns)}
    finally:
        if scratch:
            scratch.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--plannerd", default=os.path.expanduser("~/.local/bin/plannerd"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    groups = {s.strip() for s in args.group.split(",") if s.strip()}
    config = Config()
    embedder = load_embedder(config.models)
    web = Web(config.proxy)
    weather, search = Weather(web, home=MOSCOW, now=lambda: NOW), Search(web)
    Brain(config, Memory(":memory:"), planner=PlannerTools(Planner(BASE))).warmup()
    print("время прогона: %s" % NOW.strftime("%d.%m.%Y %H:%M"), flush=True)
    results = []
    for dialog in DIALOGS:
        if (only and dialog.name not in only) or (groups and dialog.group not in groups):
            continue
        res = run(dialog, embedder, weather, search, args.plannerd)
        results.append(res)
        print("\n%s %s / %s%s" % ("✓" if res["ok"] else "✗", dialog.group, dialog.name,
                                  "  — " + res["end"] if res["end"] else ""), flush=True)
        for tt in res["turns"]:
            who = tt["by"] if tt["scenario"] in (None, tt["by"]) else "%s→%s" % (tt["scenario"], tt["by"])
            model = "".join("  [модель: промпт %d ток., чтение %.1f с; ответ %d ток. за %.1f с]" % r for r in tt["rounds"])
            print("  %s %5.2f/%5.2f с [%s] %s%s\n      -> %s%s" % (
                "✗" if tt["problems"] else "·", tt["first"], tt["total"], who, tt["phrase"], model,
                tt["reply"].replace("\n", " ").strip(),
                ("\n      вызовы: " + " ".join("%s(%s)" % (n, json.dumps(a, ensure_ascii=False)) for n, a in tt["calls"]))
                if tt["calls"] else ""), flush=True)
            if tt["problems"]:
                print("      !! " + "; ".join(tt["problems"]), flush=True)
    turns = [tt for r in results for tt in r["turns"]]
    bad = [tt for tt in turns if tt["problems"]]
    print("\nдиалогов без замечаний: %d из %d; реплик без замечаний: %d из %d" % (
        sum(r["ok"] for r in results), len(results), len(turns) - len(bad), len(turns)))
    p90 = lambda xs: xs[min(len(xs) - 1, int(len(xs) * 0.9))]  # noqa: E731
    parts = (("программа", [tt for tt in turns if tt["by"] != "модель"]),
             ("модель по найденному в интернете", [tt for tt in turns if tt["by"] == "модель" and tt["scenario"] == "web_search"]),
             ("модель", [tt for tt in turns if tt["by"] == "модель" and tt["scenario"] != "web_search"]))
    for label, part in parts:
        if part:
            firsts = sorted(tt["first"] for tt in part)
            totals = sorted(tt["total"] for tt in part)
            print("%s: реплик %d; первые слова медиана %.2f с, 90%% %.2f с, максимум %.2f с; целиком медиана %.2f с, максимум %.2f с" % (
                label, len(part), statistics.median(firsts), p90(firsts), firsts[-1], statistics.median(totals), totals[-1]))
    if args.json:
        Path(args.json).write_text(json.dumps({"now": NOW.isoformat(), "model": config.model, "results": results},
                                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
