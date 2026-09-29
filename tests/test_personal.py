import hashlib
import re
from datetime import datetime

import numpy as np

from orpheus.brain import Brain, mode_command
from orpheus.config import Config
from orpheus.memory import Memory
from test_brain import FakeLLM, text  # the one fake model all the tests talk to

NOW = datetime(2026, 9, 26, 14, 5)


class WordEmbedder:
    """Stands in for the real model: a bag of word stems, so "пароль вайфая" is near "пароль сети"."""

    def anchors(self):
        from orpheus.vectors import ANCHORS
        return self.embed(ANCHORS)

    def embed(self, texts):
        out = np.zeros((len(texts), 256), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                out[i, int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 256] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def make(*replies, embedder=None):
    return Brain(Config(), Memory(":memory:", embedder), FakeLLM(*replies), private=Memory(":memory:", embedder))


def test_mode_phrases():
    for p in ["Давай поговорим о личном", "личное", "Режим личное.", "включи личное", "хочу поговорить про личное"]:
        assert mode_command(p) is True, p
    for p in ["Хватит о личном", "выйди из личного", "закончим с личным", "обычный режим", "Кватит его лично.", "Квати то лично.",
              "стоп, личное"]:
        assert mode_command(p) is False, p
    for p in ["Что у меня личного в заметках было?", "Привет", "Это личное дело каждого"]:
        assert mode_command(p) is None, p


def test_personal_section_writes_to_its_own_memory_and_back():
    b = make(text("Понимаю."), text("Ок."), embedder=WordEmbedder())
    assert "".join(b.ask("давай поговорим о личном", now=NOW)) == "Слушаю. Это останется между нами."
    assert b.personal and b.llm.requests == []  # the switch never reaches the model
    assert "".join(b.ask("запомни, что я переживаю из-за Виктора", now=NOW)) == "Запомнил."
    assert b.private.facts() == [(1, "Я переживаю из-за Виктора")] and b.memory.facts() == []
    list(b.ask("мне грустно", now=NOW))
    assert b.private.turns()[0]["user"] == "мне грустно" and b.memory.turns() == []
    assert "Сейчас раздел «Личное»" in b.llm.requests[-1][1]["content"]
    assert "".join(b.ask("хватит о личном", now=NOW)) == "Вернулись к обычному."
    assert not b.personal and b.history == []
    list(b.ask("расскажи что-нибудь", now=NOW))
    assert "Виктор" not in str(b.llm.requests[-1])  # a personal fact is not in the ordinary prompt


def test_the_ordinary_section_remembers_nothing_of_personal():
    # the owner: in the ordinary section it must remember nothing of «Личное», even asked about it
    b = make(text("Не знаю."), text("Ты переживал из-за ссоры."), embedder=WordEmbedder())
    b.private.remember("Хозяин поссорился с Виктором из-за денег")
    b.private.add_note("Виктор занял у меня пять тысяч")
    list(b.ask("что у меня с Виктором?", now=NOW))
    asked = str(b.llm.requests[0])
    assert "поссорился" not in asked and "пять тысяч" not in asked and "(личное)" not in asked
    list(b.ask("личное", now=NOW))
    list(b.ask("что у меня с Виктором?", now=NOW))
    assert "поссорился с Виктором" in str(b.llm.requests[1])  # in «Личное» itself, it does


def test_the_fixed_prompt_start_survives_a_switch():
    b = make(text("a"), text("b"))
    list(b.ask("расскажи сказку", now=NOW))
    list(b.ask("личное", now=NOW))
    list(b.ask("расскажи сказку", now=NOW))
    assert b.llm.requests[0][0] == b.llm.requests[1][0]  # only the second message differs


def test_a_long_silence_leaves_the_personal_section():
    clock = [0.0]
    b = Brain(Config(idle_reset=60), Memory(":memory:"), FakeLLM(text("a")), clock=lambda: clock[0],
              private=Memory(":memory:"))
    list(b.ask("личное", now=NOW))
    clock[0] = 100
    list(b.ask("привет", now=NOW))
    assert not b.personal and b.private.turns() == [] and b.memory.turns()[0]["user"] == "привет"


def test_without_a_private_memory_the_words_are_ordinary():
    b = Brain(Config(), Memory(":memory:"), FakeLLM(text("Про личное?")))
    assert "".join(b.ask("давай поговорим о личном", now=NOW)) == "Про личное?"


def test_recall_by_meaning_finds_notes_and_past_talks():
    m = Memory(":memory:", WordEmbedder())
    m.add_note("Пароль сети вайфай: ml3")
    m.add_note("Купить гречку и молоко")
    m.add_turn("я поссорился с Виктором из-за денег", "Сочувствую.")
    m.add_turn("хочу летом в Грузию", "Отличная идея.")
    assert m.recall("какой пароль от вайфая")[0]["text"] == "Пароль сети вайфай: ml3"
    assert m.recall("что я говорил про Виктора")[0]["kind"] == "turn"
    assert m.recall("") == []


def test_personal_recall_brings_the_general_one_marked():
    b = make(text("a"), text("b"), embedder=WordEmbedder())
    b.memory.add_note("Виктор вернёт долг в пятницу")
    list(b.ask("личное", now=NOW))
    list(b.ask("когда Виктор вернёт долг", now=NOW))
    assert "(общее) заметка" in b.llm.requests[0][-1]["content"]


def test_only_a_command_opens_personal():
    for phrase in ["Давай лично встретимся в пятницу", "Это личное дело Виктора", "Лично я думаю, что да"]:
        assert mode_command(phrase) is None, phrase
    for phrase in ["Давай о личном", "Открой личное", "Включи режим личное"]:
        assert mode_command(phrase) is True, phrase


def test_reindex_fills_vectors_for_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    old = Memory(path)
    old.add_note("Код от домофона 4521")
    old.add_turn("привет", "привет")
    old.close()
    m = Memory(path, WordEmbedder())
    assert m.reindex() == 2 and m.reindex() == 0


def test_past_talks_are_found_by_a_name_without_the_model():
    m = Memory(":memory:")  # no embedder: words only
    m.add_turn("я поссорился с Виктором из-за денег, переживаю", "Понимаю.")
    m.add_turn("мне сегодня как-то грустно", "Слушаю.")
    m.add_turn("я хочу летом поехать в Грузию", "Отлично.")
    found = m.recall("что я говорил про Виктора?")
    assert [i["kind"] for i in found] == ["turn"] and "Виктором" in found[0]["text"]
    assert m.recall("что я говорил?") == []


def test_a_locked_section_says_so_and_opens_with_the_key(tmp_path):
    from orpheus.memory import SealedMemory
    from orpheus.secure import new_key, parse_key

    b = Brain(Config(), Memory(":memory:"), FakeLLM(text("ok")), locked=True)
    assert "закрыто" in "".join(b.ask("давай поговорим о личном", now=NOW))
    assert not b.personal
    b.unlock(SealedMemory(tmp_path / "p.enc", parse_key(new_key())))
    assert "".join(b.ask("давай поговорим о личном", now=NOW)) == "Слушаю. Это останется между нами."
    assert b.personal


def test_a_mode_switch_is_its_own_turn():
    # the server ends the conversation after "stop" by brain.handled: a switch right after it must not keep it
    b = make()
    list(b.ask("Стоп.", now=NOW))
    assert b.handled == "stop"
    list(b.ask("Давай о личном.", now=NOW))
    assert b.handled == "mode" and b.calls == []
