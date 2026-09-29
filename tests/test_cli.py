from orpheus.__main__ import addressed


def test_wake_word():
    assert addressed("Орфей, который час?", "орфей") == "который час?"
    assert addressed("орфей", "Орфей") == ""
    assert addressed("Который час?", "орфей") is None


def test_wake_word_as_misheard_and_not_first():
    assert addressed("Арфей, ты тут?", "орфей") == "ты тут?"
    assert addressed("Скажи, Орфей, который час", "орфей") == "который час"
    assert addressed("Ну вот, Орфея позвали", "орфей") == "позвали"


def test_ordinary_speech_is_not_addressed():
    assert addressed("Не, Алиса, стоп. Не, я для химии ничего не делал", "орфей") is None
    assert addressed("Офис открыт до шести", "орфей") is None
    assert addressed("Слушай, а потом Орфей что-то сказал", "орфей") is None  # too far in
