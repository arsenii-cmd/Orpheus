from datetime import datetime

from orpheus.brain import NO_ANSWER, NUDGE, SYSTEM, TOOLS, Brain
from orpheus.config import Config
from orpheus.memory import Memory


class FakeLLM:
    """Replays scripted replies; each reply is a list of streamed chunks."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def chat(self, messages, tools=None, num_predict=None):
        self.requests.append([dict(m) for m in messages])
        yield from self.replies.pop(0)

    def warmup(self, messages, tools=None):
        return {"done": True}


def text(*pieces):
    return [{"message": {"content": p}} for p in pieces] + [{"done": True, "eval_count": 3}]


def tool(name, **arguments):
    return [{"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}},
            {"done": True}]


NOW = datetime(2026, 9, 25, 14, 3)


def make(*replies, **config):
    return Brain(Config(**config), Memory(":memory:"), FakeLLM(*replies))


def test_plain_reply_is_streamed_and_time_goes_into_user_message():
    brain = make(text("Жил-был", " кот."), text("Суббота."))
    assert "".join(brain.ask("расскажи сказку", now=NOW)) == "Жил-был кот."
    assert brain.llm.requests[0][-1] == {"role": "user", "content": "расскажи сказку"}  # nothing about time: no stamp
    list(brain.ask("чем заняться завтра вечером?", now=NOW))
    user = brain.llm.requests[1][-1]
    assert user == {"role": "user", "content": "чем заняться завтра вечером?\n[сейчас пятница, 25 сентября 2026, 14:03; завтра суббота, 26 сентября]"}
    assert "14:03" not in brain.llm.requests[0][0]["content"]


def test_remember_tool_writes_memory_and_confirms_without_a_second_round():
    brain = make(tool("remember", fact="Любит кофе"))
    assert "".join(brain.ask("кстати, я очень люблю кофе", now=NOW)) == "Запомнил."
    assert brain.memory.facts() == [(1, "Любит кофе")]
    assert len(brain.llm.requests) == 1
    assert brain.history[-2] == {"role": "tool", "tool_name": "remember", "content": "запомнено под номером 1"}
    assert brain.history[-1] == {"role": "assistant", "content": "Запомнил."}


def test_failed_or_reading_tools_still_get_a_second_round():
    brain = make(tool("forget", id=7), text("Такого факта нет."), tool("find_notes", query="кофе"), text("Ничего."))
    assert "".join(brain.ask("сотри седьмой факт", now=NOW)) == "Такого факта нет."
    assert "".join(brain.ask("что я писал про кофе", now=NOW)) == "Ничего."
    assert len(brain.llm.requests) == 4


def test_system_prompt_stays_identical_between_turns_for_the_cache():
    brain = make(tool("remember", fact="Кот по имени Барсик"), text("Барсик."))
    list(brain.ask("запомни, кота зовут Барсик", now=NOW))
    list(brain.ask("как зовут кота?", now=NOW))
    first, last = brain.llm.requests[0], brain.llm.requests[-1]
    assert first[:2] == last[:2]  # the fact is not re-inserted into the prompt's start
    assert last[:len(first)] == first  # the whole previous prompt is a prefix of the next one


def test_fresh_conversation_picks_up_new_facts():
    brain = make(tool("remember", fact="Кот по имени Барсик"), text("Ок."))
    list(brain.ask("запомни", now=NOW))
    brain.reset()
    assert "[1] Кот по имени Барсик" in brain.messages()[1]["content"]
    assert brain.messages()[0]["content"] == SYSTEM  # the cached start stays the same
    assert brain.history == []


def test_idle_conversation_is_reset():
    clock = [0.0]
    brain = Brain(Config(idle_reset=60), Memory(":memory:"), FakeLLM(text("a"), text("b")), clock=lambda: clock[0])
    list(brain.ask("раз", now=NOW))
    clock[0] = 100
    list(brain.ask("два", now=NOW))
    assert [m["role"] for m in brain.llm.requests[1]] == ["system", "system", "user"]


def test_find_and_forget_tools():
    brain = make()
    brain.memory.add_note("Код от домофона 1234")
    assert "Код от домофона 1234" in brain.run_tool("find_notes", {"query": "домофон"})
    assert brain.run_tool("forget", {"id": 5}) == "нет факта с таким номером"
    assert brain.run_tool("remember", '{"fact": "x"}').startswith("запомнено")
    assert brain.run_tool("remember", {}).startswith("ошибка")
    assert brain.run_tool("nope", {}).startswith("ошибка")


def test_history_is_trimmed_in_one_cut_starting_at_a_user_message():
    replies = [text("ответ " * 40) for _ in range(40)]  # ~80 tokens: a long spoken answer
    brain = make(*replies, num_ctx=4096)
    sizes = []
    for i in range(40):
        list(brain.ask("вопрос %d" % i, now=NOW))
        sizes.append(len(brain.history))
        assert brain.history[0]["role"] == "user"
    cuts = sum(1 for a, b in zip(sizes, sizes[1:]) if b < a)
    assert 0 < cuts <= 14  # a few big cuts, not a cut on every turn


def test_empty_reply_after_tool_still_says_something():
    # asked once more ("ответь по тому, что нашлось"), and never "Готово." for nothing done
    brain = make(tool("find_notes", query="x"), text(), text("Нашлось: x."))
    assert list(brain.ask("найди x", now=NOW)) == ["Нашлось: x."]
    brain = make(tool("find_notes", query="x"), text(), text())
    assert list(brain.ask("найди x", now=NOW)) == [NO_ANSWER]


def test_an_empty_answer_is_asked_again_and_the_asking_is_not_kept():
    brain = make(text(), text("Земля в порядке."))
    assert "".join(brain.ask("как там Земля?", now=NOW)) == "Земля в порядке."
    assert brain.llm.requests[1][-1] == {"role": "user", "content": NUDGE}
    assert all(m.get("content") != NUDGE for m in brain.history)


def test_tool_names_are_unique():
    names = [t["function"]["name"] for t in TOOLS]
    assert len(names) == len(set(names))


def test_stamp_works_out_tomorrow_across_months_and_years():
    from orpheus.brain import stamp
    assert stamp(datetime(2026, 9, 26, 0, 21)) == "сейчас суббота, 26 сентября 2026, 00:21; завтра воскресенье, 27 сентября"
    assert stamp(datetime(2026, 9, 30, 23, 59)).endswith("завтра четверг, 1 октября")
    assert stamp(datetime(2026, 12, 31, 12, 0)).endswith("завтра пятница, 1 января")


def test_notes_matching_the_phrase_come_along_with_it():
    brain = make(text("4521."))
    brain.memory.add_note("Код от домофона 4521", "домофон")
    list(brain.ask("Какой у меня код от домофона?", now=NOW))
    user = brain.llm.requests[0][-1]["content"]
    assert user.endswith("\n[из памяти: заметка [1]: домофон: Код от домофона 4521]")
    brain2 = make(text("Привет."))
    brain2.memory.add_note("Код от домофона 4521")
    list(brain2.ask("Привет, расскажи анекдот", now=NOW))
    assert "из памяти" not in brain2.llm.requests[0][-1]["content"]


def test_an_action_claimed_with_no_tool_called_is_not_said():
    brain = make(text("Вернул созвон ", "с Васей на 27 сентября."), text("Вернул созвон."))
    assert "".join(brain.ask("ну что там с созвоном", now=NOW)) == "Этого я не сделал: не понял, что именно. Скажи, пожалуйста, иначе."
    brain = make(tool("remember", fact="Любит чай"))
    assert "".join(brain.ask("я люблю чай", now=NOW)) == "Запомнил."
    brain = make(text("Добавлю, если скажете время."))  # not a claim of something done
    assert "".join(brain.ask("надо бы сходить к врачу", now=NOW)) == "Добавлю, если скажете время."


def test_a_passive_claim_is_caught_too():
    brain = make(text("Перенесено на пятнадцать."), text("Перенесено."))
    assert "".join(brain.ask("нет лучше на пятнадцать", now=NOW)).startswith("Этого я не сделал")


def test_a_call_written_as_text_is_made_not_said():
    """Gemma 4 wrote 'web_search{query:<|"|>книги на вечер<|"|>} Поищу вам…' as its answer."""
    from orpheus.brain import text_call
    assert text_call('web_search{query:<|"|>книги на вечер<|"|>}') == \
        {"function": {"name": "web_search", "arguments": {"query": "книги на вечер"}}}
    assert text_call('call:forget{id:3}')["function"]["arguments"] == {"id": 3}
    assert text_call("hello{x:1}") is None
    brain = make([{"message": {"content": p}} for p in ["remember{fact:", '<|"|>Любит чай<|"|>}', " Запомнил!"]] +
                 [{"done": True}])
    assert "".join(brain.ask("я люблю чай", now=NOW)) == "Запомнил."
    assert brain.memory.facts() == [(1, "Любит чай")]


def test_a_tool_named_then_its_argument_is_a_call_too():
    """Gemma 4 with the full prompt: 'remember\\nпереехал в Казань' instead of the call."""
    from orpheus.brain import text_call
    assert text_call("remember\nпереехал в Казань") == {"function": {"name": "remember", "arguments": {"fact": "переехал в Казань"}}}
    assert text_call("add_note купить батарейки") is None  # a space alone is too loose
    assert text_call("forget\n3") is None  # not a text argument
    brain = make(text("Я запомнил, что вы живёте в Казани."), text("Я запомнил."))
    assert "".join(brain.ask("у меня кот по имени барсик", now=NOW)).startswith("Этого я не сделал")


def test_gemma_gets_the_prompt_without_tool_lines_and_a_stamp_written_back_is_not_said():
    from orpheus.brain import SYSTEM, system_for
    assert system_for("gemma4-e4b-heretic") != SYSTEM and "remember" not in system_for("gemma4-e4b-heretic")
    assert system_for("huihui_ai/qwen3-abliterated:4b") == SYSTEM
    brain = make(text("(Сегодня 12:00) Добрый день."), model="gemma4-e2b-heretic")
    assert "".join(brain.ask("привет как жизнь молодая", now=NOW)) == "Добрый день."


def test_a_claim_with_no_call_is_asked_once_more():
    brain = make(text("Я зафиксировал, что вы живёте в Казани."), tool("remember", fact="Живёт в Казани"))
    assert "".join(brain.ask("у меня кот по имени барсик", now=NOW)) == "Запомнил."
    assert brain.memory.facts() == [(1, "Живёт в Казани")]


def test_a_stamp_or_remark_in_brackets_at_the_start_is_not_said():
    from orpheus.brain import without_bracket_start
    assert without_bracket_start("(Воскресенье, 27 сентября 2026, 09:28; Завтра понедельник, 28 сентября) Завтра понедельник.") == \
        "Завтра понедельник."
    assert without_bracket_start("(Поскольку вы не указали пятницу, я не могу ответить.)") == \
        "Поскольку вы не указали пятницу, я не могу ответить."
    assert without_bracket_start("Обычный ответ (с пояснением).") == "Обычный ответ (с пояснением)."
    brain = make(text("(Воскресенье, 27 сентября 2026, 09:28; Завтра", " понедельник, 28 сентября) Завтра понедельник."))
    assert "".join(brain.ask("чем заняться завтра вечером?", now=NOW)) == "Завтра понедельник."


def test_asked_to_do_something_a_claim_anywhere_in_the_answer_is_caught():
    from orpheus.brain import claims_done
    assert claims_done("В четверг пусто, поэтому я добавил тренировку на 18:00.")
    assert not claims_done("Я не добавил тренировку: не знаю времени.")
    assert not claims_done("Добавлю, если скажете время.")
    brain = make(text("В четверг пусто, ", "поэтому я добавил тренировку."), text("В четверг пусто, поэтому я добавил её."))
    assert "".join(brain.ask("посмотри четверг и если пусто добавь тренировку", now=NOW)).startswith("Этого я не сделал")


def test_words_after_a_tool_round_are_spaced_from_those_before():
    brain = make(text("Позвольте поискать."), text("Нашёл."))
    brain.llm.replies[0] = [{"message": {"content": "Позвольте поискать."}},
                            {"message": {"content": "", "tool_calls": [{"function": {"name": "find_notes", "arguments": {"query": "x"}}}]}},
                            {"done": True}]
    assert "".join(brain.ask("что там было", now=NOW)) == "Позвольте поискать. Нашёл."
