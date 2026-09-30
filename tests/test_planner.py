import calendar
from datetime import date, timedelta

from orpheus.brain import Brain
from orpheus.config import Config
from orpheus.memory import Memory
from orpheus.planner import PlannerTools, Unavailable, period
from test_brain import NOW, FakeLLM, text, tool

FRI = date(2026, 9, 25)  # a Friday


class FakePlanner:
    """plannerd's /api in memory."""

    DEFAULTS = {"body": "", "title": "", "done": False, "start_time": None, "end_time": None, "date": None}

    def __init__(self, *items):
        self.rows = {i["id"]: dict(self.DEFAULTS, **i) for i in items}
        self.deleted = []
        self.down = False
        self.clock = 0

    def items(self, first, last, text=""):
        if self.down:
            raise Unavailable("планировщик недоступен")
        rows = [r for r in self.rows.values() if r["kind"] != "note" and r["date"] and first.isoformat() <= r["date"] <= last.isoformat()
                and text.casefold() in r["title"].casefold()]
        return sorted(rows, key=lambda r: (r["date"], r["start_time"] or "99:99"))

    def notes(self):
        if self.down:
            raise Unavailable("планировщик недоступен")
        return sorted((r for r in self.rows.values() if r["kind"] == "note"), key=lambda r: -r.get("updated_at", 0))

    def quick(self, text, default_day=None):
        self.default_day = default_day
        self.quick_text = text
        item = {"id": "new", "kind": "event", "title": "Созвон", "date": "2026-09-26", "start_time": "12:00",
                "end_time": None, "done": False, "body": ""}
        self.rows["new"] = item
        return item

    def save(self, item):
        repeat = item.get("repeat")
        if repeat:  # as plannerd does: [count] items a week or a month apart, one series
            self.clock += 1
            series, first = "s%d" % self.clock, date.fromisoformat(item["date"])
            made = []
            for k in range(repeat["count"]):
                if repeat["unit"] == "day":
                    day = first + timedelta(days=k)
                elif repeat["unit"] == "week":
                    day = first + timedelta(weeks=k)
                else:  # the 31st in a shorter month is its last day, as plannerd does
                    y, m = first.year + (first.month - 1 + k) // 12, (first.month - 1 + k) % 12 + 1
                    day = date(y, m, min(first.day, calendar.monthrange(y, m)[1]))
                one = {x: v for x, v in item.items() if x not in ("repeat", "id")}
                made.append(FakePlanner.save(self, dict(one, date=day.isoformat(), series=series)))
            return made[0]
        self.clock += 1
        item = dict(self.DEFAULTS, **item, updated_at=self.clock)
        item.setdefault("id", "id%d" % self.clock)
        if not item.get("id"):
            item["id"] = "id%d" % self.clock
        self.rows[item["id"]] = item
        return item

    def delete(self, item_id):
        self.deleted.append(item_id)
        del self.rows[item_id]
        return {"ok": True}

    def delete_series(self, series):
        for item_id in [k for k, r in self.rows.items() if r.get("series") == series]:
            self.delete(item_id)
        return {"ok": True}


def tools(*items):
    return PlannerTools(FakePlanner(*items), today=lambda: FRI, now=lambda: NOW)


def test_days_are_worked_out_here_not_by_the_model():
    d = lambda s: date.fromisoformat(s)  # noqa: E731
    assert period("", FRI) == (FRI, FRI)
    assert period("Завтра", FRI) == (d("2026-09-26"),) * 2
    assert period("послезавтра", FRI) == (d("2026-09-27"),) * 2
    assert period("в понедельник", FRI) == (d("2026-09-28"),) * 2
    assert period("в пятницу", FRI) == (FRI, FRI)
    assert period("следующая пятница", FRI) == (d("2026-10-02"),) * 2
    assert period("на выходных", FRI) == (d("2026-09-26"), d("2026-09-27"))
    assert period("неделя", FRI) == (FRI, d("2026-10-01"))
    assert period("следующая неделя", FRI) == (d("2026-09-28"), d("2026-10-04"))
    assert period("25.10", FRI) == (d("2026-10-25"),) * 2
    assert period("3 января", FRI) == (d("2027-01-03"),) * 2
    assert period("когда-нибудь", FRI) is None


