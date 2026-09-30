"""The scenarios the program answers by itself (skills.py, intents_ru.txt): phrases as the
recogniser writes them - capitals, punctuation, digits - and what is said back."""

import random
from datetime import datetime

import pytest

from orpheus.brain import Brain
from orpheus.config import Config
from orpheus.intents import Intents, normalize
from orpheus.memory import Memory
from orpheus.skills import Context
from test_brain import FakeLLM, text, tool
from test_planner import FakePlanner, PlannerTools

NOW = datetime(2026, 9, 25, 14, 3)  # a Friday afternoon
FRI = NOW.date()


def planner(*items):
    return PlannerTools(FakePlanner(*items), today=lambda: FRI, now=lambda: NOW)


def make(*items, replies=(), personal=False, with_planner=True):
    brain = Brain(Config(), Memory(":memory:"), FakeLLM(*replies), private=Memory(":memory:"),
                  planner=planner(*items) if with_planner else None)
    brain.skills.rng = random.Random(1)
    if personal:
        brain.set_personal(True)
    return brain


def say(brain, phrase):
    return "".join(brain.ask(phrase, now=NOW))


# ------------------------------------------------------------------------------------ the engine

def test_template_syntax():
    it = Intents("""
<сейчас> = [сейчас|щас]
[time]
(который|сколько) <сейчас> час
[note]
<сделай> заметк(у|а) {text}
<сделай> = (сделай|создай)
[unit]
какой сейчас {unit:год|месяц|время года}
[range]
от {a} до {b}
""")
    assert [m.intent for m in it.match("Который сейчас час?")] == ["time"]
    assert [m.intent for m in it.match("сколько час")] == ["time"]
    assert it.match("который час в Токио") == []
    note = it.match("Сделай заметку: Купить хлеб, молоко.")[0]
    assert note.slots == {"text": "Купить хлеб, молоко"}  # cut out of the original, capitals and commas kept
    assert it.match("создай заметка х")[0].slots == {"text": "х"}
    assert it.match("какой сейчас время года")[0].slots == {"unit": "время года"}
    assert it.match("от 1 до 100")[0].slots == {"a": "1", "b": "100"}
    assert normalize("Ёж, привет!") == "еж  привет "


def test_fillers_around_do_not_matter():
    it = Intents.load()
    for phrase in ["Который час?", "Орфей, скажи пожалуйста, который час?", "А который час?", "Слушай, который час, пожалуйста",
                   "Ну, сколько времени?", "Подскажи время"]:
        assert it.match(phrase)[0].intent == "time", phrase


def test_the_most_specific_template_wins():
    it = Intents.load()
    assert it.match("Удали заметку про ремонт.")[0].intent == "note_delete"
    assert it.match("Удали физику.")[0].intent == "plan_delete"
    assert it.match("Поставь будильник на 7.")[0].intent == "alarm"
    assert it.match("Поставь на пятницу тренировку в 8 вечера.")[0].intent == "plan_hint"
    assert it.match("Какая погода завтра?")[0].intent == "weather"
    assert it.match("Что ты умеешь?")[0].intent == "capabilities"
    assert it.match("Запомни в заметках код 4521")[0].intent == "note_add"


# ------------------------------------------------------------------------------------ what goes where

PROGRAM = {  # phrase -> the scenario that answers it, without the model
    "Который час?": "time", "Сколько сейчас времени?": "time", "Время?": "time",
    # «напомни» перед вопросом — вопрос, не напоминание
    "Напомни, что ты умеешь делать?": "capabilities", "Напомни какое сегодня число": "date",
    "Какое сегодня число?": "date", "Какой сегодня день недели?": "date", "Какая сегодня дата?": "date",
    "Какой день недели будет 1 октября?": "date_of", "Какое число будет в пятницу?": "date_of",
    "Какое число было вчера?": "date_of", "Какой сейчас год?": "year_month", "Какой сейчас месяц?": "year_month",
    "Сколько дней до Нового года?": "days_until", "Сколько осталось до 1 октября?": "days_until",
    "Сколько будет 15 умножить на 37?": "calc", "Посчитай 20% от 3 000.": "calc", "Сколько будет 2 плюс 2?": "calc",
    "Чему равен корень из 144?": "calc",
    "Сделай заметку: купить молоко и хлеб.": "note_add", "Заметка: код от домофона 4521.": "note_add",
    "Создай заметку про ремонт, купить краску.": "note_add", "Новая заметка: позвонить в банк.": "note_add",
    "Прочитай мои заметки.": "note_list", "Какие у меня заметки?": "note_list", "Что у меня в заметках?": "note_list",
    "Прочитай последнюю заметку.": "note_last", "Найди заметку про ремонт.": "note_find",
    "Что я записывал про домофон?": "note_find", "Удали заметку про ремонт.": "note_delete",
    "Запомни, что я люблю кофе без сахара.": "remember", "Забудь, что я люблю кофе.": "forget",
    "Что у меня завтра?": "plan_ask", "А что завтра?": "plan_ask", "Какие планы на неделю?": "plan_ask",
    "Что у меня дальше?": "plan_next",
    "Запиши на завтра в 8 вечера тренировку один раз.": "record", "Напомни через 2 ч выпить таблетку.": "plan_add",
    "Запиши на 3 октября в 15:30 стоматолога, один раз.": "record",
    "Отметь физику выполненной.": "plan_done", "Перенеси физику на завтра в 15.": "plan_move",
    "Удали физику.": "plan_delete", "Отмени завтра в 10 занятие по русскому.": "plan_delete", "Удари физику.": "plan_delete",
    "Повтори.": "repeat", "Что ты сказал?": "repeat", "Привет": "greet", "Добрый вечер!": "greet",
    "Спасибо.": "thanks", "Спасибо большое!": "thanks", "Пока.": "bye", "Спокойной ночи.": "bye", "Как дела?": "how_are_you",
    "Как дело?": "how_are_you", "Стоп.": "stop", "Хватит.": "stop", "Сто.": "stop", "Квати.": "stop", "Stop.": "stop", "Орфей, не слушать.": "pause",
    "Не слушай меня пока.": "pause",
    "Кто ты?": "who", "Как тебя зовут?": "who", "Что ты умеешь?": "capabilities", "Помощь.": "capabilities",
    "Давай начнём сначала.": "reset", "Подбрось монетку.": "coin", "Отбрось монетку.": "coin", "Брось кубик.": "dice",
    "Загадай число от 1 до 10.": "random_number", "Какая погода завтра?": "weather",
    "Включи музыку.": "music", "Позвони маме.": "call", "Включи свет на кухне.": "devices",
    "Поставь таймер на 5 минут.": "timer", "Поставь будильник на 7.": "alarm", "Разбуди меня в 7.": "alarm",
    "Ладно.": "ack",
}

MODEL = [  # these must reach the model: no scenario may answer them
    "Что такое фотосинтез?", "Расскажи анекдот.", "Как приготовить борщ?", "Почему небо голубое?",
    "Сколько времени займёт дорога до Москвы?", "Сколько будет стоить ремонт кухни?", "Который час в Токио?",
    "Кто написал «Евгения Онегина»?", "Мне грустно.", "Как ты думаешь, стоит ли мне менять работу?",
    "Переведи на английский: доброе утро.", "Придумай стих про кота.",
    "Какой у меня код от домофона?", "У меня завтра день рождения.", "Что лучше: чай или кофе?",
    "Объясни, как работает двигатель.",
]


