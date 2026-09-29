from orpheus.speech import Sentences, clean


def split(pieces):
    s = Sentences()
    out = []
    for p in pieces:
        out += s.feed(p)
    return out + s.flush()


def test_streamed_text_is_cut_into_sentences():
    text = "Доброе утро! Сегодня у тебя два дела. Первое — созвон в 14:30, второе — занятие."
    pieces = [text[i:i + 4] for i in range(0, len(text), 4)]
    assert split(pieces) == ["Доброе утро!", "Сегодня у тебя два дела.",
                             "Первое — созвон в 14:30, второе — занятие."]


def test_number_with_dot_is_not_cut_in_the_middle():
    assert split(["Температура 3", ".", "5 градуса, ", "тепло."]) == ["Температура 3.5 градуса, тепло."]


def test_short_fragments_are_merged():
    assert split(["Да. ", "Запомнил это."]) == ["Да. Запомнил это."]


def test_long_text_without_punctuation_is_cut_at_a_space():
    out = split(["слово " * 60])
    assert len(out) > 1 and all(len(s) <= 180 for s in out)


def test_markup_is_removed():
    assert clean("**Важно**: `код` # заголовок") == "Важно: код заголовок"


def test_first_clause_is_spoken_before_the_sentence_ends():
    s = Sentences(first_clause=20)
    out = []
    for piece in ["Я умею разговаривать, ", "помнить о вас, ", "вести заметки и знать дату. ", "Ещё, если нужно, считаю."]:
        out += s.feed(piece)
    out += s.flush()
    assert out == ["Я умею разговаривать,", "помнить о вас, вести заметки и знать дату.", "Ещё, если нужно, считаю."]


def test_short_first_clause_waits_for_more():
    s = Sentences(first_clause=20)
    assert s.feed("Да, ") == []
    assert s.feed("конечно, завтра воскресенье. ") == ["Да, конечно, завтра воскресенье."]


def test_links_are_said_by_the_site_name():
    from orpheus.speech import clean
    assert clean("Данные из Википедии — [ru.wikipedia.org](https://ru.wikipedia.org/wiki/Ельцин).") == \
        "Данные из Википедии — ru.wikipedia.org."
    assert clean("Смотрите https://www.cbr.ru/currency_base/daily/ — там всё.") == "Смотрите cbr.ru — там всё."
    assert clean("**Важно**: # заголовок") == "Важно: заголовок"


def test_a_vosk_voice_is_named_with_its_speaker():
    from orpheus.config import Config
    config = Config(voice="vosk-model-tts-ru-0.7-multi:3")
    assert config.voice_dir.name == "vosk-model-tts-ru-0.7-multi" and config.voice_speaker == 3
    config = Config(voice="vits-piper-ru_RU-ruslan-medium")
    assert config.voice_dir.name == "vits-piper-ru_RU-ruslan-medium" and config.voice_speaker == 0


def test_latin_is_said_in_russian_letters_for_vosk():
    from orpheus.speech import russian_letters
    assert russian_letters("Подготовка в SOS по физике") == "Подготовка в эс о эс по физике"
    assert russian_letters("пароль от Wi-Fi") == "пароль от ви-фи"


def test_a_sentence_ending_the_piece_is_said_at_once_unless_a_number_may_go_on():
    s = Sentences(first_clause=20)
    assert s.feed("Сейчас поищу.") == ["Сейчас поищу."]  # not after the search is done
    s = Sentences()
    assert s.feed("Будет 3.") == []
