"""Orpheus against speech as it is really said: the same requests in many wordings — the recogniser's
forms (no commas, the wake word's remains, digits), fillers («слушай», «ну», «а»), questions instead of
commands («можешь добавить…?»), the verb at the end («…, добавь»), follow-ups, yes and no said a dozen
ways — and phrases that only look like commands («напомни, как зовут…», «хватит ли мне денег…»).
What it checks: who took each phrase (a scenario, or the model), what the reply says, and what the
Planner holds afterwards (a throwaway plannerd with a seeded week, as bench_dialogs).

    # routing only, no model, no internet (fast; on any machine with the Planner's checkout):
    python scripts/bench_speech.py --no-model --plannerd ../Planner/server/plannerd.py
    # the whole thing, the real model and the internet through the proxy (on the laptop):
    ORPHEUS_PROXY=http://127.0.0.1:2080 .venv/bin/python scripts/bench_speech.py

With --no-model a phrase that reaches the model gets "(модель)" and nothing else happens; a phrase
expected to be the model's passes on who took it only.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_dialogs as bd  # noqa: E402
from bench_dialogs import NOW, Dialog, first, items, on, one, t  # noqa: E402

from orpheus.brain import Brain  # noqa: E402
from orpheus.config import Config  # noqa: E402
from orpheus.search import Search  # noqa: E402
from orpheus.server import strip_wake_word  # noqa: E402
from orpheus.weather import MOSCOW, Weather  # noqa: E402
from orpheus.web import Web  # noqa: E402

# the seeded week (bench_dialogs.SEED): today Физика 12:00-13:30, Занятие по русскому языку 18:00, task Купить хлеб;
# +1 Занятие по русскому языку 10:00, Хакатон 10:00-13:00, ЕГЭ 22:00-23:40, task Позвонить в банк; +2 Тренировка
# 19:00-20:30; +3 task Кр по алгебре; +5 task Пробник ЕГЭ по физике; notes Wi-Fi (sunflower42), Рецепт блинов.
PLAN_WRITE = "plan_add"
ANY_PLANS = "plan_ask|plan_left|plan_next|plan_free"
MODEL = "модель"


def gone(ctx, word, n=None):
    return None if not items(ctx, word, n) else "не удалено: %s" % word


def kept(ctx, word, n=None):
    return None if items(ctx, word, n) else "удалено то, что просили оставить: %s" % word


def count(ctx, word, want):
    found = items(ctx, word)
    return None if len(found) == want else "«%s»: записей %d, а не %d" % (word, len(found), want)


def _as_seeded(it):
    return (it["title"], it.get("date"), it.get("start_time"), it.get("end_time"), bool(it.get("done")))


def untouched(ctx):
    """Nothing added, removed, moved, renamed or marked in the planner: its items are the seeded ones."""
    seeded = sorted(_as_seeded(dict(i, done=False)) for i in bd.SEED if i["kind"] != "note")
    now = sorted(_as_seeded(i) for i in ctx.scratch.items())
    if now == seeded:
        return None
    extra = [i[0] for i in now if i not in seeded]
    lost = [i[0] for i in seeded if i not in now]
    return "планы изменены: %s" % "; ".join(filter(None, ["новое/изменённое " + ", ".join(extra) if extra else "",
                                                           "пропало " + ", ".join(lost) if lost else ""]))


def added_none(ctx, *words):
    """None of these words in a title: nothing like them was added."""
    found = [i["title"] for i in ctx.scratch.items() if any(w in i["title"].lower() for w in words)]
    return "добавлено лишнее: %s" % ", ".join(found) if found else None


def ACC_ON(n):  # "в пятницу" -> "пятницу" after "на": "на пятницу"
    return on(n).split(" ", 1)[1]


DIALOGS = [
    # ------------------------------------------------------------------ the clock and the calendar
    Dialog("время", "как спрашивают время", [
        t("Сколько время?", "time"),
        t("Слушай, а сколько щас времени?", "time"),
        t("Орфей, время подскажи.", "time"),
        t("Скажи который час", "time"),
        t("Который час в Токио?", "time_in"),
    ]),
    Dialog("время", "какой день", [
        t("Какое сегодня число?", "date"),
        t("Сегодня какой день недели?", "date"),
        t("Напомни, какое сегодня число?", "date"),
        t("Напомни какое сегодня число", "date"),
        t("Какое число будет в следующую пятницу?", "date_of"),
        t("Сколько дней до нового года?", "days_until"),
        t("А сколько до конца дня осталось?", "time_until"),
    ], check=untouched),
    Dialog("время", "счёт", [
        t("Сколько будет 17 умножить на 23?", "calc", "391"),
        t("Посчитай 250 плюс 380.", "calc", "630"),
        t("17 на 23 сколько будет?", "calc", "391"),
        t("Сколько будет 15 процентов от 2000?", "calc", "300"),
        t("Сколько будет двести делить на восемь?", "calc", "25"),
    ]),

    # ------------------------------------------------------------------ the weather
    Dialog("погода", "как спрашивают погоду", [
        t("Что по погоде?", "weather"),
        t("Какая погода на улице?", "weather"),
        t("Посмотри погоду на завтра.", "weather"),
        t("Мне сегодня куртку надевать?", "weather_clothes"),
        t("Дождь сегодня будет?", "weather_rain"),
        t("Зонт брать?", "weather_rain"),
        t("Сколько сейчас градусов?", "weather_degrees"),
        t("Ветер сильный сегодня?", "weather_wind"),
        t("Какая погода в выходные?", "weather"),
        t("А в Питере?", "weather"),
        t("Погода в Сочи на завтра.", "weather"),
    ]),

    # ------------------------------------------------------------------ the planner: reading
    Dialog("планы: чтение", "как спрашивают про день", [
        t("Что у меня сегодня?", "plan_ask", "физик"),
        t("Че у меня на завтра?", "plan_ask", "хакатон"),
        t("Какие планы на завтра?", "plan_ask", "хакатон"),
        t("Слушай, а что у меня послезавтра?", "plan_ask", "трениров"),
        t("Есть что-нибудь на послезавтра?", "plan_ask", "трениров"),
        t("Что у меня %s?" % on(5), "plan_ask", "пробник"),
        t("А %s что?" % on(3), "plan_ask", "алгебр"),
        t("Что у меня на этой неделе?", "plan_ask", ["хакатон", "трениров"]),
    ], check=untouched),
    Dialog("планы: чтение", "что осталось и что дальше", [
        t("Что у меня на сегодня осталось?", "plan_left"),
        t("Что осталось на сегодня?", "plan_left"),
        t("Что у меня дальше сегодня?", "plan_left|plan_next"),
        t("Какое у меня следующее дело?", "plan_next"),
        t("А что у меня ещё завтра?", "plan_ask", "хакатон"),
    ], check=untouched),
    Dialog("планы: чтение", "когда и во сколько", [
        t("Когда у меня тренировка?", "plan_ask", "19"),
        t("Во сколько завтра Хакатон?", "plan_ask", "10"),
        t("А ЕГЭ во сколько?", "plan_ask", "22"),
        t("Когда пробник по физике?", "plan_ask", "пробник"),
        t("Я завтра свободен?", "plan_free"),
        t("Я свободен послезавтра вечером?", "plan_free"),
        t("Какой день на этой неделе самый загруженный?", "plan_ask"),
    ], check=untouched),

    # ------------------------------------------------------------------ the planner: adding
    Dialog("планы: запись", "обычные просьбы", [
        t("Добавь на завтра в 15:00 встречу с Петей.", PLAN_WRITE, "15:00"),
        t("Запланируй мне %s в 7 вечера кино." % on(4), PLAN_WRITE, "19:00"),
        t("Напомни мне завтра в 8 утра позвонить маме.", PLAN_WRITE, "08:00"),
        t("Напомни купить молоко.", PLAN_WRITE, "молоко"),
        t("Добавь задачу купить подарок Маше.", PLAN_WRITE, "подарок"),
    ], check=lambda ctx: first(
        one(items(ctx, "пет", 1), "15:00"), one(items(ctx, "кино", 4), "19:00"), one(items(ctx, "мам", 1), "08:00"),
        one(items(ctx, "молок", 0)), one(items(ctx, "подарок")))),
    Dialog("планы: запись", "вопросом и глаголом в конце", [
        t("Можешь добавить встречу с Олегом завтра в 12?", PLAN_WRITE, "12:00"),
        t("Добавишь в календарь %s созвон в 14:30?" % on(3), PLAN_WRITE, "14:30"),
        t("Мне надо послезавтра в 9 к врачу, добавь.", PLAN_WRITE, "09:00"),
        t("Не мог бы ты поставить на %s в 11 уборку?" % ACC_ON(5), PLAN_WRITE, "11:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "олег", 1), "12:00"), one(items(ctx, "созвон", 3), "14:30"), one(items(ctx, "врач", 2), "09:00"),
        one(items(ctx, "уборк", 5), "11:00"))),
    Dialog("планы: запись", "просто сказано, что будет", [
        t("Завтра в 11 у меня собеседование.", PLAN_WRITE, "11:00"),
        t("У меня %s в 5 вечера репетитор." % on(3), PLAN_WRITE, "17:00"),
        t("%s в 8 вечера иду на концерт." % on(4).capitalize(), PLAN_WRITE, "20:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "собеседован", 1), "11:00"), one(items(ctx, "репетитор", 3), "17:00"), one(items(ctx, "концерт", 4), "20:00"))),
    Dialog("планы: запись", "время словами и относительно", [
        t("Запиши на послезавтра в полседьмого вечера пробежку.", PLAN_WRITE, "18:30"),
        t("Добавь на завтра в 3 встречу с куратором.", PLAN_WRITE, "15:00"),
        t("Добавь через 2 часа проверить духовку.", PLAN_WRITE),
        t("Добавь встречу с Олегом 5 октября в 14:00.", PLAN_WRITE, "14:00"),
        t("Поставь на завтра с 10 до 12 созвон с командой.", PLAN_WRITE, "10:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "пробежк", 2), "18:30"), one(items(ctx, "куратор", 1), "15:00"), one(items(ctx, "духовк")),
        one([i for i in items(ctx, "олег") if i["date"].endswith("-10-05")], "14:00"),
        one(items(ctx, "команд", 1), "10:00", end="12:00"))),
    Dialog("планы: запись", "с названием дела", [
        t("Запиши меня к парикмахеру на %s на 12." % ACC_ON(5), PLAN_WRITE, "12:00"),
        t("Добавь на завтра в 16:40 занятие по физике с Петровым.", PLAN_WRITE, "16:40"),
        t("Поставь на %s в 10 утра стоматолога." % ACC_ON(4), PLAN_WRITE, "10:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "парикмахер", 5), "12:00", "Парикмахер"), one(items(ctx, "петров", 1), "16:40"),
        one(items(ctx, "стоматолог", 4), "10:00", "Стоматолог"))),
    Dialog("планы: запись", "поправка сразу после", [
        t("Добавь на завтра в 7 пробежку.", PLAN_WRITE, "07:00"),
        t("Нет, лучше в 8.", "plan_fix", "08:00"),
        t("Добавь на послезавтра в 14 встречу с юристом.", PLAN_WRITE, "14:00"),
        t("Ой, не туда, верни.", "undo"),
    ], check=lambda ctx: first(one(items(ctx, "пробежк", 1), "08:00"), None if not items(ctx, "юрист") else "встреча с юристом осталась")),

    # ------------------------------------------------------------------ the planner: changing
    Dialog("планы: правка", "перенести по-разному", [
        t("Перенеси физику на 14.", "plan_move", "14:00"),
        t("Сдвинь тренировку на час позже.", "plan_move", "20:00"),
        t("Хакатон перенеси на послезавтра.", "plan_move"),
        t("Поменяй время у ЕГЭ на 21:00.", "plan_move", "21:00"),
    ], check=lambda ctx: first(
        one(items(ctx, "физик", 0), "14:00"), one(items(ctx, "трениров", 2), "20:00"), one(items(ctx, "хакатон", 2), "10:00"),
        one(items(ctx, "егэ", 1), "21:00"))),
    Dialog("планы: правка", "перенести, сказав иначе", [
        t("Давай тренировку на 8 вечера поставим.", "plan_move", "20:00"),
        t("Физику на завтра перекинь.", "plan_move"),
    ], check=lambda ctx: first(one(items(ctx, "трениров"), "20:00"), one(items(ctx, "физик", 1)))),
    Dialog("планы: правка", "переименовать", [
        t("Переименуй Хакатон в собеседование в Хакатоне.", "plan_rename", "собеседование"),
        t("Поменяй название тренировки на бассейн.", "plan_rename", "бассейн"),
    ], check=lambda ctx: first(one(items(ctx, "собеседование в хакатон", 1)), one(items(ctx, "бассейн", 2)))),
    Dialog("планы: удаление", "да разными словами", [
        t("Удали ЕГЭ.", "plan_delete", "?"),
        t("Ага.", "confirm", "удалил"),
        t("Убери тренировку.", "plan_delete", "?"),
        t("Угу, давай.", "confirm", "удалил"),
        t("Отмени завтрашний русский.", "plan_delete", "?"),
        t("Да, удаляй.", "confirm", "удалил"),
    ], check=lambda ctx: first(gone(ctx, "егэ", 1), gone(ctx, "трениров"), gone(ctx, "русск", 1), kept(ctx, "русск", 0))),
    Dialog("планы: удаление", "нет разными словами", [
        t("Удали физику.", "plan_delete", "?"),
        t("Не, оставь.", "confirm", no="удалил"),
        t("Убери Хакатон.", "plan_delete", "?"),
        t("Нет, не надо.", "confirm", no="удалил"),
        t("Сотри тренировку.", "plan_delete", "?"),
        t("Отмена.", "confirm", no="удалил"),
    ], check=lambda ctx: first(kept(ctx, "физик"), kept(ctx, "хакатон"), kept(ctx, "трениров"))),
    Dialog("планы: отметки", "сделал", [
        t("Я купил хлеб.", "plan_did|plan_done"),
        t("Отметь, что я позвонил в банк.", "plan_done|plan_did"),
        t("Кр по алгебре написал, отметь.", "plan_done|plan_did"),
    ], check=lambda ctx: first(
        None if all(i["done"] for i in items(ctx, "хлеб")) else "хлеб не отмечен",
        None if all(i["done"] for i in items(ctx, "банк")) else "банк не отмечен",
        None if all(i["done"] for i in items(ctx, "алгебр")) else "алгебра не отмечена")),
    Dialog("планы: повторы", "повтор разными словами", [
        t("Сделай тренировку каждую неделю.", "plan_repeat", "каждую неделю"),
        t("Пусть повторяется 8 недель.", "plan_repeat", "8"),
        t("Больше не повторяй тренировку.", "plan_repeat_off", "убрал"),
    ], check=lambda ctx: count(ctx, "трениров", 1)),
    Dialog("планы: повторы", "по дням с разных слов", [
        t("Поставь зарядку по будням в 7 утра на 2 недели.", PLAN_WRITE, "будням"),
        t("Добавь бассейн по вторникам и четвергам в 20.", PLAN_WRITE, "вторникам"),
    ], check=lambda ctx: first(count(ctx, "зарядк", 10), None if len(items(ctx, "бассейн")) >= 20 else "бассейн: мало повторов")),

    # ------------------------------------------------------------------ not the planner, though it sounds like it
    Dialog("ловушки", "напомни, но не про планы", [
        t("Напомни, как зовут актёра из Интерстеллара?", MODEL),
        t("Напомни мне, что я говорил про Виктора.", MODEL + "|about_me|note_find"),
        t("Напомни, сколько стоит айфон?", "fresh|web_search|" + MODEL),
    ], check=untouched),
    Dialog("ловушки", "поставь, но не в календарь", [
        t("Поставь будильник на 7 утра.", "alarm"),
        t("Поставь таймер на 10 минут.", "timer"),
        t("Поставь музыку.", "music"),
        t("Засеки 5 минут.", "timer"),
    ], check=untouched),
    Dialog("ловушки", "добавь, но в заметки", [
        t("Добавь в заметки купить батарейки.", "note_add"),
        t("Запиши заметку: код от домофона 4521.", "note_add"),
        t("Запиши, что мой размер обуви 43.", "note_add|remember"),
    ], check=untouched),
    Dialog("ловушки", "перенеси и удали без планов", [
        t("Давай перенесём этот разговор на потом.", MODEL + "|bye|ack"),
        t("Удали это из головы, забудь.", "forget|" + MODEL),
        t("Отмени, я передумал.", "undo|" + MODEL + "|ack"),
    ], check=untouched),
    Dialog("ловушки", "стоп и хватит внутри фразы", [
        t("Хватит ли мне денег на айфон, если у меня 90 тысяч?", MODEL + "|fresh|web_search"),
        t("Что такое стоп-кран?", "wiki|" + MODEL),
    ]),
    Dialog("ловушки", "не к Орфею", [
        t("Да я не тебе.", MODEL + "|ack"),
        t("Мам, я поел уже.", MODEL + "|ack"),
    ], check=untouched),

    # ------------------------------------------------------------------ notes and memory
    Dialog("заметки", "найти и прочитать", [
        t("Какой пароль от вайфая?", "note_find|" + MODEL, "sunflower"),
        t("Прочитай рецепт блинов.", "note_find", "мука"),
        t("Сколько у меня заметок?", "note_count"),
        t("Что у меня в заметках?", "note_list"),
    ], check=untouched),
    Dialog("память", "запомнить и спросить", [
        t("Запомни, что мой любимый цвет синий.", "remember", "запомнил"),
        t("Какой у меня любимый цвет?", MODEL + "|about_me", "син"),
        t("Что ты обо мне знаешь?", "about_me", "син"),
        t("Забудь про любимый цвет.", "forget"),
    ]),

    # ------------------------------------------------------------------ the internet
    Dialog("интернет", "новости и свежее", [
        t("Что нового в мире?", "fresh|web_search"),
        t("Какие новости в спорте?", "fresh|web_search"),
        t("Когда прилетит Starship?", "fresh|web_search"),
        t("Кто выиграл последний матч Спартака?", "fresh|web_search"),
    ]),
    Dialog("интернет", "справка и курсы", [
        t("Что такое квантовая запутанность?", "wiki"),
        t("Кто такой Илон Маск?", "wiki"),
        t("Почём доллар?", "rate"),
        t("Курс юаня.", "rate"),
        t("Сколько будет 100 долларов в рублях?", "rate"),
        t("Сколько стоит биткоин?", "fresh|rate|web_search"),
    ]),

    # ------------------------------------------------------------------ stop, pause, «Личное»
    Dialog("стоп", "как останавливают", [
        t("Стоп.", "stop", silent=True),
        t("Хватит.", "stop", silent=True),
        t("Stop.", "stop", silent=True),
        t("Всё, хватит, спасибо.", "stop|thanks|bye"),
    ]),
    Dialog("стоп", "пауза", [
        t("Не слушай пока.", "pause"),
    ]),
    Dialog("личное", "только по команде", [
        t("Давай лично встретимся в пятницу, что скажешь?", MODEL),
        t("Это личное дело каждого.", MODEL + "|ack"),
        t("Давай о личном.", "", "между нами"),
        t("Хватит о личном.", "", "обычному"),
    ], personal=True),

    # ------------------------------------------------------------------ talk
    Dialog("болтовня", "вежливость и прочее", [
        t("Привет.", "greet"),
        t("Как дела?", "how_are_you"),
        t("Спасибо большое.", "thanks"),
        t("Ты кто вообще?", "who"),
        t("Что ты умеешь?", "capabilities"),
        t("Расскажи анекдот.", MODEL),
        t("Пока.", "bye"),
    ], check=untouched),
    Dialog("болтовня", "не умею", [
        t("Позвони маме.", "call"),
        t("Напиши Пете, что я опоздаю.", "message"),
        t("Выключи свет в комнате.", "devices"),
    ], check=untouched),

    # ------------------------------------------------------------------ two in one
    Dialog("вместе", "два вопроса в одной фразе", [
        t("Что у меня завтра и какая погода?", "plan_ask+weather", ["хакатон"]),
        t("Который час и какое сегодня число?", "time+date"),
    ], check=untouched),
]


# ---------------------------------------------------------------------------------------- round two
# which item the planner picks, words for the time, speech that is not to Orpheus, ways to stop

DIALOGS += [
    Dialog("планы: выбор", "какое из двух: удалить", [
        t("Удали сегодняшнее занятие по русскому.", "plan_delete", "сегодня"),
        t("Нет, завтрашнее.", "confirm|plan_delete", "завтра"),
        t("Да.", "confirm", "удалил"),
    ], check=lambda ctx: first(gone(ctx, "русск", 1), kept(ctx, "русск", 0))),
    Dialog("планы: выбор", "после списка по номеру", [
        t("Что у меня завтра?", "plan_ask", "хакатон"),
        t("Второе перенеси на 12.", "plan_move", "12:00"),
        t("А последнее удали.", "plan_delete", "?"),
        t("Давай.", "confirm", "удалил"),
    ], check=lambda ctx: first(one(items(ctx, "хакатон", 1), "12:00"), kept(ctx, "русск", 1), gone(ctx, "банк", 1))),
    Dialog("планы: выбор", "его и её после записи", [
        t("Добавь на завтра в 18 встречу с Лёшей.", PLAN_WRITE, "18:00"),
        t("Перенеси её на час позже.", "plan_move", "19:00"),
        t("И переименуй в встречу с Лёшей и Катей.", "plan_rename", "катей"),
    ], check=lambda ctx: one(items(ctx, "лёш", 1) or items(ctx, "леш", 1), "19:00")),
    Dialog("планы: выбор", "спрашивают про одно из многих", [
        t("А Хакатон во сколько заканчивается?", "plan_ask", "13"),
        t("Сколько у меня дел на завтра?", "plan_ask", "4|четыре"),
        t("Что я делаю %s?" % on(2), "plan_ask", "трениров"),
        t("Где я завтра в 10?", "plan_ask", "хакатон|русск"),
    ], check=untouched),
    Dialog("планы: запись", "названия с предлогами", [
        t("Добавь встречу в кафе с Машей %s в 7 вечера." % on(4), PLAN_WRITE, "19:00"),
        t("Напомни %s поздравить Сашу с днём рождения." % on(5), PLAN_WRITE, "поздрав"),
        t("Добавь день рождения мамы 12 ноября.", PLAN_WRITE, "12 ноября"),
    ], check=lambda ctx: first(
        one(items(ctx, "кафе", 4), "19:00", "Встреча в кафе с Машей"), one(items(ctx, "поздрав", 5)),
        one([i for i in items(ctx, "мамы") if i["date"].endswith("-11-12")]))),
    Dialog("планы: запись", "время словами", [
        t("Добавь на завтра в семь тридцать утра зарядку.", PLAN_WRITE, "07:30"),
        t("Поставь на послезавтра в четверть девятого созвон.", PLAN_WRITE, "08:15|20:15"),
        t("Запиши на %s без пятнадцати десять стрижку." % ACC_ON(4), PLAN_WRITE, "09:45|21:45"),
        t("Добавь на завтра в пол третьего обед с отцом.", PLAN_WRITE, "14:30"),
    ], check=lambda ctx: first(
        one(items(ctx, "зарядк", 1), "07:30"), one(items(ctx, "созвон", 2)), one(items(ctx, "стрижк", 4)), one(items(ctx, "обед", 1), "14:30"))),
    Dialog("планы: запись", "без точного времени", [
        t("Добавь на завтра утром пробежку.", PLAN_WRITE, "пробежк"),
        t("Запланируй на выходные поход в горы.", PLAN_WRITE, "поход"),
        t("Добавь на следующей неделе в четверг в 18 бассейн.", PLAN_WRITE, "18:00"),
    ], check=lambda ctx: first(one(items(ctx, "пробежк", 1)), one(items(ctx, "поход")), one(items(ctx, "бассейн"), "18:00"))),
    Dialog("планы: запись", "дело на весь день и срок", [
        t("Добавь задачу сдать отчёт до пятницы.", PLAN_WRITE, "отчёт|отчет"),
        t("Напомни в среду оплатить интернет.", PLAN_WRITE, "интернет"),
    ], check=lambda ctx: first(one(items(ctx, "отч")), one(items(ctx, "интернет")))),
    Dialog("планы: удаление", "всё за день", [
        t("Очисти планы на завтра.", "plan_delete", "?"),
        t("Нет.", "confirm", no="удалил"),
        t("Удали все дела на послезавтра.", "plan_delete", "?"),
        t("Да, всё удаляй.", "confirm", "удалил"),
    ], check=lambda ctx: first(kept(ctx, "хакатон", 1), gone(ctx, "трениров", 2))),
    Dialog("планы: чтение", "свободное время", [
        t("У меня есть время %s после обеда?" % on(3), "plan_free"),
        t("Я занят завтра утром?", "plan_free", "хакатон|русск"),
        t("Когда я завтра свободен?", "plan_free|plan_ask"),
    ], check=untouched),

    Dialog("ловушки", "разговор не с Орфеем, но про планы", [
        t("Я завтра в 10 не смогу прийти.", MODEL),
        t("Он сказал, что перенесёт встречу на завтра.", MODEL),
        t("Мама сказала, что в субботу в 12 придёт сантехник.", MODEL + "|" + PLAN_WRITE),
        t("Мне надо было вчера в 5 позвонить.", MODEL),
    ], check=lambda ctx: added_none(ctx, "смогу", "перенес", "позвон")),
    Dialog("ловушки", "удали и отмени вообще", [
        t("Удали.", "plan_delete|" + MODEL + "|note_delete", no="удалил"),
        t("Отмени.", "undo|" + MODEL + "|plan_delete", no="удалил"),
        t("Всё отменяется.", MODEL + "|ack|plan_delete", no="удалил"),
    ], check=untouched),
    Dialog("ловушки", "добавь, но не в планы", [
        t("Добавь громкости.", MODEL + "|devices"),
        t("Добавь сахар в чай.", MODEL),
        t("Поставь чайник.", MODEL + "|devices"),
    ], check=untouched),

    Dialog("стоп", "другие способы замолчать", [
        t("Помолчи.", "stop", silent=True),
        t("Замолчи.", "stop", silent=True),
        t("Отбой.", "stop|bye", silent=True),
        t("Тихо.", "stop", silent=True),
        t("Всё, свободен.", "stop|bye"),
    ]),
    Dialog("личное", "выйти и войти по-разному", [
        t("Включи личный режим.", "", "между нами"),
        t("Выйди из личного.", "", "обычному"),
        t("Открой личное.", "", "между нами"),
        t("Обычный режим.", "", "обычному"),
    ], personal=True),

    Dialog("память", "о себе", [
        t("Меня зовут Иван.", "remember|my_name", "иван"),
        t("Мой день рождения 15 марта.", "my_birthday|remember"),
        t("Когда у меня день рождения?", "my_birthday", "15 марта"),
        t("Я живу в Казани.", "remember|moved|" + MODEL),
        t("Как меня зовут?", "ask_name", "иван"),
    ]),
    Dialog("погода", "ещё варианты", [
        t("Сегодня холодно?", "weather_degrees|weather"),
        t("Какая погода будет вечером?", "weather"),
        t("Нужны ли перчатки?", "weather_clothes"),
        t("Во сколько закат?", "weather_sun"),
        t("Будет ли снег на неделе?", "weather_rain"),
    ]),
    Dialog("время", "ещё про время", [
        t("Сколько времени осталось до полуночи?", "time_until"),
        t("Какой сейчас год?", "year_month"),
        t("Этот год високосный?", "leap_year"),
        t("Какого числа будет следующий понедельник?", "date_of"),
        t("Через сколько дней Новый год?", "days_until"),
    ]),
    Dialog("интернет", "ещё варианты", [
        t("Что там с курсом евро?", "rate"),
        t("Сколько 50 евро в рублях?", "rate"),
        t("Найди рецепт борща.", "web_search"),
        t("Загугли, что такое LLM.", "web_search|wiki"),
        t("Расскажи про Эйфелеву башню.", "wiki|web_search|" + MODEL),
    ]),
    Dialog("заметки", "ещё варианты", [
        t("Запиши заметку купить лампочки и батарейки.", "note_add"),
        t("Допиши к последней заметке ещё удлинитель.", "note_append"),
        t("Прочитай последнюю заметку.", "note_last", "удлинитель"),
        t("Удали заметку про блины.", "note_delete"),
        t("Да.", "confirm|note_delete|ack"),
    ], check=lambda ctx: None if not any("блин" in (n.get("title", "") + n.get("body", "")).lower() for n in ctx.scratch.notes())
        else "заметка про блины не удалена"),
    Dialog("болтовня", "ещё варианты", [
        t("Доброе утро!", "greet"),
        t("Ну как ты там?", "how_are_you"),
        t("Спасибо, друг.", "thanks"),
        t("Пока-пока.", "bye"),
        t("Ага, понятно.", "ack"),
        t("Хм.", "ack"),
    ], check=untouched),
    Dialog("вместе", "ещё пары", [
        t("Какая погода и что у меня сегодня?", "weather+plan_ask|plan_ask+weather"),
        t("Добавь на завтра в 9 созвон и напомни в 10 позвонить врачу.", "plan_add+plan_add"),
    ]),
]

# ---------------------------------------------------------------------------------------- round three
# whole conversations the way they go, corrections said in passing, dates in words, hesitation

DIALOGS += [
    Dialog("разговор", "утро с планами", [
        t("Доброе утро.", "greet"),
        t("Что у меня завтра?", "plan_ask", "хакатон"),
        t("Перенеси русский на 11.", "plan_move", "11:00"),
        t("А Хакатон тогда на 14.", "plan_move|plan_fix", "14:00"),
        t("И напомни мне завтра в 9 взять документы.", PLAN_WRITE, "09:00"),
        t("Что в итоге завтра?", "plan_ask", ["11:00", "14:00", "документ"]),
    ], check=lambda ctx: first(one(items(ctx, "русск", 1), "11:00"), one(items(ctx, "хакатон", 1), "14:00"),
                               one(items(ctx, "документ", 1), "09:00"), kept(ctx, "русск", 0))),
    Dialog("разговор", "запись с поправкой на ходу", [
        t("Добавь на завтра в 10, нет, в 11 встречу с бухгалтером.", PLAN_WRITE, "11:00"),
        t("Эээ, добавь, короче, на послезавтра созвон в 5.", PLAN_WRITE, "17:00"),
        t("Поставь на завтра созвон в Zoom в 16:30.", PLAN_WRITE, "16:30"),
    ], check=lambda ctx: first(one(items(ctx, "бухгалтер", 1), "11:00"), one(items(ctx, "созвон", 2), "17:00"),
                               one(items(ctx, "zoom", 1) or items(ctx, "зум", 1), "16:30"))),
    Dialog("разговор", "даты словами", [
        t("Добавь на первое октября встречу выпускников в 19.", PLAN_WRITE, "1 октября"),
        t("Запиши на 15-е в 10 утра визит к нотариусу.", PLAN_WRITE, "15"),
        t("Добавь через неделю в это же время созвон с Олегом.", PLAN_WRITE, "созвон"),
        t("Поставь через 3 дня в 18 бассейн.", PLAN_WRITE, "18:00"),
    ], check=lambda ctx: first(
        one([i for i in items(ctx, "выпускник") if i["date"].endswith("-10-01")], "19:00"),
        one([i for i in items(ctx, "нотариус") if i["date"].endswith("-15")], "10:00"),
        one(items(ctx, "олег", 7)), one(items(ctx, "бассейн", 3), "18:00"))),
    Dialog("разговор", "план на вечер и вопросы вокруг", [
        t("Какая погода вечером?", "weather"),
        t("А у меня вечером что-нибудь есть?", "plan_ask|plan_free|plan_left"),
        t("Тогда добавь на завтра в 21 прогулку.", PLAN_WRITE, "21:00"),
        t("Спасибо.", "thanks"),
    ], check=lambda ctx: one(items(ctx, "прогулк", 1), "21:00")),
    Dialog("разговор", "задача и её судьба", [
        t("Напомни купить корм коту.", PLAN_WRITE, "корм"),
        t("Купил корм.", "plan_did|plan_done"),
        t("Что у меня сегодня из задач осталось?", "plan_left|plan_ask"),
    ], check=lambda ctx: None if all(i["done"] for i in items(ctx, "корм")) else "корм не отмечен"),
    Dialog("разговор", "удалить и передумать", [
        t("Удали тренировку.", "plan_delete", "?"),
        t("Да.", "confirm", "удалил"),
        t("Ой, нет, верни её.", "undo", "вернул"),
        t("Перенеси её лучше на четверг.", "plan_move", "четверг"),
    ], check=lambda ctx: one([i for i in items(ctx, "трениров") if bd.date.fromisoformat(i["date"]).weekday() == 3])),
    Dialog("разговор", "вопросы про прошлое", [
        t("Что у меня было вчера?", "plan_ask"),
        t("Я сегодня что-нибудь пропустил?", "plan_ask|plan_left|" + MODEL),
    ], check=untouched),
    Dialog("разговор", "много дел за раз", [
        t("Добавь на завтра: в 9 пробежка, в 13 обед с Никитой и в 20 кино.", PLAN_WRITE + "+" + PLAN_WRITE + "+" + PLAN_WRITE
          + "|" + PLAN_WRITE + "+" + PLAN_WRITE + "|" + PLAN_WRITE, "09:00"),
    ], check=lambda ctx: first(one(items(ctx, "пробежк", 1), "09:00"), one(items(ctx, "никит", 1), "13:00"),
                               one(items(ctx, "кино", 1), "20:00"))),
    Dialog("разговор", "уточняющие ответы", [
        t("Добавь встречу.", PLAN_WRITE + "|" + MODEL),
        t("Завтра в 12.", "plan_fix|plan_add|" + MODEL),
    ], check=lambda ctx: one(items(ctx, "встреч", 1), "12:00")),
]

class _Quiet:
    """The model's stand-in for --no-model: every phrase that reaches it gets "(модель)"."""

    def chat(self, messages, tools=None, num_predict=None):
        yield {"message": {"content": "(модель)"}}
        yield {"done": True}

    def warmup(self, messages, tools=None):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--plannerd", default=os.path.expanduser("~/.local/bin/plannerd"))
    ap.add_argument("--no-model", action="store_true", help="routing only: no model, no internet")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    groups = {s.strip() for s in args.group.split(",") if s.strip()}
    config = Config()
    llm = None
    if args.no_model:
        llm = _Quiet()
        embedder = None
        dead = Web("http://127.0.0.1:9", timeout=1)
        weather, search = Weather(dead, now=lambda: NOW), Search(dead)
    else:
        embedder = bd.load_embedder(config.models)
        web = Web(config.proxy)
        weather, search = Weather(web, home=MOSCOW, now=lambda: NOW), Search(web)
        Brain(config, bd.Memory(":memory:"), planner=bd.PlannerTools(bd.Planner(bd.BASE))).warmup()
    print("время прогона: %s%s" % (NOW.strftime("%d.%m.%Y %H:%M"), " (без модели и интернета)" if args.no_model else ""), flush=True)
    results = []
    for dialog in DIALOGS:
        if (only and dialog.name not in only) or (groups and dialog.group not in groups):
            continue
        for turn in dialog.turns:  # as the server does with the phrase after «Орфей»
            turn.phrase = strip_wake_word(turn.phrase) or turn.phrase
        res = bd.run(dialog, embedder, weather, search, args.plannerd, llm=llm)
        if args.no_model:
            # the model's words are a stand-in: for a phrase that was expected to be the model's, only who took
            # it counts; and what the model would have written is not there to check at the end
            modelled = False
            for turn, tt in zip(dialog.turns, res["turns"]):
                # the model's own, or a scenario's that handed the answer to the model to word
                if tt["by"] == MODEL and (MODEL in turn.by.split("|") or tt["scenario"] in turn.by.split("|")):
                    tt["problems"] = [p for p in tt["problems"] if p.startswith("ответил")]
                    modelled = True
            if modelled and res["end"] and dialog.check not in (untouched,):
                res["end"] = None
            res["ok"] = not res["end"] and not any(tt["problems"] for tt in res["turns"])
        results.append(res)
        print("\n%s %s / %s%s" % ("✓" if res["ok"] else "✗", dialog.group, dialog.name,
                                  "  — " + res["end"] if res["end"] else ""), flush=True)
        for tt in res["turns"]:
            who = tt["by"] if tt["scenario"] in (None, tt["by"]) else "%s→%s" % (tt["scenario"], tt["by"])
            print("  %s [%s] %s\n      -> %s%s" % (
                "✗" if tt["problems"] else "·", who, tt["phrase"], tt["reply"].replace("\n", " ").strip()[:220],
                ("\n      вызовы: " + " ".join("%s(%s)" % (n, json.dumps(a, ensure_ascii=False)) for n, a in tt["calls"]))
                if tt["calls"] else ""), flush=True)
            if tt["problems"]:
                print("      !! " + "; ".join(tt["problems"]), flush=True)
    turns = [tt for r in results for tt in r["turns"]]
    bad = [tt for tt in turns if tt["problems"]]
    print("\nдиалогов без замечаний: %d из %d; реплик без замечаний: %d из %d" % (
        sum(r["ok"] for r in results), len(results), len(turns) - len(bad), len(turns)))
    if args.json:
        Path(args.json).write_text(json.dumps({"now": NOW.isoformat(), "results": results}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
