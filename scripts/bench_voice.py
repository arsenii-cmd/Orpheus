"""Orpheus end to end, as the phone hears it: each phrase is spoken by a Piper voice, recognised by
GigaAM, answered (by a scenario of the program or by the model) and the first sentence of the answer
synthesized - the time a person waits for the first sound, less the network. It also shows how the
phrases come out of the recogniser, which is what the templates have to match.

    .venv/bin/python scripts/bench_voice.py [--voice ruslan|irina|both]

Nothing real is touched: a throwaway plannerd (see bench_tools.py) and an in-memory memory.
"""

import argparse
import dataclasses
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_tools import BASE, Scratch  # noqa: E402

from orpheus.brain import Brain  # noqa: E402
from orpheus.config import Config  # noqa: E402
from orpheus.memory import Memory  # noqa: E402
from orpheus.planner import Planner, PlannerTools  # noqa: E402
from orpheus.speech import Sentences, Voice  # noqa: E402
from orpheus.stt import Recognizer  # noqa: E402
from orpheus.vectors import load_embedder  # noqa: E402
from orpheus.web import online  # noqa: E402

# as a person says them (numbers in words); the scenario expected, or "модель"
PHRASES = [
    ("который час", "time"),
    ("сколько сейчас времени", "time"),
    ("какое сегодня число", "date"),
    ("какой день недели будет первого октября", "date_of"),
    ("сколько дней до нового года", "days_until"),
    ("сколько будет пятнадцать умножить на тридцать семь", "calc"),
    ("сколько будет двадцать процентов от трёх тысяч", "calc"),
    ("сделай заметку купить молоко и хлеб", "note_add"),
    ("сделай заметку про ремонт купить краску и валик", "note_add"),
    ("запиши код от домофона четыре пять два один", "note_add"),
    ("прочитай мои заметки", "note_list"),
    ("найди заметку про вайфай", "note_find"),
    ("запомни что я люблю кофе без сахара", "remember"),
    ("что у меня завтра", "plan_ask"),
    ("какие планы на неделю", "plan_ask"),
    ("что у меня дальше", "plan_next"),
    ("добавь на завтра в восемь вечера тренировку", "plan_add"),
    ("напомни через два часа выпить таблетку", "plan_add"),
    ("напомни в пятницу в половине восьмого позвонить маме", "plan_add"),
    ("запланируй на третье октября в пятнадцать тридцать стоматолога", "plan_add"),
    ("перенеси физику на завтра в три часа дня", "plan_move"),
    ("отметь хлеб купленным", "plan_done"),
    ("удали физику", "plan_delete"),
    ("привет", "greet"),
    ("спасибо", "thanks"),
    ("как дела", "how_are_you"),
    ("что ты умеешь", "capabilities"),
    ("повтори", "repeat"),
    ("подбрось монетку", "coin"),
    ("какая погода завтра", "weather"),
    ("какая погода в казани", "weather"),
    ("а завтра", "weather"),
    ("что у меня завтра и какая будет погода", "plan_ask+weather"),
    ("сколько у меня завтра дел", "plan_ask"),
    ("добавь на завтра в двенадцать созвон с петей", "plan_add"),
    ("перенеси его на четырнадцать", "plan_move"),
    ("нет лучше на пятнадцать", "plan_fix"),
    ("удали его", "plan_delete"),
    ("да", "confirm"),
    ("меня зовут иван", "my_name"),
    ("как меня зовут", "ask_name"),
    ("корень из ста сорока четырёх", "calc"),
    ("включи музыку", "music"),
    ("есть ли завтра занятие по русскому в десять", "plan_ask"),
    ("какой у меня пароль от вайфая", "модель"),
    ("расскажи короткий анекдот", "модель"),
    ("почему небо голубое", "модель"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="ruslan", choices=["ruslan", "irina", "both"])
    ap.add_argument("--plannerd", default=str(Path.home() / ".local/bin/plannerd"))
    args = ap.parse_args()
    config = Config()
    recognizer = Recognizer(config)
    voices = {"ruslan": Voice(config), "irina": Voice(dataclasses.replace(config, voice=config.voice_female))}
    speakers = list(voices) if args.voice == "both" else [args.voice]
    reply_voice = voices["ruslan"]
    embedder = load_embedder(config.models)
    scratch = Scratch(args.plannerd)
    rows = []
    try:
        Brain(config, Memory(":memory:"), planner=PlannerTools(Planner(BASE))).warmup()
        weather, search = online(config)  # through ORPHEUS_PROXY, as the server does
        brain = Brain(config, Memory(":memory:", embedder), planner=PlannerTools(Planner(BASE), embedder=embedder),
                      weather=weather, search=search)
        brain.briefing = False  # no morning's day summary: the checks are about the phrase
        for phrase, expected in PHRASES:
            for who in speakers:
                samples, rate = voices[who].synth(phrase)
                t0 = time.monotonic()
                heard = recognizer.transcribe(samples, rate)
                t_stt = time.monotonic() - t0
                splitter = Sentences(first_clause=20)
                first_sentence, reply, t_first_text = None, "", None
                # read to the end, as the server does: Brain notes the turn (the reply to "повтори", what
                # "его" is) only after its last piece; the time is taken at the first words all the same
                for piece in brain.ask(heard):
                    t_first_text = t_first_text or time.monotonic()
                    reply += piece
                    if first_sentence is None:
                        done = splitter.feed(piece)
                        if done:
                            first_sentence = done[0]
                t_brain = (t_first_text or time.monotonic()) - t0 - t_stt
                if first_sentence is None:
                    rest = splitter.flush()
                    first_sentence = rest[0] if rest else ""
                t1 = time.monotonic()
                if first_sentence:
                    reply_voice.synth(first_sentence)
                t_tts = time.monotonic() - t1
                by = brain.handled
                ok = by == expected
                rows.append({"ok": ok, "by": by, "stt": t_stt, "brain": t_brain, "tts": t_tts,
                             "first_sound": t_stt + t_brain + t_tts})
                print("%s %-11s %5.2f + %5.2f + %5.2f = %5.2f с  «%s» -> %s" % (
                    "✓" if ok else "✗ ждали %s," % expected, by, t_stt, t_brain, t_tts, t_stt + t_brain + t_tts,
                    heard, (first_sentence or reply)[:70]), flush=True)
    finally:
        scratch.close()
    ok = sum(r["ok"] for r in rows)
    print("\nпонято верно: %d из %d" % (ok, len(rows)))
    for label, part in (("программа", [r for r in rows if r["by"] != "модель"]), ("модель", [r for r in rows if r["by"] == "модель"])):
        if part:
            print("%s (%d): распознавание %.2f с, ответ %.3f с, первый звук медиана %.2f с, максимум %.2f с" % (
                label, len(part), statistics.median(r["stt"] for r in part), statistics.median(r["brain"] for r in part),
                statistics.median(r["first_sound"] for r in part), max(r["first_sound"] for r in part)))


if __name__ == "__main__":
    main()
