import pytest

pytest.importorskip("websockets")  # the one thing it may lack; any other import error fails
from orpheus import server  # noqa: E402


def test_only_the_tunnel_and_the_home_lan_get_in():
    assert server.allowed("10.8.0.1")
    assert server.allowed("192.168.2.118")
    assert server.allowed("127.0.0.1")
    assert server.allowed("::ffff:192.168.2.5")
    assert not server.allowed("10.8.0.2")
    assert not server.allowed("192.168.3.1")
    assert not server.allowed("8.8.8.8")
    assert not server.allowed("garbage")


def test_wake_word_leftovers_are_dropped():
    assert server.strip_wake_word("Орфей, какая погода?") == "какая погода?"
    assert server.strip_wake_word("фей, какая погода") == "какая погода"
    assert server.strip_wake_word("какая погода") == "какая погода"
    assert server.strip_wake_word("А фей, сколько будет два плюс два?") == "сколько будет два плюс два?"
    assert server.strip_wake_word("Арфеи, привет") == "привет"
    assert server.strip_wake_word("А где мой телефон?") == "А где мой телефон?"
    assert server.strip_wake_word("Скажи, Орфей, который час") == "который час"
    assert server.strip_wake_word("Фейерверк будет вечером") == "Фейерверк будет вечером"
    # from a metre away: the start misheard, the comma still there
    assert server.strip_wake_word("Арфий, который час") == "который час"
    assert server.strip_wake_word("Архей, сколько тебе лет?") == "сколько тебе лет?"
    assert server.strip_wake_word("Рофей, какие у меня планы на завтра?") == "какие у меня планы на завтра?"
    assert server.strip_wake_word("Арфи, давай поболтаем.") == "давай поболтаем."
    assert server.strip_wake_word("Андрей, привет") == "Андрей, привет"
    assert server.strip_wake_word("Хорошо, давай") == "Хорошо, давай"
    assert server.strip_wake_word("Привет.") == "Привет."


def test_pcm16():
    pcm = server.to_pcm16([0.0, 1.0, -1.0, 2.0])
    assert pcm == b"\x00\x00\xff\x7f\x01\x80\xff\x7f"


def test_a_phrases_messages_carry_its_id():
    import asyncio
    sent = []

    async def send(message):
        sent.append(message)

    async def turn():
        tagged = server.tag_phrase(send, 7)
        for message in ({"type": "transcript", "text": "да"}, {"type": "reply", "text": "Ок."}, {"type": "audio", "sample_rate": 22050},
                        b"\x00\x00", {"type": "audio_end", "expect_reply": False}, {"type": "mode", "personal": False}):
            await tagged(message)
        assert server.tag_phrase(send, None) is send  # an older app: as before

    asyncio.run(turn())
    assert [m.get("id") for m in sent if isinstance(m, dict)] == [7, 7, 7, 7, None]  # "mode" is not the phrase's
    assert sent[3] == b"\x00\x00"


def test_a_new_connection_starts_out_of_personal():
    import asyncio
    o = server.Orpheus.__new__(server.Orpheus)
    o.turn_lock = asyncio.Lock()

    class B:
        personal = True

        def set_personal(self, on):
            changed = self.personal != on
            self.personal = on
            return changed
    o.brain = B()
    asyncio.run(o.leave_personal("новое подключение"))
    assert o.brain.personal is False


def test_reminders_are_said_once_when_their_time_comes():
    from datetime import datetime
    items = [{"id": "t", "kind": "event", "title": "Тренировка", "date": "2026-09-30", "start_time": "19:00", "remind": 15, "done": False},
             {"id": "e", "kind": "event", "title": "ЕГЭ", "date": "2026-09-30", "start_time": "22:00", "remind": 60, "done": False},
             {"id": "n", "kind": "event", "title": "Без напоминания", "date": "2026-09-30", "start_time": "19:00", "remind": None, "done": False},
             {"id": "k", "kind": "task", "title": "Купить хлеб", "date": "2026-09-30", "remind": 15, "done": False}]
    done = set()
    assert server.due_reminders(items, datetime(2026, 9, 30, 18, 44), done) == []
    due = server.due_reminders(items, datetime(2026, 9, 30, 18, 45), done)
    assert [i["id"] for _, i in due] == ["t"]
    assert server.reminder_text(due[0][1]) == "Напоминаю: через 15 минут, в 19:00, — тренировка."
    done.add(due[0][0])
    assert server.due_reminders(items, datetime(2026, 9, 30, 18, 50), done) == []
    moved = [dict(items[0], start_time="20:00")]  # moved: said again at its new time
    assert server.due_reminders(moved, datetime(2026, 9, 30, 19, 45), done)
    assert server.reminder_text(items[1]) == "Напоминаю: через час, в 22:00, — ЕГЭ."
