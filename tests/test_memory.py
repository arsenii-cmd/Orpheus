from orpheus.memory import Memory


def test_facts_roundtrip():
    m = Memory(":memory:")
    a = m.remember("Хозяина зовут Иван")
    b = m.remember("Любит чёрный кофе")
    assert m.facts() == [(a, "Хозяина зовут Иван"), (b, "Любит чёрный кофе")]
    assert m.update_fact(b, "Любит кофе с молоком")
    assert m.facts()[1] == (b, "Любит кофе с молоком")
    assert m.forget(a)
    assert not m.forget(a)
    assert [f[0] for f in m.facts()] == [b]


def test_notes_search_ignores_endings_and_yo():
    m = Memory(":memory:")
    m.add_note("Встреча с Петей в пятницу", title="встреча")
    m.add_note("Купить зелёный чай")
    m.add_note("Позвонить маме")
    assert [n["text"] for n in m.find_notes("встречи")] == ["Встреча с Петей в пятницу"]
    assert [n["text"] for n in m.find_notes("зеленого")] == ["Купить зелёный чай"]
    assert m.find_notes("самолёт") == []
    assert len(m.find_notes("")) == 3


def test_note_update_and_delete_keep_index_in_sync():
    m = Memory(":memory:")
    n = m.add_note("Пароль от роутера на наклейке")
    assert m.update_note(n, "Код от домофона 1234")
    assert m.find_notes("роутер") == []
    assert m.find_notes("домофон")[0]["id"] == n
    assert m.delete_note(n)
    assert m.find_notes("домофон") == []


def test_memory_can_be_used_from_another_thread():
    import threading

    m = Memory(":memory:")
    m.remember("факт")
    out = []
    t = threading.Thread(target=lambda: out.append(m.facts()))
    t.start()
    t.join()
    assert out == [[(1, "факт")]]


def test_related_notes_ignore_filler_words():
    m = Memory(":memory:")
    m.add_note("Код от домофона 4521", "домофон")
    m.add_note("Как варить гречку: 1 к 2")
    m.add_note("Позвонить маме в субботу")
    assert [n["text"] for n in m.related_notes("Какой у меня код от домофона?")] == ["Код от домофона 4521"]
    assert m.related_notes("Как у тебя дела?") == []
    assert m.related_notes("") == []
