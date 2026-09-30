"""The owner's fixed commands (commands.py): "Запиши …" with its questions, the checklist, "Заметка …"."""

from orpheus.commands import read_repeat
from test_skills import FRI, make, say


def rows(brain):
    return sorted(brain.planner.planner.rows.values(), key=lambda r: (r["date"] or "", r["id"]))


def test_the_repeat_is_read_in_any_place():
    assert read_repeat("тренировку в среду в 6 утра 10 недель") == ("week", 10, None, "тренировку в среду в 6 утра")
    assert read_repeat("зарядку каждый день в 7 утра 30 дней") == ("day", 30, None, "зарядку в 7 утра")
    assert read_repeat("тренировку десять недель в среду в шесть")[:2] == ("week", 10)
    assert read_repeat("бассейн в пятницу в 18 с повтором на 5 недель")[:2] == ("week", 5)
    assert read_repeat("платёж 5 числа в 10 3 месяца")[:2] == ("month", 3)
    assert read_repeat("кр 3 октября в 10 один раз") == (None, 1, None, "кр 3 октября в 10")
    assert read_repeat("тренировку по понедельникам и средам в 6 2 месяца") == ("week", 8, [0, 2], "тренировку в 6")
    assert read_repeat("зарядку каждый день")[:2] == ("day", None)
    # a time or a date, not a repeat
    assert read_repeat("обед в 2 дня")[:2] == (None, None)
    assert read_repeat("встречу с 2 до 4 дня")[:2] == (None, None)
    assert read_repeat("врача через 2 недели в 10")[:2] == (None, None)


def test_all_said_at_once_is_written():
    brain = make()
    answer = say(brain, "Орфей, запиши тренировку в среду в 6 утра, 10 недель.")
    assert answer.startswith("Записал «Созвон»") and "каждую неделю, 10 раз" in answer, answer
    assert brain.planner.planner.quick_text.lower().startswith("тренировку")
    assert len([r for r in rows(brain) if r.get("series")]) == 10
    assert brain.skills.handled == "record"


def test_the_time_and_the_count_are_asked():
    brain = make()
    assert say(brain, "Запиши тренировку") == "Время?"
    assert say(brain, "в среду в 6 утра") == "Сколько раз?"
    answer = say(brain, "5 недель")
    assert "каждую неделю, 5 раз" in answer, answer
    assert brain.planner.planner.quick_text == "тренировку в среду в 06:00"


def test_once_is_no_repeat():
    brain = make()
    assert say(brain, "Запиши стоматолога в пятницу в 10") == "Сколько раз?"
    answer = say(brain, "один раз")
    assert answer.startswith("Записал") and "раз" not in answer.split(":")[1], answer
    assert not any(r.get("series") for r in rows(brain))


def test_a_bare_number_takes_the_unit_said_before():
    brain = make()
    assert say(brain, "Запиши зарядку каждый день в 7 утра") == "Сколько раз?"
    assert "каждый день, 30 раз" in say(brain, "30")
    assert len([r for r in rows(brain) if r.get("series")]) == 30


def test_cancel_and_another_command_drop_the_question():
    brain = make()
    assert say(brain, "Запиши тренировку") == "Время?"
    assert say(brain, "отмена") == "Хорошо, не записываю."
    assert rows(brain) == []
    assert say(brain, "Запиши тренировку") == "Время?"
    assert say(brain, "Запиши в чек-лист купить молоко") == "Добавил в чек-лист: Купить молоко."


def test_the_checklist_is_a_task_for_today():
    brain = make()
    assert say(brain, "Запиши в чек лист позвонить в банк") == "Добавил в чек-лист: Позвонить в банк."
    task = rows(brain)[0]
    assert task["kind"] == "task" and task["date"] == FRI.isoformat() and task["title"] == "Позвонить в банк"
    assert say(brain, "верни").startswith("Убрал из чек-листа")


def test_a_note_is_word_for_word():
    brain = make()
    assert say(brain, "Заметка: код от домофона 4521, второй подъезд") == \
        "Записал в заметки: Код от домофона 4521, второй подъезд."
    assert say(brain, "Запиши в заметки идея: бот для каналов").startswith("Записал в заметки: Идея")


def test_nothing_to_write_is_asked_for():
    brain = make()
    assert say(brain, "Запиши").startswith("Что записать?")


def test_adding_without_the_command_is_not_done():
    brain = make(replies=["Скажи «Запиши», и я запишу."])
    say(brain, "Добавь в понедельник в 6 утра тренировку")
    assert rows(brain) == []