def test_program_scenarios():
    brain = make({"id": "p", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "16:00", "end_time": "17:30"},
                 {"id": "r", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-26", "start_time": "10:00"})
    brain.last_reply = "Готово."
    for phrase, intent in PROGRAM.items():
        brain.skills.pending = None
        result = brain.skills.handle(phrase, NOW)
        assert result is not None and not isinstance(result, Context), phrase
        assert brain.skills.handled == intent, "%s: %s" % (phrase, brain.skills.handled)


def test_model_phrases():
    brain = make({"id": "r", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-26", "start_time": "10:00"})
    brain.last_reply = "Готово."
    for phrase in MODEL:
        result = brain.skills.handle(phrase, NOW)
        assert result is None or isinstance(result, Context), "%s -> %s: %r" % (phrase, brain.skills.handled, result)


# ------------------------------------------------------------------------------------ time and date

def test_time_and_date():
    brain = make()
    assert say(brain, "Который час?") == "Сейчас 14:03."
    assert say(brain, "Какое сегодня число?") == "Сегодня пятница, 25 сентября."
    assert say(brain, "Какое число будет завтра?") == "Завтра будет суббота, 26 сентября."
    assert say(brain, "Какой день недели будет 1 октября?") == "1 октября — четверг."
    assert say(brain, "Какое число будет в понедельник?") == "В понедельник — 28 сентября."
    assert say(brain, "Какое число было вчера?") == "Вчера был четверг, 24 сентября."
    assert say(brain, "Какой сейчас год?") == "Сейчас 2026 год."
    assert say(brain, "Какой сейчас месяц?") == "Сейчас сентябрь."
    assert say(brain, "Сколько дней до Нового года?") == "До Нового года 98 дней, это будет пятница."
    assert say(brain, "Сколько осталось до 1 октября?") == "До 1 октября 6 дней, это будет четверг."
    assert say(brain, "Сколько дней до 1 сентября?") == "До 1 сентября 341 день, это будет среда."
    assert brain.llm.requests == []


def test_sums():
    brain = make()
    assert say(brain, "Сколько будет 15 умножить на 37?") == "15 умножить на 37 — 555."
    assert say(brain, "Посчитай 20% от 3 000.") == "20% от 3 000 — 600."
    assert say(brain, "Сколько будет 100 разделить на 3?") == "100 разделить на 3 — 33,33."
    assert say(brain, "Сколько будет 5 разделить на 0?") == "На ноль делить нельзя."
    assert brain.llm.requests == []


# ------------------------------------------------------------------------------------ notes

def notes(brain):
    return [(r["title"], r["body"]) for r in brain.planner.planner.notes()]


def test_notes_go_to_the_planner():
    brain = make()
    assert say(brain, "Сделай заметку: купить молоко и хлеб.") == "Записал в заметки: Купить молоко и хлеб."
    assert say(brain, "Сделай заметку про ремонт, купить краску и валик.") == \
        "Записал в заметки «Ремонт»: купить краску и валик."
    assert say(brain, "Заметка: код от домофона 4521.") == "Записал в заметки: Код от домофона 4521."
    long = ("Запиши, что в субботу приедет мастер чинить стиральную машину, нужно освободить проход в ванную "
            "и убрать вещи с полки")
    assert say(brain, long).startswith("Записал в заметки: В субботу приедет мастер")
    assert notes(brain)[:3] == [("", "В субботу приедет мастер чинить стиральную машину, нужно освободить проход в ванную "
                                     "и убрать вещи с полки"),
                                ("Код от домофона 4521", ""), ("Ремонт", "Купить краску и валик")]
    assert brain.llm.requests == [] and brain.memory.recent_notes() == []


def test_notes_are_read_found_and_deleted_with_a_yes():
    brain = make()
    say(brain, "Сделай заметку про ремонт: купить краску и валик.")
    say(brain, "Заметка: код от домофона 4521.")
    assert say(brain, "Прочитай мои заметки.") == "В заметках: Код от домофона 4521; Ремонт."
    assert say(brain, "Прочитай последнюю заметку.") == "Последняя заметка: Код от домофона 4521."
    assert say(brain, "Найди заметку про ремонт.") == "«Ремонт»: Купить краску и валик."
    assert say(brain, "Найди заметку про самолёт.") == "Не нашёл заметок про самолёт."
    assert say(brain, "Удали заметку про ремонт.") == "Удалить заметку «Ремонт»?"
    assert say(brain, "Нет.") == "Хорошо, не удаляю."
    assert len(notes(brain)) == 2
    say(brain, "Удали заметку про ремонт.")
    assert say(brain, "Да.") == "Удалил заметку «Ремонт»."
    assert notes(brain) == [("Код от домофона 4521", "")]
    assert say(brain, "Верни.") == "Вернул заметку «Ремонт»."
    assert len(notes(brain)) == 2
    say(brain, "Удали заметку про ремонт.")
    assert say(brain, "За.") == "Удалил заметку «Ремонт»."  # a short "да" heard as "за"
    assert say(brain, "Верни.") == "Вернул заметку «Ремонт»."
    assert brain.llm.requests == []


def test_in_the_personal_section_notes_stay_on_the_laptop():
    brain = make(personal=True)
    assert say(brain, "Сделай заметку: поговорить с мамой о лете.") == "Записал в личные заметки: Поговорить с мамой о лете."
    assert notes(brain) == [] and brain.private.recent_notes()[0]["text"] == "Поговорить с мамой о лете"
    assert say(brain, "Прочитай мои заметки.") == "Одна заметка: Поговорить с мамой о лете."


def test_the_models_note_tools_use_the_planner_too():
    brain = make(replies=[[{"message": {"content": "", "tool_calls": [{"function": {"name": "add_note",
                                                                                     "arguments": {"text": "идея: сад"}}}]}},
                           {"done": True}]])
    assert say(brain, "У меня есть идея насчёт сада, сохрани её") == "Записал."
    assert notes(brain) == [("", "идея: сад")]
    assert "идея" in brain.run_tool("find_notes", {"query": "сад"})
    assert brain.run_tool("delete_note", {"id": 1}) == "удалено" and notes(brain) == []


# ------------------------------------------------------------------------------------ facts

def test_remember_forget_and_bring_back():
    brain = make()
    assert say(brain, "Запомни, что я люблю кофе без сахара.") == "Запомнил."
    assert say(brain, "Запомни: мою сестру зовут Аня.") == "Запомнил."
    assert brain.memory.facts() == [(1, "Я люблю кофе без сахара"), (2, "Мою сестру зовут Аня")]
    assert say(brain, "Забудь, что я люблю кофе.") == "Забыл: ты любишь кофе без сахара."
    assert brain.memory.facts() == [(2, "Мою сестру зовут Аня")]
    assert say(brain, "Верни как было.") == "Вернул в память: Я люблю кофе без сахара."
    assert say(brain, "Забудь про самолёты.") == "Такого я не помню."
    assert brain.llm.requests == []


def test_remember_a_plan_is_a_plan():
    brain = make()
    assert say(brain, "Запомни, что завтра в 10 у меня врач.").startswith("Добавил на")
    assert brain.memory.facts() == []


# ------------------------------------------------------------------------------------ the planner

def test_plan_next_and_add_in_colloquial_time():
    brain = make({"id": "p", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "16:00"},
                 {"id": "r", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-26", "start_time": "10:00"})
    assert say(brain, "Что у меня дальше?") == "Дальше в 16:00 Физика."
    say(brain, "Напомни через 2 ч выпить таблетку.")
    assert brain.planner.planner.quick_text == "в 16:03 выпить таблетку"
    say(brain, "Запиши в половине восьмого ужин с Олей один раз.")
    assert brain.planner.planner.quick_text == "в 19:30 ужин с Олей"
    say(brain, "Запиши на завтра в 8 вечера тренировку, один раз.")
    assert brain.planner.planner.quick_text == "завтра в 20:00 тренировку"


def test_undo_after_adding_removes_it():
    brain = make()
    say(brain, "Запиши на завтра в 12 созвон, один раз.")
    assert "new" in brain.planner.planner.rows
    assert say(brain, "Верни как было.").startswith("Убрал из планов:")
    assert "new" not in brain.planner.planner.rows


# ------------------------------------------------------------------------------------ talk

def test_small_talk_and_repeat():
    brain = make(replies=[text("Жил-был кот.")])
    assert say(brain, "Повтори.") == "Я пока ничего не говорил."
    say(brain, "Расскажи сказку.")
    assert say(brain, "Повтори.") == "Жил-был кот."
    assert say(brain, "Привет!").startswith("Добрый день! Сегодня пятница, 25 сентября.")  # the day's first: its summary
    assert say(brain, "Привет!") in ("Добрый день. Чем могу помочь?", "Добрый день. Слушаю вас.", "Добрый день. Чем займёмся?")
    assert say(brain, "Спасибо!") in ("Всегда пожалуйста.", "Рад стараться.", "Для этого я здесь.")
    assert say(brain, "Кто ты?") == "Я Орфей, твой личный голосовой ассистент."
    assert say(brain, "Ладно.") == ""  # nothing to say to that
    assert say(brain, "Какая погода завтра?") == "Погоды у меня нет: выход в интернет выключен."
    assert say(brain, "Подбрось монетку.") in ("Орёл.", "Решка.")
    assert say(brain, "Брось кубик.") in ["Выпало %d." % n for n in range(1, 7)]
    n = int(say(brain, "Загадай число от 1 до 10.").split()[-1].rstrip("."))
    assert 1 <= n <= 10


def test_a_yes_or_no_to_the_models_question_is_the_models():
    brain = make(replies=[text("Хотите, я запишу это?"), tool("add_note", text="Не забыть про встречу")])
    say(brain, "Мне нужно не забыть про встречу.")
    assert say(brain, "Да.") == "Записал."
    assert len(brain.llm.requests) == 2


def test_in_the_personal_section_small_talk_is_the_models():
    brain = make(replies=[text("Привет. Я рядом.")], personal=True)
    assert say(brain, "Привет") == "Привет. Я рядом."
    assert say(brain, "Который час?") == "Сейчас 14:03."


def test_reset_starts_a_fresh_conversation():
    brain = make(replies=[text("Жил-был кот.")])
    say(brain, "Расскажи сказку.")
    assert say(brain, "Давай начнём сначала.") == "Хорошо, начнём с чистого листа."
    assert brain.history == [] and brain.tail == [{"role": "user", "content": "Давай начнём сначала."},
                                                  {"role": "assistant", "content": "Хорошо, начнём с чистого листа."}]


def test_the_scenario_is_named_for_the_log():
    brain = make(replies=[text("Кот.")])
    say(brain, "Который час?")
    assert brain.handled == "time"
    say(brain, "Расскажи сказку.")
    assert brain.handled == "модель"


def test_without_the_internet_search_says_so():
    brain = make()
    assert say(brain, "Найди в интернете, кто такой Тесла.") == "Искать в интернете я не могу: выход в интернет выключен."


def test_every_scenario_has_a_handler():
    from orpheus.skills import Skills
    missing = [name for name in Intents.load().names if not hasattr(Skills, "do_" + name)]
    assert missing == []


@pytest.mark.parametrize("phrase", list(PROGRAM))
def test_program_phrases_are_fast(phrase):
    # the best of three: one slow run is the machine (a GC pause, a busy laptop), not the phrase;
    # 0.1 s is still far below what a model would take
    import time
    best = float("inf")
    for _ in range(3):
        brain = make()
        t = time.perf_counter()
        brain.skills.handle(phrase, NOW)
        best = min(best, time.perf_counter() - t)
    assert best < 0.1


def test_program_answers_reach_the_model_only_as_the_last_exchange():
    brain = make({"id": "r", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-26", "start_time": "10:00"},
                 replies=[text("В 10.")])
    for phrase in ["Который час?", "Какое сегодня число?", "Сколько будет 2 плюс 2?", "Что у меня завтра?"]:
        say(brain, phrase)
    say(brain, "А что мне взять с собой?")
    sent = brain.llm.requests[0]
    assert [m["role"] for m in sent] == ["system", "system", "user", "assistant", "user"]
    assert sent[2]["content"] == "Что у меня завтра?"  # the one right before; the earlier ones never reach the model


def test_a_long_first_sentence_starts_sounding_at_its_first_clause():
    from orpheus.speech import Sentences
    s = Sentences(first_clause=20)
    reply = ("Сегодня, пятница, 25 сентября: с 12:00 до 13:30 Физика; в 18:00 Занятие по русскому языку; "
             "задача «Купить хлеб». Завтра ничего.")
    first = s.feed(reply)[0]
    assert first == "Сегодня, пятница, 25 сентября:"


def test_a_note_is_found_whatever_letters_it_was_written_in():
    brain = make()
    brain.planner.planner.save({"kind": "note", "title": "Wi-Fi", "body": "пароль sunflower42"})
    assert say(brain, "Найди заметку про вайфай.") == "«Wi-Fi»: пароль sunflower42."
    assert say(brain, "Удали заметку про вай-фай.") == "Удалить заметку «Wi-Fi»?"


def test_task_words_are_not_the_title():
    brain = make()
    say(brain, "Запиши задачу купить молоко без времени один раз.")
    assert brain.planner.planner.quick_text == "купить молоко"
    say(brain, "Запиши на завтра встречу с Петей, весь день, один раз.")
    assert brain.planner.planner.quick_text == "завтра встречу с Петей"
    say(brain, "Запиши на субботу поход в планетарий и купить готовую еду без времени один раз.")
    assert brain.planner.planner.quick_text == "на субботу поход в планетарий и купить готовую еду"


def test_a_note_is_kept_on_the_laptop_when_the_planner_is_down():
    brain = make()
    brain.planner.planner.down = True
    brain.planner.planner.save = lambda item: (_ for _ in ()).throw(__import__("orpheus.planner").planner.Unavailable("нет"))
    assert say(brain, "Сделай заметку: позвонить в банк.") == \
        "Записал в память ноута, Planner сейчас не отвечает: Позвонить в банк."
    assert brain.memory.recent_notes()[0]["text"] == "Позвонить в банк"


# ------------------------------------------------------------------------------------ conversations

WEEK = [
    {"id": "f", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "16:00", "end_time": "17:30"},
    {"id": "r", "kind": "event", "title": "Занятие по русскому языку", "date": "2026-09-26", "start_time": "10:00"},
    {"id": "a", "kind": "event", "title": "Хакатон", "date": "2026-09-26", "start_time": "10:00", "end_time": "13:00"},
    {"id": "e", "kind": "event", "title": "ЕГЭ", "date": "2026-09-26", "start_time": "22:00", "end_time": "23:40"},
    {"id": "b", "kind": "task", "title": "Позвонить в банк", "date": "2026-09-26"},
    {"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-28", "start_time": "19:00", "end_time": "20:30"},
]


def test_a_short_follow_up_asks_the_last_question_again():
    brain = make(*WEEK)
    assert say(brain, "Что у меня завтра?").startswith("Завтра, суббота, 26 сентября: в 10:00 Занятие")
    assert say(brain, "А в понедельник?") == "Понедельник, 28 сентября: с 19:00 до 20:30 Тренировка."
    assert brain.skills.handled == "plan_ask"
    assert say(brain, "Есть ли завтра физика?").startswith("Нет, завтра такого в планах нет. Есть: в 10:00 Занятие")
    assert say(brain, "А в понедельник?") == "Нет, в понедельник, 28 сентября такого в планах нет. Есть: с 19:00 до 20:30 Тренировка."
    assert say(brain, "Какой сейчас месяц?") == "Сейчас сентябрь."
    assert say(brain, "А год?") == "Сейчас 2026 год."
    assert say(brain, "Сколько дней до Нового года?") == "До Нового года 98 дней, это будет пятница."
    assert say(brain, "А до 8 марта?") == "До 8 марта 164 дня, это будет понедельник."


def test_it_and_the_second_one_are_what_was_just_said():
    brain = make(*WEEK)
    say(brain, "Что у меня завтра?")
    assert say(brain, "Перенеси третье на 21.") == "Перенёс на завтра, субботу, 26 сентября: с 21:00 до 22:40 ЕГЭ."
    assert say(brain, "А первое удали.") == "Удалить завтра, суббота, 26 сентября — в 10:00 Занятие по русскому языку?"
    assert say(brain, "Да.") == "Удалил: завтра, суббота, 26 сентября — в 10:00 Занятие по русскому языку."
    say(brain, "Запиши на завтра в 12 созвон с Петей один раз.")
    assert say(brain, "Перенеси его на 14.") == "Перенёс на завтра, субботу, 26 сентября: в 14:00 Созвон."
    assert say(brain, "Нет, лучше на 15.") == "Перенёс на завтра, субботу, 26 сентября: в 15:00 Созвон."
    assert say(brain, "Удали его.") == "Удалить завтра, суббота, 26 сентября — в 15:00 Созвон?"
    assert say(brain, "Да.") == "Удалил: завтра, суббота, 26 сентября — в 15:00 Созвон."
    assert "new" not in brain.planner.planner.rows and "r" not in brain.planner.planner.rows


def test_a_correction_is_only_right_after_adding_or_moving():
    brain = make(*WEEK, replies=[text("На какой день?")])
    say(brain, "Добавь на завтра в 12 созвон с Петей.")
    say(brain, "Который час?")
    assert brain.skills.handle("Нет, лучше на 15.", NOW) is None  # the moment has passed: not the program's


def test_everything_on_a_day_goes_after_a_yes_and_comes_back():
    brain = make(*WEEK)
    assert say(brain, "Удали все дела на завтра.") == ("Удалить всё завтра — в 10:00 Занятие по русскому языку; с 10:00 до 13:00 "
                                                     "Хакатон; с 22:00 до 23:40 ЕГЭ; задача «Позвонить в банк»?")
    assert say(brain, "Нет.") == "Хорошо, не удаляю."
    assert len(brain.planner.planner.items(FRI, FRI.replace(day=30))) == 6
    say(brain, "Удали всё на завтра.")
    assert say(brain, "Да.") == "Удалил 4 записи."
    assert say(brain, "Верни.").startswith("Вернул: в 10:00 Занятие по русскому языку; с 10:00 до 13:00 Хакатон;")
    assert len(brain.planner.planner.items(FRI, FRI.replace(day=30))) == 6
    assert say(brain, "Удали всё.") == "Скажи, за какой день удалить всё."


def test_two_requests_in_one_phrase_are_both_done():
    brain = make(*WEEK)
    assert say(brain, "Какое сегодня число и что у меня по плану?") == \
        "Сегодня пятница, 25 сентября. По плану: с 16:00 до 17:30 Физика."
    assert brain.skills.handled == "date+plan_ask"
    assert say(brain, "Запомни, что я люблю кофе, и сделай заметку купить кофе.") == \
        "Запомнил. Записал в заметки: Купить кофе."
    assert brain.memory.facts() == [(1, "Я люблю кофе")]
    say(brain, "Запиши на завтра созвон в 12 и тренировку в 18, один раз.")
    assert brain.skills.handled == "record"
    assert brain.planner.planner.quick_text == "завтра тренировку в 18"
    assert say(brain, "Что у меня завтра и послезавтра?").endswith("Послезавтра, воскресенье, 27 сентября: в планах ничего нет.")
    # one request with "и" inside stays one
    say(brain, "Сделай заметку: купить молоко и хлеб.")
    assert brain.skills.handled == "note_add"
    say(brain, "Запиши на субботу поход в планетарий и купить готовую еду без времени, один раз.")
    assert brain.skills.handled == "record"


def test_a_second_half_that_is_no_request_leaves_the_phrase_whole():
    brain = make(*WEEK, replies=[text("Удобную обувь.")])
    assert say(brain, "Что у меня сегодня, и что бы ты посоветовал надеть на физику?") == "Удобную обувь."
    assert "из планировщика: подходящее: сегодня, пятница, 25 сентября — с 16:00 до 17:30 Физика" in \
        brain.llm.requests[0][-1]["content"]
    # a half the program takes, and the rest it cannot: said, and the model answers the rest
    brain = make(*WEEK, replies=[text("Лучше взять зонт.")])
    reply = say(brain, "Что у меня сегодня? И сколько будет стоить такси до физики?")
    assert reply == "Сегодня, пятница, 25 сентября: с 16:00 до 17:30 Физика. Лучше взять зонт."
    assert "уже сказано вслух" in brain.llm.requests[0][-1]["content"]


def test_how_many_and_a_plain_no():
    brain = make(*WEEK)
    assert say(brain, "Сколько у меня завтра дел?") == ("Завтра 4 дела: в 10:00 Занятие по русскому языку; с 10:00 до 13:00 "
                                                      "Хакатон; с 22:00 до 23:40 ЕГЭ; задача «Позвонить в банк».")
    assert say(brain, "Сколько у меня задач на завтра?") == "Завтра 1 дело: задача «Позвонить в банк»."
    assert say(brain, "Сколько дел в среду?") == "В среду, 30 сентября в планах ничего нет."


def test_a_name_is_remembered_and_told():
    brain = make()
    assert say(brain, "Меня зовут Иван.") == "Приятно познакомиться, Иван."
    assert say(brain, "Как меня зовут?") == "Тебя зовут Иван."
    assert say(brain, "Называй меня Сеня.") == "Запомнил: тебя зовут Сеня."
    assert brain.memory.facts() == [(1, "Меня зовут Сеня")]
    assert brain.facts == "Собеседника зовут Сеня."  # the model knows it at once
    assert brain.skills.handle("меня зовут на работу", NOW) is None


def test_done_needs_most_of_the_words_and_can_be_taken_back():
    brain = make(*WEEK, {"id": "p", "kind": "task", "title": "Пробник ЕГЭ по физике", "date": "2026-09-30"})
    assert brain.skills.handle("Пробник по химии сделан.", NOW) is None  # "Пробник … по физике" is not it
    assert say(brain, "Пробник по физике сделан.") == "Отметил: «Пробник ЕГЭ по физике» (среда, 30 сентября) — сделано."
    assert say(brain, "Верни.") == "Снял отметку: «Пробник ЕГЭ по физике» снова не сделано."
    assert not brain.planner.planner.rows["p"]["done"]
    assert brain.skills.handle("Ужин готов.", NOW) is None


def test_thinking_aloud_gets_silence():
    brain = make()
    assert say(brain, "Эээ...") == ""
    assert say(brain, "Хм.") == ""
    assert brain.llm.requests == []


def test_the_models_delete_is_asked_and_confirmed_by_the_program():
    from test_brain import tool
    brain = make(*WEEK, replies=[tool("delete_plan", query="хакатон")])
    assert say(brain, "Слушай, а Хакатон завтра отменили, убери его.") == \
        "Удалить завтра, суббота, 26 сентября — с 10:00 до 13:00 Хакатон?"
    assert say(brain, "Да.") == "Удалил: завтра, суббота, 26 сентября — с 10:00 до 13:00 Хакатон."
    assert len(brain.llm.requests) == 1 and "a" not in brain.planner.planner.rows


def test_the_planners_no_is_said_as_it_is_not_retold():
    from test_brain import tool
    brain = make(*WEEK, replies=[tool("plan_done", query="созвон с Васей")])
    assert say(brain, "Я уже поговорил с Васей по созвону, отметь.") == \
        "Не нашёл в планах ничего похожего на «созвон с Васей»."
    assert len(brain.llm.requests) == 1


def test_what_the_program_forgets_the_model_forgets_too():
    brain = make(replies=[text("Вы любите чай."), text("Вы любите чай.")])
    say(brain, "Запомни, что я люблю кофе.")
    say(brain, "Запомни, что я живу в Казани.")
    say(brain, "Забудь, что я люблю кофе.")
    say(brain, "Расскажи мне что-нибудь интересное.")
    assert "кофе" not in brain.llm.requests[0][1]["content"]  # the facts message is the memory's again
    # "что ты обо мне знаешь?": the program says the facts themselves (the model dropped half, and took 20-30 s)
    assert say(brain, "Что ты обо мне знаешь?") == "Я помню: ты живёшь в Казани."
    brain = make()
    assert say(brain, "Что ты обо мне знаешь?") == "Пока я ничего о тебе не знаю. Расскажи — запомню."


def test_leap_years_are_counted_not_guessed():
    brain = make()
    assert say(brain, "Високосный ли сейчас год?") == "2026 год не високосный."
    assert say(brain, "2028 год високосный?") == "2028 год високосный."
    assert say(brain, "Является ли 1900 год високосным?") == "1900 год не високосный."
    assert say(brain, "Когда следующий високосный год?") == "Следующий високосный год — 2028."


def test_the_model_reads_no_plans():
    brain = make(*WEEK, replies=[text("Смотря сколько у тебя дел.")])
    say(brain, "Успею ли я завтра между делами сходить в зал?")
    assert "Занятие" not in str(brain.llm.requests[0])


def test_bring_back_the_one_named():
    brain = make(*WEEK)
    say(brain, "Удали физику.")
    say(brain, "Да.")
    say(brain, "Удали Хакатон.")
    say(brain, "Да.")
    assert say(brain, "Верни физику.") == "Вернул: сегодня, пятница, 25 сентября — с 16:00 до 17:30 Физика."
    assert "a" not in brain.planner.planner.rows  # Хакатон stays deleted
    assert say(brain, "Верни ЕГЭ.") == "Не нашёл среди недавно удалённого ничего похожего на «ЕГЭ»."


def test_am_i_free_looks_at_that_part_of_the_day():
    brain = make(*WEEK)
    assert say(brain, "Я завтра вечером свободен?") == "Нет, завтра вечером: с 22:00 до 23:40 ЕГЭ."
    assert say(brain, "Свободен ли я завтра днём?") == "Нет, завтра днём: с 10:00 до 13:00 Хакатон."
    assert say(brain, "Я послезавтра вечером свободен?") == "Да, послезавтра вечером ничего не запланировано."
    assert say(brain, "Свободен ли я завтра ночью?") == \
        "Да, завтра ночью ничего не запланировано. Из задач на день: «Позвонить в банк»."
    assert say(brain, "Я завтра утром занят?") == "Да, завтра утром: в 10:00 Занятие по русскому языку; с 10:00 до 13:00 Хакатон."
    assert say(brain, "Мы свободны в среду?") == "Да, в среду, 30 сентября ничего не запланировано."


def test_numbers_in_words_move_it_too():
    brain = make(*WEEK)
    say(brain, "Запиши на завтра в 12 созвон с Петей один раз.")
    assert say(brain, "Перенеси его на четырнадцать.") == "Перенёс на завтра, субботу, 26 сентября: в 14:00 Созвон."
    assert say(brain, "Нет, лучше на пятнадцать.") == "Перенёс на завтра, субботу, 26 сентября: в 15:00 Созвон."


def test_mark_what_i_did_in_a_whole_sentence():
    brain = make({"id": "h", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"})
    assert say(brain, "Отметь, что я купил хлеб.") == "Отметил: «Купить хлеб» — сделано."


def test_dates_asked_every_which_way():
    brain = make()
    assert say(brain, "Какой завтра день недели?") == "Завтра будет суббота, 26 сентября."
    assert say(brain, "Какое число будет в понедельник?") == "В понедельник — 28 сентября."
    assert say(brain, "А в следующий?") == "5 октября — понедельник."
    assert say(brain, "Сколько недель до Нового года?") == "До Нового года 98 дней — ровно 14 недель, это будет пятница."
    assert say(brain, "Сколько недель до 8 марта?") == "До 8 марта 164 дня — 23 недели и 3 дня, это будет понедельник."
    assert say(brain, "What time is it?") == "Сейчас 14:03."


def test_the_birthday_told_is_counted_to():
    brain = make()
    assert brain.skills.handle("Сколько дней до моего дня рождения?", NOW) is None  # not told yet: the model's
    brain.memory.remember("Мой день рождения 15 марта")
    assert say(brain, "Сколько дней до моего дня рождения?") == "До твоего дня рождения 171 день, это будет понедельник."


def test_notes_are_added_to_counted_and_deleted_all_at_once_after_a_yes():
    brain = make()
    say(brain, "Сделай заметку про ремонт: купить краску.")
    say(brain, "Запиши заметку: позвонить в страховую.")
    assert say(brain, "Добавь в заметку про ремонт ещё кисти.") == "Дописал в заметку «Ремонт»: кисти."
    assert [n["body"] for n in brain.planner.planner.notes() if n["title"] == "Ремонт"] == ["Купить краску, кисти"]
    assert say(brain, "Верни.") == "Вернул заметку «Ремонт» как было."
    assert say(brain, "Сколько у меня заметок?") == "Всего 2 заметки: Ремонт; Позвонить в страховую."
    assert say(brain, "Удали все заметки.") == "Удалить все заметки — 2 штуки: Ремонт; Позвонить в страховую?"
    assert say(brain, "Нет.") == "Хорошо, не удаляю."
    say(brain, "Удали все заметки.")
    assert say(brain, "Да.") == "Удалил 2 заметки."
    assert brain.planner.planner.notes() == []
    assert say(brain, "Верни.") == "Вернул 2 заметки."
    assert len(brain.planner.planner.notes()) == 2


def test_a_birthday_is_kept_and_everything_forgotten_after_a_yes():
    brain = make()
    assert say(brain, "Мой день рождения 15 марта.") == "Запомнил: твой день рождения 15 марта."
    say(brain, "Запомни, что у меня аллергия на орехи.")
    assert say(brain, "Сколько дней до моего дня рождения?") == "До твоего дня рождения 171 день, это будет понедельник."
    assert say(brain, "Забудь всё обо мне.") == "Забыть всё, что я о тебе помню — 2 факта?"
    assert say(brain, "Нет.") == "Хорошо, всё помню как было."
    say(brain, "Забудь всё обо мне.")
    assert say(brain, "Да.") == "Забыл всё, что помнил о тебе."
    assert brain.memory.facts() == []
    assert say(brain, "Верни.") == "Вернул в память всё, что забыл."
    assert len(brain.memory.facts()) == 2


def test_moves_by_an_hour_renames_marks_all_and_the_last_added():
    brain = make(*WEEK)
    say(brain, "Запиши на завтра в 12 созвон с Петей один раз.")
    assert say(brain, "Перенеси созвон на час позже.") == "Перенёс на завтра, субботу, 26 сентября: в 13:00 Созвон."
    assert say(brain, "Сдвинь на полчаса раньше.") == "Перенёс на завтра, субботу, 26 сентября: в 12:30 Созвон."
    assert say(brain, "Измени время физики на 18.") == "Перенёс на сегодня, пятницу, 25 сентября: с 18:00 до 19:30 Физика."
    assert say(brain, "Переименуй созвон в звонок Пете.") == "Переименовал: теперь это «Звонок Пете»."
    assert say(brain, "Верни.") == "Вернул название «Созвон»."
    assert say(brain, "Удали последнее добавленное.") == "Удалить завтра, суббота, 26 сентября — в 12:30 Созвон?"
    assert say(brain, "Нет.") == "Хорошо, не удаляю."
    assert say(brain, "Отметь все задачи на завтра выполненными.") == "Отметил сделанными: «Позвонить в банк»."
    assert brain.planner.planner.rows["b"]["done"]
    assert say(brain, "Верни.") == "Снял отметки: 1."
    assert not brain.planner.planner.rows["b"]["done"]


def test_repeats_by_weeks_and_months_on_any_days():
    brain = make()
    saved, quick = [], []
    fake = brain.planner.planner
    orig_save, orig_quick = fake.save, fake.quick
    fake.save = lambda item: (saved.append(item), orig_save(item))[1]
    fake.quick = lambda text, default_day=None: (quick.append(text), orig_quick(text, default_day))[1]
    assert say(brain, "Запиши тренировку по средам в 19.") == "Сколько раз?"
    say(brain, "26 недель")
    assert quick[-1] == "в среду тренировку в 19" and saved[-1]["repeat"] == {"unit": "week", "count": 26}
    say(brain, "Напомни оплатить интернет каждый месяц 5 числа.")
    assert saved[-1]["repeat"] == {"unit": "month", "count": 12}
    quick.clear(), saved.clear()
    reply = say(brain, "Запиши тренировку по понедельникам, средам и пятницам в 6 утра с повтором 10 недель.")
    assert quick == ["в понедельник тренировку в 06:00", "в среду тренировку в 06:00", "в пятницу тренировку в 06:00"]
    assert [i["repeat"] for i in saved if i.get("repeat")] == [{"unit": "week", "count": 10}] * 3
    assert reply.startswith("Записал «Созвон»") and "по понедельникам, средам и пятницам, 10 недель" in reply, reply
    assert say(brain, "Верни.").startswith("Убрал")
    quick.clear(), saved.clear()
    assert "каждый день, 30 раз" in say(brain, "Запиши выпить витамины каждый день в 8 утра 30 дней.")
    assert saved[-1]["repeat"] == {"unit": "day", "count": 30}
    quick.clear(), saved.clear()
    say(brain, "Запиши зарядку по будням в 7 на 3 месяца.")
    assert len(quick) == 5 and saved[-1]["repeat"] == {"unit": "week", "count": 12}
    quick.clear()
    say(brain, "Запиши бассейн каждый вторник и четверг в 20 10 раз.")
    assert quick == ["во вторник бассейн в 20", "в четверг бассейн в 20"]
    quick.clear(), saved.clear()
    say(brain, "Запиши тренировку в понедельник и среду в 6 утра 10 недель.")
    assert [i["repeat"] for i in saved if i.get("repeat")] == [{"unit": "week", "count": 10}] * 2
    quick.clear(), saved.clear()
    say(brain, "Запиши платёж 5 октября в 10 3 месяца.")
    assert saved[-1]["repeat"] == {"unit": "month", "count": 3}


def test_several_days_once_each():
    brain = make()
    saved, quick = [], []
    fake = brain.planner.planner
    orig_save, orig_quick = fake.save, fake.quick
    fake.save = lambda item: (saved.append(item), orig_save(item))[1]
    fake.quick = lambda text, default_day=None: (quick.append(text), orig_quick(text, default_day))[1]
    reply = say(brain, "Запиши тренировку в понедельник, в среду и в пятницу в 6 утра, один раз.")
    assert quick == ["в понедельник тренировку в 06:00", "в среду тренировку в 06:00", "в пятницу тренировку в 06:00"]
    assert not any(i.get("repeat") for i in saved)  # no "каждую неделю": once each
    assert reply.startswith("Записал в ")
    quick.clear()
    say(brain, "Запиши встречу в понедельник в 10 один раз.")
    assert quick == ["встречу в понедельник в 10:00"]  # one day: as before


def test_titles_lose_what_is_said_around_them():
    brain = make()
    say(brain, "Запиши reminder на завтра купить хлеб без времени один раз.")
    assert brain.planner.planner.quick_text == "завтра купить хлеб"
    from orpheus.planner import tidy_title
    assert tidy_title("Встреча с Машей в в кафе") == "Встреча с Машей в кафе"


def test_a_move_replaces_where_one_lives():
    brain = make()
    say(brain, "Запомни, что я живу в Москве.")
    assert say(brain, "Я переехал в Санкт-Петербург.") == "Запомнил: ты переехал в Санкт-Петербург."
    assert brain.memory.facts() == [(1, "Я переехал в Санкт-Петербург")]
    assert say(brain, "Кстати, я теперь живу в Казани, запомни это.") == "Запомнил: ты живёшь в Казани."
    assert brain.memory.facts() == [(1, "Я живу в Казани")]


def test_how_many_days_and_the_weekday_are_one_answer():
    brain = make()
    assert say(brain, "Сколько дней до Нового года и какой это будет день недели?") == "До Нового года 98 дней, это будет пятница."
    assert brain.llm.requests == []


def test_write_it_down_said_last_is_a_note():
    brain = make()
    assert say(brain, "Мне бы не забыть купить батарейки для пульта, запиши куда-нибудь.") == \
        "Записал в заметки: Купить батарейки для пульта."


def test_if_the_day_is_empty_the_plan_goes_in_else_it_is_asked_first():
    brain = make(*WEEK)
    fake = brain.skills.planner.planner
    assert say(brain, "Посмотри, что у меня в четверг, и если там пусто — добавь тренировку в 18.").startswith(
        "В четверг, 1 октября пусто. Добавил на ")
    assert "четверг" in fake.quick_text and "тренировку" in fake.quick_text and brain.llm.requests == []
    assert say(brain, "Посмотри, что у меня завтра, и если там пусто, добавь тренировку в 18.") == (
        "Завтра: в 10:00 Занятие по русскому языку; с 10:00 до 13:00 Хакатон; в 12:00 Созвон; с 22:00 до 23:40 ЕГЭ; "
        "задача «Позвонить в банк». Добавить всё равно?")  # (the fake Planner put its "Созвон" there)
    assert say(brain, "Нет.") == "Хорошо, не добавляю."
    fake.quick_text = ""
    assert say(brain, "Если я свободен завтра в 18, запиши кино.").startswith("Завтра в 18:00 свободно. Добавил на ")
    assert fake.quick_text == "завтра в 18 кино"  # the Planner reads "в 18" itself
    assert say(brain, "Если я свободен завтра в 22, запиши кино.") == "Завтра: с 22:00 до 23:40 ЕГЭ. Добавить всё равно?"
    assert say(brain, "Да.").startswith("Добавил на ")


def test_time_left_till_midnight_or_an_hour():
    brain = make()
    assert say(brain, "Сколько времени осталось до полуночи?") == "До полуночи осталось 9 часов 57 минут."
    assert say(brain, "Сколько осталось до 18:00?") == "До 18:00 осталось 3 часа 57 минут."
    assert say(brain, "Сколько часов до 6 вечера?") == "До 18:00 осталось 3 часа 57 минут."
    assert say(brain, "Сколько ещё осталось до 15?") == "До 15:00 осталось 57 минут."
    assert say(brain, "Сколько осталось до Нового года?").startswith("До Нового года 98 дней")
    assert brain.llm.requests == []


def test_two_answers_to_one_phrase_do_not_say_the_same_twice():
    brain = make(*WEEK)
    assert say(brain, "Какое сегодня число и что у меня по плану?") == \
        "Сегодня пятница, 25 сентября. По плану: с 16:00 до 17:30 Физика."
    reply = say(brain, "Запиши на завтра созвон в 12 и тренировку в 18, один раз.")
    assert reply.count("Записал") == 1 and reply.count("Созвон") == 2, reply  # the fake Planner makes a "Созвон" of both
    assert brain.llm.requests == []


def test_when_it_ends_and_the_next_one_are_found_in_the_plans():
    brain = make(*WEEK, {"id": "t0", "kind": "event", "title": "Тренировка", "date": "2026-09-21", "start_time": "19:00"},
                 {"id": "t2", "kind": "event", "title": "Тренировка", "date": "2026-10-05", "start_time": "19:00"})
    assert say(brain, "Во сколько заканчивается Хакатон?") == "«Хакатон» заканчивается завтра, суббота, 26 сентября в 13:00."
    assert say(brain, "Когда у меня следующая тренировка?") == "Понедельник, 28 сентября — с 19:00 до 20:30 Тренировка."
    assert brain.llm.requests == []


def test_days_of_a_week_each_start_a_sentence():
    brain = make(*WEEK)
    reply = say(brain, "Какие планы на неделю?")
    assert ". Завтра, суббота, 26 сентября: в 10:00" in reply and ". Понедельник, 28 сентября: " in reply


def test_how_long_a_planned_thing_lasts_is_the_program_s():
    brain = make(*WEEK)
    assert say(brain, "Сколько длится ЕГЭ?") == "ЕГЭ — 1 час 40 минут: завтра, суббота, 26 сентября — с 22:00 до 23:40 ЕГЭ."
    assert say(brain, "Сколько идёт Хакатон?") == "Хакатон — 3 часа: завтра, суббота, 26 сентября — с 10:00 до 13:00 Хакатон."
    assert brain.llm.requests == []


def test_the_busiest_and_the_freest_day_are_counted():
    brain = make(*WEEK)
    assert say(brain, "Какой день на этой неделе у меня самый загруженный?") == (
        "Самый загруженный день — завтра, суббота, 26 сентября, 4 дела: в 10:00 Занятие по русскому языку; "
        "с 10:00 до 13:00 Хакатон; с 22:00 до 23:40 ЕГЭ; задача «Позвонить в банк».")
    assert say(brain, "А какой самый свободный?") == "Самый свободный день — послезавтра, воскресенье, 27 сентября: ничего не запланировано."
    assert brain.llm.requests == []


def test_a_plan_told_is_not_added_without_the_command():
    brain = make(*WEEK, replies=[text("Скажи «Запиши», и запишу."), text("Поздравляю!"), text("Понятно."), text("Была.")])
    fake = brain.skills.planner.planner
    fake.quick_text = ""
    say(brain, "В понедельник в 16:40 занятие Петров по физике.")  # plans are written by "Запиши …" only
    fake = brain.skills.planner.planner
    fake.quick_text = ""
    say(brain, "У меня завтра день рождения.")  # no time: not a plan
    say(brain, "Вчера в 10 была встреча.")  # the past
    say(brain, "Что у меня в понедельник в 16:40?")
    assert fake.quick_text == ""


def test_what_is_left_for_today_is_by_the_clock():
    from datetime import datetime as dt
    items = WEEK + [{"id": "m", "kind": "event", "title": "Обед", "date": "2026-09-25", "start_time": "13:00"},
                    {"id": "h", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"}]
    brain = make(*items)
    assert say(brain, "Что осталось на сегодня?") == \
        "Сейчас 14:03. Дальше: с 16:00 до 17:30 Физика. Задачи на сегодня: «Купить хлеб»."
    brain.skills.planner.now = lambda: dt(2026, 9, 25, 16, 45)
    assert say(brain, "Что у меня ещё сегодня?") == \
        "Сейчас 16:45. Идёт: с 16:00 до 17:30 Физика. Задачи на сегодня: «Купить хлеб»."
    brain = make(*WEEK)
    brain.skills.planner.now = lambda: dt(2026, 9, 25, 20, 0)
    assert say(brain, "Какие дела остались на сегодня?") == \
        "Сейчас 20:00. На сегодня больше ничего. Завтра первым: в 10:00 Занятие по русскому языку."
    assert brain.llm.requests == []


def test_what_else_tomorrow_is_the_plans():
    brain = make(*WEEK)
    assert say(brain, "Что у меня ещё завтра?").startswith("Завтра, суббота, 26 сентября: в 10:00")
    assert brain.llm.requests == []


def test_the_first_greeting_of_the_day_brings_its_summary_by_name():
    from datetime import datetime as dt
    items = WEEK + [{"id": "m", "kind": "event", "title": "Обед", "date": "2026-09-25", "start_time": "13:00"},
                    {"id": "h", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"}]
    brain = make(*items)
    say(brain, "Меня зовут Иван.")
    assert say(brain, "Доброе утро!") == ("Добрый день, Иван! Сегодня пятница, 25 сентября. "
                                          "По плану: с 16:00 до 17:30 Физика; задача «Купить хлеб».")  # 14:03: lunch is over
    assert say(brain, "Привет.").startswith("Добрый день, Иван. ")  # the second: just hello
    tomorrow = dt(2026, 9, 26, 8, 30)
    brain.skills.planner.now = lambda: tomorrow
    assert "".join(brain.ask("Доброе утро", now=tomorrow)).startswith(
        "Доброе утро, Иван! Сегодня суббота, 26 сентября. По плану: в 10:00 Занятие по русскому языку;")
    assert brain.llm.requests == []


def test_an_item_there_repeats_and_its_repeat_is_changed_whole():
    # "сделай повтор тренировки 10 недель" went to the model, which has no tool for it
    brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-26", "start_time": "07:00"})
    rows = brain.planner.planner.rows
    assert say(brain, "Сделай повтор тренировки 10 недель.") == "Теперь «Тренировка» повторяется каждую неделю, 10 раз, с 26 сентября."
    assert len(rows) == 10 and len({r["series"] for r in rows.values()}) == 1
    assert say(brain, "Перенеси тренировку на 8 утра.") == "Перенёс на 08:00 все повторы «Тренировка»."
    assert {r["start_time"] for r in rows.values()} == {"08:00"}
    assert say(brain, "Переименуй тренировку в утреннюю пробежку.") == "Переименовал все повторы: теперь это «Утренняя пробежка»."
    assert {r["title"] for r in rows.values()} == {"Утренняя пробежка"}
    say(brain, "Сделай пробежку с 7 до 8.")
    assert {(r["start_time"], r["end_time"]) for r in rows.values()} == {("07:00", "08:00")}
    assert say(brain, "Больше не повторяй пробежку.").startswith("Повтор убрал, остался ближайший раз: завтра")
    assert len(rows) == 1 and not list(rows.values())[0].get("series")


def test_a_repeat_by_days_is_changed_on_all_its_days():
    brain = make()
    fake = brain.planner.planner
    for day in ("2026-09-28", "2026-09-30", "2026-10-02"):  # "зарядка по понедельникам, средам и пятницам"
        fake.save({"kind": "event", "title": "Зарядка", "date": day, "start_time": "06:00", "repeat": {"unit": "week", "count": 26}})
    assert say(brain, "Сделай повтор зарядки 4 недели.") == \
        "Теперь «Зарядка» повторяется по понедельникам, средам и пятницам, 4 недели, с 28 сентября."
    assert len(fake.rows) == 12
    assert say(brain, "Удали все зарядки.") == "Удалить все повторы «Зарядка» с 28 сентября — 12 раз?"
    say(brain, "Да.")
    assert not fake.rows
    assert say(brain, "Верни.") == "Вернул: «Зарядка», 12 раз."


def test_change_to_a_day_moves_to_a_name_renames():
    brain = make({"id": "b", "kind": "event", "title": "Бассейн", "date": "2026-09-26", "start_time": "20:00"})
    assert say(brain, "Поменяй бассейн на плавание.") == "Переименовал: теперь это «Плавание»."
    assert say(brain, "Поменяй плавание на воскресенье.") == "Перенёс на послезавтра, воскресенье, 27 сентября: в 20:00 Плавание."
    assert say(brain, "Переименуй плавание во встречу с Петей.") == "Переименовал: теперь это «Встреча с Петей»."


def test_time_spans():
    from orpheus.planner import tidy_title, time_span
    assert time_span("с 7 до 8") == ("07:00", "08:00")
    assert time_span("с 3 до 5") == ("15:00", "17:00")
    assert time_span("с 7 до 9 вечера") == ("19:00", "21:00")
    assert time_span("с 11 до 1") == ("11:00", "13:00")
    assert tidy_title("у меня стоматолог") == "Стоматолог"


def test_ways_to_ask_for_a_repeat():
    for phrase, reply in [
        ("Сделай тренировку каждую неделю.", "каждую неделю, 26 раз"),
        ("Сделай тренировку раз в месяц.", "каждый месяц, 12 раз"),
        ("Тренировку повторяй каждую неделю.", "каждую неделю, 26 раз"),
        ("Повтори тренировку 10 недель.", "каждую неделю, 10 раз"),
        ("Сделай его повторяющимся 10 недель.", "каждую неделю, 10 раз"),
        ("Пусть тренировка повторяется 10 недель.", "каждую неделю, 10 раз"),
    ]:
        brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-26", "start_time": "07:00"})
        brain.planner.focus = [brain.planner.planner.rows["t"]]
        assert say(brain, phrase) == "Теперь «Тренировка» повторяется %s, с 26 сентября." % reply, phrase
    brain = make()
    assert brain.skills and say(brain, "Повтори.") == "Я пока ничего не говорил."  # not a repeat of a plan


# a code review: each case it found

def test_review_repeat_of_a_list_asks_which_and_undoes():
    brain = make({"id": "a", "kind": "event", "title": "Тренировка", "date": "2026-09-26", "start_time": "07:00"},
                 {"id": "b", "kind": "event", "title": "Встреча", "date": "2026-09-26", "start_time": "12:00"},
                 replies=[text("Хорошо.")])
    say(brain, "Что у меня завтра?")
    assert say(brain, "Сделай его повторяющимся каждую неделю.").startswith("Какое именно:")
    assert say(brain, "Повтори два раза.") != ""  # "say it twice", not a repeat of every item listed
    assert len(brain.planner.planner.rows) == 2
    say(brain, "Сделай повтор тренировки 10 недель.")
    assert len(brain.planner.planner.rows) == 11
    assert say(brain, "Верни.") == "Вернул повтор как было: «Тренировка»."
    rows = brain.planner.planner.rows
    assert len(rows) == 2 and not any(r.get("series") for r in rows.values())


def test_review_change_to_a_bare_hour_or_a_part_of_day_moves():
    for to, start in (("15", "15:00"), ("семь", "07:00"), ("вечер", "19:00"), ("утро", "09:00")):
        brain = make({"id": "m", "kind": "event", "title": "Встреча", "date": "2026-09-26", "start_time": "12:00"})
        say(brain, "Поменяй встречу на %s." % to)
        row = brain.planner.planner.rows["m"]
        assert (row["title"], row["start_time"]) == ("Встреча", start), to


def test_review_a_span_with_a_day_moves_that_day():
    brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-25", "start_time": "18:00"})
    say(brain, "Перенеси тренировку на завтра с 7 до 8.")
    row = brain.planner.planner.rows["t"]
    assert (row["date"], row["start_time"], row["end_time"]) == ("2026-09-26", "07:00", "08:00")


def test_review_spans_by_part_of_day():
    from orpheus.planner import time_span
    assert time_span("с 10 до 12 дня") == ("10:00", "12:00")
    assert time_span("с 11 до 1 дня") == ("11:00", "13:00")
    assert time_span("с 12 до 2 ночи") == ("00:00", "02:00")
    assert time_span("с 5 до 13") == ("05:00", "13:00")
    assert time_span("с 2 до 4 дня") == ("14:00", "16:00")


def test_review_srednij_is_not_wednesday():
    from orpheus.skills import repeat_days, several_days
    assert several_days("встреча в пятницу в среднем зале")[0] is None
    assert repeat_days("йога по пятницам в среднем зале")[0] == [4]
    assert repeat_days("по понедельникам средам и пятницам")[0] == [0, 2, 4]


def test_review_one_named_day_of_a_repeat_moves_alone_and_a_whole_move_undoes():
    brain = make()
    fake = brain.planner.planner
    fake.save({"kind": "event", "title": "Зарядка", "date": "2026-09-26", "start_time": "06:00", "repeat": {"unit": "week", "count": 5}})
    say(brain, "Перенеси завтрашнюю зарядку на 8 утра.")
    times = sorted((r["date"], r["start_time"]) for r in fake.rows.values())
    assert times[0] == ("2026-09-26", "08:00") and {t for _, t in times[1:]} == {"06:00"}
    say(brain, "Перенеси зарядку на 7 утра.")
    assert {r["start_time"] for r in fake.rows.values()} == {"07:00"}
    assert brain.planner.focus[0]["start_time"] == "07:00"  # not the old time: the next change starts from here
    assert say(brain, "Верни.") == "Вернул время «Зарядка»."
    assert sorted(r["start_time"] for r in fake.rows.values()) == ["06:00"] * 4 + ["08:00"]


def test_review_stop_repeat_keeps_the_next_time_not_a_past_one():
    brain = make()
    fake = brain.planner.planner
    fake.save({"kind": "event", "title": "Бассейн", "date": "2026-09-24", "start_time": "20:00", "repeat": {"unit": "week", "count": 5}})
    say(brain, "Убери повтор у бассейна.")
    # yesterday's stays as it was (the past), the one kept is the next, not yesterday's
    assert sorted((r["date"], bool(r.get("series"))) for r in fake.rows.values()) == [("2026-09-24", True), ("2026-10-01", False)]
    assert say(brain, "Верни.") == "Вернул повтор «Бассейн»."
    assert len(fake.rows) == 5


def test_review_loose_phrases_are_not_plans():
    brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-26", "start_time": "07:00"},
                 replies=[text("Хорошо."), text("Хорошо.")])
    for phrase in ("Повторяй за мной.", "Сделай перевод с английского."):
        say(brain, phrase)
        assert brain.handled == "модель", phrase
    assert len(brain.planner.planner.rows) == 1 and not brain.planner.planner.deleted


def test_a_morning_hour_for_another_day_stays_morning():
    # at 14:03 "на 9" for tomorrow's training is 09:00; for today's item it would be 21:00
    brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-26", "start_time": "07:00"})
    say(brain, "Перенеси тренировку на 9.")
    assert brain.planner.planner.rows["t"]["start_time"] == "09:00"


def test_the_training_as_it_was_asked_on_28_09():
    # the server's journal, 12:59-13:01: one item "Тренировка среду и пятницу", the model refusing, a task «Тогда»
    brain = make()
    fake = brain.planner.planner
    fake.quick = lambda text, default_day=None: fake.save(  # plannerd's parse, enough for these three
        {"kind": "event", "title": "Тренировка", "start_time": "06:00",
         "date": {"в понедельник": "2026-09-28", "в среду": "2026-09-30", "в пятницу": "2026-10-02"}[text.split(" тренировку")[0]]})
    assert say(brain, "Запиши тренировку в понедельник, среду и пятницу на 6:00 утра, один раз.").startswith("Записал в понедельник, 28 сентября; в среду")
    assert len(fake.rows) == 3
    assert say(brain, "Сделай для этих тренировок повторение 50 недель.") == \
        "Теперь «Тренировка» повторяется по понедельникам, средам и пятницам, 50 недель, с 28 сентября."
    assert len(fake.rows) == 150
    assert say(brain, "Поставь тогда повтор 10 недель.").startswith("Теперь «Тренировка» повторяется по понедельникам, средам и пятницам, 10 недель")
    assert len(fake.rows) == 30 and not any(r["title"] == "Тогда" for r in fake.rows.values())


# speech as it is said (scripts/bench_speech.py found each of these)

def test_speech_as_said():
    brain = make({"id": "f", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "18:00"},
                 {"id": "k", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-26"})
    routed = {
        "Напомни какое сегодня число": "date",
        "Че у меня на завтра?": "plan_ask",
        "Помолчи.": "stop", "Отбой.": "stop", "Всё, свободен.": "stop",
        "Засеки 5 минут.": "timer",
        "Пока-пока.": "bye",
        "Ты кто вообще?": "who",
        "Время подскажи.": "time",
        "17 на 23 сколько будет?": "calc",
    }
    for phrase, intent in routed.items():
        brain.skills.handle(phrase, NOW)
        assert brain.skills.handled == intent, "%s: %s" % (phrase, brain.skills.handled)
    for phrase in ["Напомни мне, что я говорил про Виктора.", "Добавь сахар в чай.", "Поставь чайник.", "Добавь громкости.",
                   "Я завтра в 10 не смогу прийти.", "Он сказал, что перенесёт встречу на завтра.", "Отмени, я передумал."]:
        result = brain.skills.handle(phrase, NOW)
        assert brain.skills.handled not in ("plan_add", "plan_delete", "plan_move"), "%s: %s" % (phrase, brain.skills.handled)
    assert len(brain.planner.planner.rows) == 2  # nothing added or removed


def test_plans_as_said():
    brain = make({"id": "f", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "18:00"},
                 {"id": "k", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-26"})
    fake = brain.planner.planner
    quick = []
    orig = fake.quick
    fake.quick = lambda text, default_day=None: (quick.append(text), orig(text, default_day))[1]
    assert say(brain, "Можешь добавить встречу с Олегом завтра в 12?").startswith("Чтобы записать в планы, скажи: «Запиши»")
    assert quick == []
    assert say(brain, "Запиши меня к парикмахеру на субботу на 12.") == "Сколько раз?"
    say(brain, "Один раз.")
    assert quick[-1].endswith("в 12:00")
    say(brain, "Запиши на завтра в семь тридцать утра зарядку один раз.")
    assert "07:30" in quick[-1]
    assert say(brain, "Физику на завтра перекинь.").startswith("Перенёс на завтра")
    assert fake.rows["f"]["date"] == "2026-09-26"
    say(brain, "Кр по алгебре написал, отметь.")
    assert fake.rows["k"]["done"]


def test_no_the_other_day_offers_it():
    brain = make({"id": "a", "kind": "event", "title": "Русский", "date": "2026-09-25", "start_time": "18:00"},
                 {"id": "b", "kind": "event", "title": "Русский", "date": "2026-09-26", "start_time": "10:00"})
    assert say(brain, "Удали русский.").startswith("Удалить сегодня")
    assert say(brain, "Нет, завтрашний.").startswith("Удалить завтра")  # not "Хорошо, не удаляю"
    say(brain, "Да.")
    assert list(brain.planner.planner.rows) == ["a"]


def test_a_plan_told_right_after_adding_is_not_a_fix():
    brain = make(replies=[text("Хорошего концерта.")])
    fake = brain.planner.planner
    say(brain, "Запиши на завтра в 17 репетитора один раз.")
    quick = []
    orig = fake.quick
    fake.quick = lambda text, default_day=None: (quick.append(text), orig(text, default_day))[1]
    before = dict(fake.rows)
    say(brain, "В понедельник в 8 вечера иду на концерт.")
    assert brain.skills.handled != "plan_move" and fake.rows == before  # not "move the last one to Monday"


def test_spoken_times_and_titles():
    from orpheus.planner import clean_quick, tidy_title
    assert clean_quick("на завтра в семь тридцать утра зарядку", NOW) == "на завтра в 07:30 зарядку"
    assert clean_quick("на послезавтра в четверть девятого созвон", NOW) == "на послезавтра в 8:15 созвон"
    assert clean_quick("на пятницу без пятнадцати десять стрижку", NOW) == "на пятницу в 9:45 стрижку"
    assert clean_quick("к парикмахеру на субботу на 12", NOW) == "к парикмахеру на субботу в 12:00"
    assert clean_quick("на 2 недели в отпуск", NOW) == "на 2 недели в отпуск"
    assert tidy_title("у меня в репетитор") == "Репетитор"
    assert tidy_title("иду на концерт") == "Концерт"


def test_conversation_as_it_goes():
    # bench_speech «разговор»: after «что у меня завтра?», «перенеси русский на 11» is tomorrow's
    brain = make({"id": "a", "kind": "event", "title": "Русский", "date": "2026-09-25", "start_time": "18:00"},
                 {"id": "b", "kind": "event", "title": "Русский", "date": "2026-09-26", "start_time": "10:00"},
                 {"id": "r", "kind": "event", "title": "Хакатон", "date": "2026-09-26", "start_time": "10:00"})
    rows = brain.planner.planner.rows
    say(brain, "Что у меня завтра?")
    say(brain, "Перенеси русский на 11.")
    assert (rows["b"]["start_time"], rows["a"]["start_time"]) == ("11:00", "18:00")
    say(brain, "А Хакатон тогда на 14.")
    assert rows["r"]["start_time"] == "14:00"


def test_an_event_without_a_day_is_asked_for():
    brain = make()
    assert say(brain, "Запиши встречу.") == "Время?"
    quick = []
    orig = brain.planner.planner.quick
    brain.planner.planner.quick = lambda text, default_day=None: (quick.append(text), orig(text, default_day))[1]
    assert say(brain, "Завтра в 12.") == "Сколько раз?"
    say(brain, "один раз")
    assert [q.lower() for q in quick] == ["встречу завтра в 12"]
    assert say(brain, "Напомни купить хлеб.").startswith("Добавил")  # a thing to do: no question


def test_several_items_in_one_phrase():
    brain = make()
    quick = []
    orig = brain.planner.planner.quick
    brain.planner.planner.quick = lambda text, default_day=None: (quick.append(text), orig(text, default_day))[1]
    say(brain, "Запиши на завтра: в 9 пробежка, в 13 обед с Никитой и в 20 кино. Один раз.")
    assert [q.split(" в ")[0] for q in quick] == ["завтра"] * 3 and len(quick) == 3


def test_dates_and_corrections_in_passing():
    from orpheus.planner import clean_quick
    assert clean_quick("на завтра в 10, нет, в 11 встречу", NOW) == "на завтра в 11:00 встречу"
    assert clean_quick("на 15-е в 10 утра нотариус", NOW) == "15 октября в 10:00 нотариус"  # the 15th has passed in September
    assert clean_quick("через 3 дня в 18 бассейн", NOW) == "28 сентября в 18 бассейн"
    assert clean_quick("через неделю в это же время созвон", NOW) == "через неделю в 14:03 созвон"


def test_words_match_at_their_start_and_claims_in_any_person():
    from orpheus.brain import claims_done
    brain = make()
    fake = brain.planner.planner
    fake.save({"kind": "note", "title": "Wi-Fi", "body": "пароль от wi-fi: sunflower42"})
    assert brain.planner.notes_recall("Хватит ли мне денег на айфон, если у меня 90 тысяч?") == []
    assert brain.planner.notes_recall("Какой пароль от вайфая?")
    assert claims_done("Вы перенесли занятие по русскому на завтра.") and not claims_done("Вы не перенесли.")


def test_where_one_lives_is_remembered():
    brain = make()
    assert say(brain, "Я живу в Казани.") == "Запомнил: ты живёшь в Казани."
    assert brain.skills.handle("Я живу в своё удовольствие.", NOW) is None or brain.skills.handled != "moved"


def test_missed_today_reads_the_plans():
    brain = make({"id": "f", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "12:00"},
                 replies=[text("Физику в 12.")])
    say(brain, "Я сегодня что-нибудь пропустил?")
    assert brain.skills.handled == "plan_ask" and "Физика" in str(brain.llm.requests[0])  # the model sees the day


def test_a_move_to_where_it_already_is_says_so():
    # the speech bench on a Tuesday: "перенеси её лучше на четверг" of a training on Thursday got "на какой день?"
    brain = make({"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-10-01", "start_time": "19:00", "end_time": "20:30"})
    reply = say(brain, "Перенеси тренировку на четверг.")
    assert reply.startswith("«Тренировка» и так") and "четверг" in reply and "19:00" in reply
    assert say(brain, "Перенеси тренировку на 19.").startswith("«Тренировка» и так")
    assert say(brain, "Перенеси её на пятницу.").startswith("Перенёс на")  # "её": the one just named


def test_remind_what_i_wanted_is_a_question_not_a_task():
    # a talk with the model: "напомни что я хотел на выходных" became a task «Я хотел на выходных»
    brain = make(replies=[text("Ты думал о горах или кино.")])
    assert say(brain, "напомни что я хотел на выходных") == "Ты думал о горах или кино."
    assert brain.handled == "модель"
    assert not [i for i in brain.skills.planner.planner.rows.values() if "хотел" in i["title"].lower()]


def test_what_a_talk_with_him_showed_the_second_time():
    # a talk by an agent: "а её на час позже", "не верни как было" went to the model, which said "перенесу на 20:00",
    # "удалена и добавлена", "восстанавливаю" and did none of it (the fake planner answers any add with 12:00 «Созвон»)
    brain = make()
    say(brain, "слушай запиши тренировку завтра в 7 вечера один раз")
    assert say(brain, "а её на час позже").startswith("Перенёс на") and "13:00" in brain.last_reply
    assert say(brain, "не верни как было").startswith("Вернул как было") and "12:00" in brain.last_reply
    assert brain.skills.planner.planner.rows["new"]["start_time"] == "12:00"
    say(brain, "напомни завтра утром сдать долг по физике")
    assert "в 9:00" in brain.skills.planner.planner.quick_text and "утром" not in brain.skills.planner.planner.quick_text.lower()
    brain = make({"id": "h", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"})
    assert say(brain, "отметь что хлеб купил").startswith("Отметил")
    assert say(brain, "вызови такси") == "Такси вызывать я пока не умею."


def test_forget_what_was_only_said_in_the_talk():
    # a name only said in the talk (never saved), "забудь про Тимура" -> "Такого я не помню", and he named him after
    brain = make(replies=[text("Понял, Тимур."), text("Не знаю."), text("Не знаю.")])
    say(brain, "я сегодня видел Тимура в парке")
    assert say(brain, "забудь про Тимура") == "Хорошо, забыл."
    say(brain, "как зовут моего друга")
    assert not any("Тимур" in str(m.get("content")) for m in brain.llm.requests[-1] if m["role"] != "system")


def test_what_one_tells_about_oneself_is_remembered_by_the_program():
    # told "моего друга зовут Тимур", the model said "запомнил" and saved nothing
    brain = make(replies=[text("И я тебя.")])
    assert say(brain, "моего друга зовут Тимур") == "Запомнил."
    say(brain, "моя любимая еда это пельмени")
    say(brain, "я не ем острое")
    say(brain, "моего друга зовут Тима")  # a newer word for the same: replaces
    facts = [t for _, t in brain.memory.facts()]
    assert facts == ["Моего друга зовут Тима", "Моя любимая еда это пельмени", "Я не ем острое"]
    assert say(brain, "я люблю тебя") == "И я тебя."  # not a fact to keep
    assert len(brain.memory.facts()) == 3


def test_days_until_a_plan_by_its_name():
    # "сколько дней до кр по алгебре": the model counted from the wrong day and took the mock exam for the ЕГЭ
    brain = make(*WEEK, {"id": "k", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-28"})
    assert say(brain, "сколько дней до кр по алгебре") == "До «Кр по алгебре» 3 дня: понедельник, 28 сентября."
    assert say(brain, "через сколько у меня хакатон") == "«Хакатон» уже завтра в 10:00."
    assert say(brain, "сколько дней до нового года").startswith("До Нового года")


def test_corrections_as_they_are_said_move_it_and_memory_is_updated():
    # the third talk: "не в 12 а в 14", "не, в 15", "не, на четверг", "и в 10" went to the model, which said
    # "переношу" each time and moved nothing; "теперь друга зовут Миша" -> "обновляю", the old one kept
    brain = make(replies=[text("м")] * 3)
    say(brain, "запиши на завтра встречу в 12 один раз")
    assert "14:00" in say(brain, "не в 12 а в 14")
    assert "16:00" in say(brain, "блин не в 14 в 16")
    assert "13:00" in say(brain, "нет в час")
    assert "четверг" in say(brain, "не, на четверг")
    assert brain.skills.planner.planner.rows["new"]["date"] == "2026-10-01"
    say(brain, "моего друга зовут Серёжа")
    say(brain, "теперь друга зовут Миша")
    say(brain, "мне 17 лет")
    assert [t for _, t in brain.memory.facts()] == ["Моего друга зовут Миша", "Мне 17 лет"]


def test_plans_said_as_they_are_said_are_added_with_clean_titles():
    # the third talk: each of these went to the model ("Этого я не сделал") or left bits of speech in the title
    from orpheus.planner import tidy_title
    brain = make(*WEEK, replies=[text("Отдохни.")])
    for said, sent in [("запиши на завтра футбол с 16 до 18 один раз", "завтра футбол с 16 до 18"),
                       ("ну запиши задачу сделать доклад по истории на четверг без времени один раз", "сделать доклад по истории на четверг")]:
        assert say(brain, said).startswith("Записал"), said
        assert brain.skills.planner.planner.quick_text == sent, said
    assert say(brain, "мне надо отдохнуть") == "Отдохни."
    assert tidy_title("Ну у меня в пробное собеседование запиши") == "Пробное собеседование"
    assert tidy_title("Слушай в у меня стоматолог") == "Стоматолог"
    assert tidy_title("Следующий про кр") == "Кр"


def test_am_i_free_at_an_hour_and_am_i_busy():
    brain = make(*WEEK)
    assert say(brain, "я свободен завтра в три").startswith("Да, завтра в 15:00 ничего не запланировано")
    assert say(brain, "я свободен завтра в 10").startswith("Нет, завтра в 10:00:")
    assert say(brain, "занят ли я завтра днём").startswith("Да, завтра днём:")


def test_a_long_week_is_summed_up_aloud():
    # "что у меня на этой неделе" read out ~1500 characters
    many = [{"id": "x%d" % k, "kind": "task", "title": "Дело %d" % k, "date": "2026-09-2%d" % (5 + k % 3)} for k in range(14)]
    brain = make(*many)
    reply = say(brain, "что у меня на этой неделе")
    assert reply.startswith("Сегодня: дело 0, дело 3") and "Завтра:" in reply and len(reply) < 250


def test_delete_all_but_one_and_a_pronoun_to_place():
    # "удали всё на сегодня кроме русского" deleted the Russian lesson too; "поставь его на 9 утра" made «Его»
    brain = make(*WEEK)
    assert "кроме «Хакатон»" in say(brain, "удали всё на завтра кроме хакатона")
    say(brain, "да")
    assert [i["title"] for i in brain.skills.planner.planner.rows.values() if i["date"] == "2026-09-26"] == ["Хакатон"]
    assert "по одному шагу" in say(brain, "отмени всё что я сегодня менял")
    say(brain, "что у меня в понедельник")
    assert say(brain, "поставь её на 9 утра").startswith("Перенёс на понедельник")
    assert say(brain, "физику отметь как сделанную").startswith("Отметил")


def test_what_to_remind_is_asked_and_the_answer_added():
    # the fourth talk: "напомни через 20 минут" - "уточни…" - "позвонить бабушке" went to the model: "Напомню тебе
    # позвонить бабушке через 20 минут", with nothing set
    brain = make(*WEEK)
    assert say(brain, "напомни через 20 минут") == "Что напомнить?"
    assert say(brain, "позвонить бабушке").startswith("Добавил")
    assert brain.skills.planner.planner.quick_text == "в 14:23 позвонить бабушке"
    assert say(brain, "напомни") == "Что напомнить?"
    assert say(brain, "напомни через час") == "Что напомнить?"
    assert say(brain, "ладно забей") == "Хорошо."


def test_a_yes_after_one_model_turn_still_confirms():
    # "удали занятие по русскому" - "Удалить …?" - "какое" (the model) - "да": nothing happened
    brain = make(*WEEK, replies=[text("Занятие по русскому завтра в 10:00.")])
    assert say(brain, "удали занятие по русскому завтра").startswith("Удалить")
    say(brain, "какое")
    assert say(brain, "да").startswith("Удалил")


def test_a_plan_moved_by_its_name_and_a_time_without_the_verb():
    # the fourth talk: "а ЕГЭ на 20", "ЕГЭ не в 22 а в 20" -> "ЕГЭ перенесён на 20:00" from the model, nothing moved
    brain = make(*WEEK, replies=[text("Ясно.")] * 3)
    assert "с 20:00 до 21:40 ЕГЭ" in say(brain, "а ЕГЭ на 20")
    assert "с 12:00 до 15:00 Хакатон" in say(brain, "хакатон лучше в 12")
    assert "с 21:00 до 22:40 ЕГЭ" in say(brain, "ЕГЭ не в 20 а в 21")
    assert say(brain, "а физика на завтра?") == "Ясно."
    assert say(brain, "а я на работу") == "Ясно."


def test_time_until_a_plan_and_the_end_of_the_week():
    # the fourth talk: "через сколько часов хакатон" -> the model said 15 hours for 10; "до конца недели" -> "Это сегодня"
    brain = make(*WEEK)
    assert say(brain, "через сколько часов хакатон") == "До «Хакатон» осталось 19 часов 57 минут."
    assert say(brain, "сколько до конца хакатона") == "До конца «Хакатон» осталось 22 часа 57 минут."
    assert say(brain, "сколько времени до ЕГЭ") == "До «ЕГЭ» осталось 1 день 7 часов."
    assert say(brain, "сколько до 18:00") == "До 18:00 осталось 3 часа 57 минут."
    assert say(brain, "сколько осталось до конца недели") == "До конца недели 2 дня: воскресенье, 27 сентября."


def test_a_repeat_without_the_verb_and_the_whole_of_a_repeat_deleted():
    # the fourth talk: "по будням в 8 утра английский" went to the model ("добавлен в расписание", nothing added);
    # "удали всю серию английский" deleted the next one only
    brain = make(*WEEK)
    assert say(brain, "запиши английский по будням в 8 утра 10 недель").startswith("Записал")
    series = [{"id": "e%d" % k, "kind": "event", "title": "Английский", "date": "2026-09-%d" % (28 + k), "start_time": "08:00",
               "series": "s9"} for k in range(3)]
    brain = make(*WEEK, *series)
    assert "Это один из повторов" in say(brain, "удали английский")
    say(brain, "нет")
    assert say(brain, "удали всю серию английский").startswith("Удалить все повторы «Английский»")


def test_what_the_fourth_talk_showed_about_facts():
    # "я играю на гитаре" -> the model: "Добавил в память" (nothing saved); now the program keeps each of these
    brain = make(replies=[text("Ясно.")] * 3)
    for said in ["я играю на гитаре", "я учусь в 11 классе", "у меня собака по кличке Рекс", "мне нравится Тарантино",
                 "мой лучший друг Даня", "я хочу поступить в МФТИ"]:
        assert say(brain, said) == "Запомнил.", said
    assert say(brain, "мой друг уехал") == "Ясно."
    assert len(brain.memory.facts()) == 6


def test_what_one_tells_changes_the_memory():
    # the fifth talk: "теперь мой лучший друг Егор", "я больше не играю на гитаре" -> "Понял…" from the model, the old kept
    brain = make(replies=[text("Ясно.")] * 3)
    for said in ["мой лучший друг Артём", "теперь мой лучший друг Егор", "поправка лучший друг у меня не Егор а Костя",
                 "я играю на гитаре", "я теперь играю на барабанах", "у меня есть кот"]:
        assert say(brain, said) == "Запомнил.", said
    assert [t for _, t in brain.memory.facts()] == ["Мой лучший друг Костя", "Я играю на барабанах", "У меня есть кот"]
    assert say(brain, "я больше не играю на барабанах") == "Понял, убрал из памяти."
    assert say(brain, "мой друг уехал") == "Ясно."
    assert [t for _, t in brain.memory.facts()] == ["Мой лучший друг Костя", "У меня есть кот"]


def test_the_end_of_a_plan_and_an_age_are_the_programs():
    # the fifth talk: "до ЕГЭ сколько" -> "через 2 года и 6 месяцев", "через сколько закончится физика" -> "через 5 дней",
    # "сколько мне лет, если я родился в 2008" -> "16" (all the model's)
    brain = make(*WEEK)
    assert say(brain, "до ЕГЭ сколько") == "До «ЕГЭ» осталось 1 день 7 часов."
    assert say(brain, "через сколько закончится хакатон") == "До конца «Хакатон» осталось 22 часа 57 минут."
    assert say(brain, "когда закончится хакатон") == "«Хакатон» заканчивается завтра, суббота, 26 сентября в 13:00."
    assert say(brain, "сколько мне лет если я родился в 2008") == "Тебе 18, если день рождения в этом году уже был, иначе 17."


def test_lets_add_postpone_by_days_and_titles_from_speech():
    # the fifth talk: "давай добавим контрольную по химии в среду" -> "Этого я не сделал"; "отложи ЕГЭ на неделю" -> the
    # model; titles «Давай запишем химию», «Там купить билеты», «Контрольную»
    from orpheus.planner import tidy_title
    brain = make(*WEEK)
    assert say(brain, "давай добавим контрольную по химии в среду").startswith("Чтобы записать в планы")
    assert say(brain, "отложи ЕГЭ на неделю").startswith("Перенёс на субботу, 3 октября")
    assert say(brain, "отложи хакатон на 2 дня").startswith("Перенёс на понедельник, 28 сентября")
    assert [tidy_title(t) for t in ("Давай запишем химию", "Там купить билеты", "Контрольную")] == [
        "Химия", "Купить билеты", "Контрольная"]


def test_plan_news_is_acted_on_or_asked_about():
    # the sixth talk: "хакатон отменяется", "физика теперь называется мехи", "хакатон переезжает на вечер часов на 7",
    # "его зовут Мурзик" -> "принято", "переименована", "теперь в 19:00", "записал" from the model, nothing done
    brain = make(*WEEK, replies=[text("Ясно.")] * 2)
    assert say(brain, "хакатон отменяется").startswith("Удалить завтра")
    say(brain, "нет")
    assert say(brain, "физика теперь называется мехи") == "Переименовал: теперь это «Мехи»."
    assert "с 19:00 до 22:00 Хакатон" in say(brain, "хакатон переезжает на вечер часов на 7")
    say(brain, "у меня есть кот")
    say(brain, "его зовут Мурзик")
    assert [t for _, t in brain.memory.facts()] == ["У меня есть кот по имени Мурзик"]
    assert say(brain, "мой любимый цвет какой") == "Ясно."


def test_a_plan_named_makes_the_models_claims_checked():
    # the seventh talk: "контрольная теперь во вторник" -> "Контрольная переносится на вторник" (nothing moved)
    brain = make(*WEEK, {"id": "k", "kind": "task", "title": "Контрольная по алгебре", "date": "2026-09-28"},
                 replies=[text("Контрольная переносится на вторник.")])
    assert say(brain, "контрольная теперь во вторник").startswith("Перенёс на вторник")
    assert say(brain, "а контрольная как там вообще").startswith("Этого я сам не делаю")


def test_what_the_seventh_talk_showed():
    brain = make(*WEEK, {"id": "k", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-28"},
                 {"id": "m", "kind": "event", "title": "Встреча с Димой", "date": "2026-09-26", "start_time": "15:00"},
                 replies=[text("Ясно.")] * 3)
    assert say(brain, "перенеси кр на вторник").startswith("Перенёс на вторник")  # "кр" was no word
    assert say(brain, "что у меня завтра утром").startswith("Завтра утром: в 10:00")
    assert say(brain, "какие у меня задачи").startswith("Задачи:")
    assert "с 21:00 до 22:40" in say(brain, "ЕГЭ не в 22 а в 9")  # "в 9" of a 22:00 exam is 21:00, not 09:00
    assert say(brain, "у меня завтра нет русского").startswith("Удалить завтра")
    say(brain, "нет")
    assert say(brain, "напомни завтра за час до хакатона").startswith("Напомню за час до «Хакатон»")
    say(brain, "перенеси встречу с Димой на 16")
    assert say(brain, "нет не с Димой а с Аней") == "Переименовал: теперь это «Встреча с Аней»."
    assert say(brain, "удали всё кроме хакатона") == "Скажи, за какой день удалить всё."
    assert say(brain, "да") == "За какой день?"
    assert say(brain, "завтра").startswith("Удалить всё завтра, кроме «Хакатон»")
    say(brain, "нет")
    say(brain, "меня зовут Арсений")
    assert say(brain, "меня зовут не Арсений а Арсен") == "Запомнил: тебя зовут Арсен."
    say(brain, "у меня сестра Маша")
    say(brain, "ей 12")
    assert [t for _, t in brain.memory.facts()] == ["Меня зовут Арсен", "У меня сестра Маша, ей 12 лет"]


def test_what_the_eighth_talk_showed():
    brain = make(*WEEK, {"id": "k", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-28"}, replies=[text("Ясно.")] * 3)
    for said in ["мой любимый цвет синий", "нет не синий а зелёный", "мой любимый предмет физика", "на самом деле математика",
                 "у меня брат Олег", "ему 20", "брату исполнилось 21", "мой лучший друг Дима", "нет лучший друг Влад"]:
        say(brain, said)
    assert say(brain, "что ты обо мне знаешь") == ("Я помню: твой любимый цвет зелёный; твой любимый предмет математика; "
                                                  "у тебя брат Олег, ему 21 год; твой лучший друг Влад.")
    assert say(brain, "кр по алгебре когда") == "Понедельник, 28 сентября — задача «Кр по алгебре»."
    assert say(brain, "сколько до физики") == "До «Физика» осталось 1 час 57 минут."
    assert say(brain, "сколько часов до нового года") == "До Нового года осталось 97 дней 9 часов."
    assert say(brain, "а в 15 я свободен завтра").startswith("Да, завтра в 15:00")
    assert say(brain, "я перешёл в 12") == "Ясно."
    assert say(brain, "и напомни за час") == "За час до чего напомнить?"
    assert say(brain, "до хакатона").startswith("Напомню за час до «Хакатон»")
    assert say(brain, "хакатон закончился").startswith("Отметил")


def test_what_the_ninth_talk_showed_about_ages():
    # "ему не десять а двенадцать", "Диме исполнилось четырнадцать", "я ошибся Диме шестнадцать", "на самом деле ей
    # шестнадцать": the model said "обновляю информацию" each time, and the first age stayed
    brain = make(replies=[text("Ясно.")] * 2)
    say(brain, "у меня брат Дима ему десять")
    assert say(brain, "ему не десять а двенадцать") == "Запомнил: у тебя брат Дима ему 12 лет."
    assert say(brain, "Диме исполнилось четырнадцать") == "Запомнил: у тебя брат Дима ему 14 лет."
    say(brain, "у меня сестра Аня")
    say(brain, "ей 15")
    assert say(brain, "на самом деле ей шестнадцать") == "Запомнил: у тебя сестра Аня, ей 16 лет."
    assert [t for _, t in brain.memory.facts()] == ["У меня брат Дима ему 14 лет", "У меня сестра Аня, ей 16 лет"]


def test_what_the_ninth_talk_showed():
    brain = make(*WEEK, replies=[text("Ясно.")] * 3)
    assert "с 11:00 до 14:00 Хакатон" in say(brain, "хакатон начнётся на час позже")
    assert say(brain, "поставь напоминание за час до хакатона").startswith("Напомню за час до «Хакатон»")
    assert say(brain, "напомни про ЕГЭ за два часа").startswith("Напомню за 2 часа до «ЕГЭ»")
    assert say(brain, "какие у меня напоминания").startswith("Напомню: о «Хакатон» — за час")
    say(brain, "перенеси ЕГЭ на 21")
    assert say(brain, "верни хакатон").startswith("Вернул как было") and "Хакатон" in brain.last_reply
    assert "с 19:00 до 20:30 Физика" in say(brain, "перенеси физику на сегодня на вечер")
    assert say(brain, "запиши на завтра в 25 часов сон один раз").startswith("Такого часа нет")
    say(brain, "мой любимый цвет синий")
    assert say(brain, "поменяй мой любимый цвет на красный") == "Запомнил: твой любимый цвет красный."
    say(brain, "я сдаю физику и информатику")
    assert say(brain, "забудь что я сдаю физику") == "Забыл про физику."
    assert say(brain, "что ты обо мне знаешь") == "Я помню: твой любимый цвет красный; ты сдаёшь информатику."


def test_what_the_tenth_talk_showed():
    brain = make(*WEEK, replies=[text("Ясно.")] * 3)
    assert "с 11:00 до 14:00 Хакатон" in say(brain, "теперь хакатон начинается в 11")
    assert "с 12:00 до 15:00 Хакатон" in say(brain, "хакатон будет в 12")
    assert "с 14:00 до 17:00 Хакатон" in say(brain, "хакатон сдвинулся на два часа")
    assert "с 16:00 до 18:00 Физика" in say(brain, "продли физику на полчаса")
    assert "с 14:00 до 15:00 Хакатон" in say(brain, "хакатон теперь до 15")
    assert say(brain, "у меня сегодня вместо физики информатика") == "Переименовал: теперь это «Информатика»."
    say(brain, "у меня брат Дима ему десять")
    assert say(brain, "забудь про Диму").startswith("Забыл: у тебя брат Дима")
    say(brain, "моего друга зовут Саша")
    assert say(brain, "нет его зовут Сашка") == "Запомнил: твоего друга зовут Сашка."
    say(brain, "мама работает врачом")
    say(brain, "мама теперь работает учителем")
    assert say(brain, "что ты обо мне знаешь") == "Я помню: твоего друга зовут Сашка; мама работает учителем."
    assert say(brain, "сколько лет Диме если он с 2010") == "Диме 16, если день рождения в этом году уже был, иначе 15."


def test_what_the_eleventh_talk_showed():
    brain = make(*WEEK, replies=[text("Ясно.")] * 3)
    assert "с 18:00 до 19:30 Физика" in say(brain, "сдвинули физику на два часа")
    assert "с 21:00 до 22:40 ЕГЭ" in say(brain, "не в 22 а в 21 ЕГЭ")
    assert say(brain, "хакатон отменился").startswith("Удалить")
    say(brain, "нет")
    for said in ["папу зовут Игорь", "у меня есть друг Саша ему 17", "мой брат Кирилл"]:
        assert say(brain, said) == "Запомнил.", said
    assert say(brain, "Саше на самом деле 18") == "Запомнил: у тебя есть друг Саша ему 18 лет."
    assert say(brain, "нет брату 15") == "Запомнил: твой брат Кирилл, ему 15 лет."
    assert say(brain, "какой день недели был 1 января 2000") == "1 января 2000 года была суббота."
    say(brain, "запиши на завтра в 15 встречу с Димой один раз")
    say(brain, "и запиши на послезавтра в 15 тоже один раз")
    assert brain.skills.planner.planner.quick_text == "послезавтра в 15 Созвон"  # the fake's title of the last one


def test_what_the_twelfth_talk_showed():
    brain = make(*WEEK, {"id": "p", "kind": "event", "title": "Пробник", "date": "2026-09-30", "start_time": "10:00"},
                 replies=[text("Ясно.")] * 2)
    assert say(brain, "экзамен отложили на два дня") == "Ясно."  # no plan «Экзамен отложили»
    assert say(brain, "напомни мне про пробник за день").startswith("Напомню за день до «Пробник»")
    assert "с 10:00 до 15:00 Хакатон" in say(brain, "хакатон продлили до 15")
    assert say(brain, "когда у меня следующее дело").startswith("Дальше")
    say(brain, "я родился 14 марта 2009")
    assert say(brain, "сколько мне лет") == "Тебе 17 лет."
    assert say(brain, "когда у меня день рождения") == "Твой день рождения 14 марта 2009 года."