def test_plans_are_read_out_as_words():
    t = tools({"id": "1", "kind": "event", "title": "Созвон", "date": "2026-09-26", "start_time": "12:00", "end_time": "13:00"},
              {"id": "2", "kind": "task", "title": "Купить хлеб", "date": "2026-09-26"})
    assert t.plans("завтра") == "завтра, суббота, 26 сентября: с 12:00 до 13:00 Созвон; задача «Купить хлеб»"
    assert t.plans("сегодня") == "сегодня, пятница, 25 сентября: в планах ничего нет"
    t.planner.save({"kind": "note", "title": "Пароль", "body": "wi-fi 1234"})
    assert "wi-fi 1234" in t.plans("заметки")
    assert t.plans("когда-нибудь").startswith("уточни")


def test_done_marks_the_nearest_open_item():
    t = tools({"id": "old", "kind": "task", "title": "Купить хлеб", "date": "2026-09-20", "done": True},
              {"id": "now", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"})
    assert t.done("хлеб") == "Отметил: «Купить хлеб» — сделано."
    assert t.planner.rows["now"]["done"] is True
    assert t.done("молоко").startswith("нет ")


def test_the_day_and_time_said_pick_the_item():
    today = {"id": "t", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-25", "start_time": "18:00"}
    tomorrow = {"id": "m", "kind": "event", "title": "Занятие по русскому", "date": "2026-09-26", "start_time": "10:00"}
    physics = {"id": "p", "kind": "event", "title": "Физика", "date": "2026-09-26", "start_time": "10:00"}
    t = tools(today, tomorrow, physics)
    t.new_turn("Отмени завтра в 10 занятия по русскому языку.")
    assert "26 сентября — в 10:00 Занятие по русскому" in t.delete("занятие по русскому")  # the day from the phrase
    t.new_turn("отмени занятие")
    assert "25 сентября" in t.delete("занятия по русскому", when="сегодня")
    assert "26 сентября" in t.delete("русский", when="завтра в 10")
    assert t.delete("русский", when="в понедельник").startswith("нет на понедельник")


def test_quick_add_text_is_tidied():
    from orpheus.planner import clean_quick
    assert clean_quick("Сегодня, суббота, — в 18:00 Занятие по русскому языку") == "Сегодня в 18:00 Занятие по русскому языку"
    assert clean_quick("в пятницу 19:00 позвонить маме") == "в пятницу 19:00 позвонить маме"
    assert clean_quick("завтра, в воскресенье, созвон") == "завтра созвон"


def brain_with(*items, replies=()):
    return Brain(Config(), Memory(":memory:"), FakeLLM(*replies), planner=tools(*items))


RUS_TODAY = {"id": "t", "kind": "event", "title": "Занятие по русскому языку", "date": "2026-09-25", "start_time": "18:00"}
RUS_TOMORROW = {"id": "m", "kind": "event", "title": "Занятие по русскому языку", "date": "2026-09-26", "start_time": "10:00"}
PHYSICS = {"id": "p", "kind": "event", "title": "Физика", "date": "2026-09-25", "start_time": "12:00", "end_time": "13:30"}


def say(brain, phrase):
    return "".join(brain.ask(phrase, now=NOW))


def test_the_program_adds_without_the_model():
    brain = brain_with()
    assert say(brain, "Запиши на завтра в 12 созвон один раз") == "Записал на завтра, субботу, 26 сентября: в 12:00 Созвон."
    assert brain.llm.requests == []
    assert brain.tail[-1] == {"role": "assistant", "content": "Записал на завтра, субботу, 26 сентября: в 12:00 Созвон."}


def test_a_list_of_the_day_needs_no_model_and_a_question_gets_the_facts():
    brain = brain_with(RUS_TODAY, RUS_TOMORROW, replies=[text("Да, в 10.")])
    assert say(brain, "А что у меня завтра?") == "Завтра, суббота, 26 сентября: в 10:00 Занятие по русскому языку."
    assert say(brain, "Какие занятия есть завтра?").startswith("Завтра, суббота")
    assert brain.llm.requests == []
    assert say(brain, "Есть ли завтра занятие по русскому в 10?") == "Да: в 10:00 Занятие по русскому языку."
    assert brain.llm.requests == []
    assert say(brain, "Что мне взять завтра на занятие по русскому?") == "Да, в 10."
    assert "[из планировщика: подходящее: завтра, суббота, 26 сентября — в 10:00 Занятие по русскому языку]" in brain.llm.requests[0][-1]["content"]


def test_cancel_tomorrow_picks_tomorrow_and_waits_for_yes():
    brain = brain_with(RUS_TODAY, RUS_TOMORROW)
    assert say(brain, "Отмени завтра в 10 занятие по русскому.") == \
        "Удалить завтра, суббота, 26 сентября — в 10:00 Занятие по русскому языку?"
    assert brain.planner.planner.deleted == []
    assert say(brain, "Да.") == "Удалил: завтра, суббота, 26 сентября — в 10:00 Занятие по русскому языку."
    assert brain.planner.planner.deleted == ["m"]
    assert brain.llm.requests == []


def test_no_means_no_and_back_brings_the_same_item():
    brain = brain_with(PHYSICS)
    say(brain, "Удали физику")
    assert say(brain, "Нет, не удаляй") == "Хорошо, не удаляю."
    assert brain.planner.planner.deleted == []
    say(brain, "Удали физику")
    say(brain, "да")
    assert brain.planner.planner.deleted == ["p"]
    assert say(brain, "Ой, верни обратно") == "Вернул: сегодня, пятница, 25 сентября — с 12:00 до 13:30 Физика."
    assert brain.planner.planner.rows["p"]["title"] == "Физика"
    assert brain.llm.requests == []


def test_a_yes_much_later_deletes_nothing():
    brain = brain_with(PHYSICS, replies=[text("Хорошо.")])
    say(brain, "Удали физику")
    say(brain, "Как дела?")  # the model's turn in between
    say(brain, "Да.")  # a yes this late is not the answer to "Удалить …?"
    assert brain.planner.planner.deleted == []


def test_done_and_not_found_are_said_as_speech():
    brain = brain_with({"id": "b", "kind": "task", "title": "Купить хлеб", "date": "2026-09-25"})
    assert say(brain, "Отметь хлеб купленным") == "Отметил: «Купить хлеб» — сделано."
    assert say(brain, "Удали молоко").startswith("Не нашёл")


def test_notes_go_to_the_planner_and_facts_to_the_memory():
    brain = brain_with()
    assert say(brain, "Добавь в заметки код 4521") == "Записал в заметки: Код 4521."
    note = next(r for r in brain.planner.planner.rows.values() if r["kind"] == "note")
    assert (note["title"], note["body"]) == ("Код 4521", "")
    assert say(brain, "Запомни, что я люблю кофе") == "Запомнил."
    assert brain.memory.facts() == [(1, "Я люблю кофе")]
    assert brain.llm.requests == []


def test_planner_down_is_said():
    brain = brain_with()
    brain.planner.planner.down = True
    assert say(brain, "что у меня завтра") == "Планировщик сейчас не отвечает."


def test_a_tool_call_that_says_back_is_a_restore():
    """The model's own way of "верни": quick-add the words of "Удалил: …" - brings back the item."""
    brain = brain_with(RUS_TODAY, replies=[tool("add_plan", text="Сегодня, пятница, 25 сентября — в 18:00 Занятие по русскому языку")])
    say(brain, "удали русский сегодня")
    say(brain, "да")
    assert say(brain, "слушай, а можно его назад") == "Вернул: сегодня, пятница, 25 сентября — в 18:00 Занятие по русскому языку."
    assert "new" not in brain.planner.planner.rows


def routed(brain, phrase):
    result = brain.skills.handle(phrase, NOW)
    return "context" if type(result).__name__ == "Context" else ("reply" if result is not None else None)


def test_what_the_program_takes_and_what_it_leaves_to_the_model():
    brain = brain_with(RUS_TODAY, RUS_TOMORROW, PHYSICS)
    for phrase in ["Что у меня завтра?", "А что завтра?", "Какие планы на неделю?", "Что по плану сегодня?",
                   "Что у меня в пятницу?", "Какие занятия есть завтра?", "Расскажи, что у меня на выходных",
                   "Добавь на завтра в 12 созвон", "Напомни в пятницу в 19 позвонить маме",
                   "Запланируй на 3 октября в 15:30 стоматолога", "Отметь физику выполненной", "Удали физику",
                   "Есть ли завтра занятие по русскому в 10?", "Во сколько завтра русский?", "Когда у меня физика на этой неделе?"]:
        brain.skills.pending = None
        assert routed(brain, phrase) == "reply", phrase
    for phrase in ["Что мне взять завтра на занятие по русскому?", "Есть ли завтра занятие по физике?",
                   "Во сколько мне лучше выйти завтра, чтобы успеть на занятие по русскому?"]:
        assert routed(brain, phrase) == "context", phrase
    for phrase in ["Что такое фотосинтез?", "Как ты думаешь, стоит ли идти?", "Расскажи анекдот",
                   "Удали заметку три", "Забудь факт два", "Напомни, что ты умеешь делать?", "Давай поговорим о жизни",
                   "Сколько будет стоить ремонт?", "у меня завтра день рождения."]:
        assert routed(brain, phrase) != "reply" or brain.skills.handled not in ("plan_ask", "plan_add", "plan_delete"), phrase


def test_move_keeps_the_length_and_asks_when_the_target_is_unclear():
    brain = brain_with(RUS_TODAY, RUS_TOMORROW, PHYSICS)
    assert say(brain, "Перенеси физику на завтра в 15") == "Перенёс на завтра, субботу, 26 сентября: с 15:00 до 16:30 Физика."
    assert say(brain, "Перенеси завтрашний русский на понедельник") == \
        "Перенёс на понедельник, 28 сентября: в 10:00 Занятие по русскому языку."
    assert brain.planner.planner.rows["t"]["date"] == "2026-09-25"  # today's one stays
    assert say(brain, "Перенеси физику на потом").startswith("На какой день")
    assert brain.llm.requests == []


def test_when_questions_get_only_what_they_are_about():
    exam = {"id": "e", "kind": "task", "title": "Пробник ЕГЭ по физике", "date": "2026-09-30"}
    brain = brain_with(RUS_TODAY, PHYSICS, exam)
    assert brain.skills.handle("Когда на этой неделе пробник по физике?", NOW) == "Среда, 30 сентября — задача «Пробник ЕГЭ по физике»."
    assert brain.skills.handle("Когда у меня пробник?", NOW) == "Среда, 30 сентября — задача «Пробник ЕГЭ по физике»."
    assert brain.skills.handle("Во сколько сегодня физика?", NOW) == "С 12:00 до 13:30 Физика."


def test_titles_are_tidied():
    from orpheus.planner import tidy_title
    assert tidy_title("в стоматолога") == "Стоматолог"
    assert tidy_title("к врачу на") == "Врач"
    assert tidy_title("Встречу") == "Встреча"
    assert tidy_title("тренировку по боксу") == "Тренировка по боксу"
    assert tidy_title("лекцию") == "Лекция"
    assert tidy_title("Позвонить маме") == "Позвонить маме"
    assert tidy_title("Созвон с Петей") == "Созвон с Петей"


def test_did_it_marks_an_open_item_and_something_there_lists_the_day():
    brain = brain_with(RUS_TOMORROW, {"id": "a", "kind": "task", "title": "Кр по алгебре", "date": "2026-09-28"})
    assert say(brain, "Я написал кр по алгебре, отметь её.") == \
        "Отметил: «Кр по алгебре» (понедельник, 28 сентября) — сделано."
    assert say(brain, "Слушай, а у меня завтра что-нибудь есть?") == "Завтра, суббота, 26 сентября: в 10:00 Занятие по русскому языку."
    assert brain.llm.requests == []
