"""python -m orpheus [voice|chat|listen|warmup|memory|notes]"""

import argparse
import os
import re
import sys
import time

from .config import Config
from .llm import LLMError
from .memory import Memory


def _brain(config):
    from .brain import Brain

    from .planner import from_config as planner_tools
    from .vectors import load_embedder

    embedder = load_embedder(config.models)
    from .web import online

    weather, search = online(config)
    brain = Brain(config, Memory(config.db, embedder), locked=True, planner=planner_tools(config, embedder),
                  weather=weather, search=search)
    key = os.environ.get("ORPHEUS_PERSONAL_KEY")  # the same key as on the phone, base64
    if key:
        from .memory import SealedMemory
        from .secure import parse_key

        brain.unlock(SealedMemory(config.personal_db, parse_key(key), embedder))
    return brain


def _warmup(brain):
    t = time.monotonic()
    stats = brain.warmup()
    print("модель %s готова за %.1f с (промпт %s токенов)" % (
        brain.config.model, time.monotonic() - t, stats.get("prompt_eval_count", "?")), flush=True)


def _stats_line(stats, first, total):
    evals = stats.get("prompt_eval_count", 0)
    rate = stats.get("eval_count", 0) / max(stats.get("eval_duration", 1) / 1e9, 1e-9)
    return "  [первое слово %.2f с, всего %.1f с, прочитано токенов промпта %s, %.1f ток/с]" % (
        first, total, evals, rate)


def cmd_chat(config, args):
    brain = _brain(config)
    _warmup(brain)
    print("пиши; /mem — память, /notes — заметки, /reset — новый разговор, /quit — выход")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            return
        if text == "/reset":
            brain.reset()
            print("новый разговор")
            continue
        if text == "/mem":
            cmd_memory(config, args, brain.memory)
            continue
        if text == "/notes":
            cmd_notes(config, args, brain.memory)
            continue
        t = time.monotonic()
        first = None
        try:
            for piece in brain.ask(text):
                first = first or time.monotonic() - t
                print(piece, end="", flush=True)
        except LLMError as exc:
            print("ошибка: %s" % exc)
            continue
        print()
        if args.stats:
            print(_stats_line(brain.stats, first or 0, time.monotonic() - t))


def _near(a, b):
    """At most one letter apart: the recogniser hears «Орфей» as «Арфей», «Орфея», «Морфей»."""
    if abs(len(a) - len(b)) > 1:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= 1


def addressed(text, wake, within=3):
    """'Орфей, который час?' / 'Скажи, Арфей, который час' -> 'который час?'.
    None if the wake word is not among the first few words."""
    norm = lambda w: w.lower().replace("ё", "е")
    target = norm(wake)
    for m in list(re.finditer(r"\w+", text))[:within]:
        if _near(norm(m.group()), target):
            return text[m.end():].lstrip(" ,.!?:;-—").strip()
    return None


def cmd_voice(config, args):
    from .audio import Microphone
    from .speech import Speaker
    from .stt import Recognizer

    rec = Recognizer(config)
    speaker = Speaker(config)
    brain = _brain(config)
    _warmup(brain)
    mic = Microphone(config)
    wake = "" if config.wake.strip() in ("", "-") else config.wake.strip()
    print("слушаю%s (Ctrl+C — выход)" % (" слово «%s»" % wake if wake else ""), flush=True)
    last_reply = 0.0
    try:
        while True:
            samples = mic.phrase()
            t = time.monotonic()
            text = rec.transcribe(samples)
            heard = time.monotonic() - t
            if not text:
                continue
            if wake and time.monotonic() - last_reply > config.follow_up:
                text = addressed(text, wake)
                if text is None:
                    continue
                if not text:
                    mic.mute()
                    speaker.say("Слушаю.")
                    mic.unmute()
                    last_reply = time.monotonic()
                    continue
            print("ты: %s" % text, flush=True)
            mic.mute()
            first = None
            print("орфей: ", end="", flush=True)
            try:
                for piece in brain.ask(text):
                    first = first or time.monotonic() - t
                    print(piece, end="", flush=True)
                    speaker.feed(piece)
            except LLMError as exc:
                print("ошибка: %s" % exc, end="")
                speaker.feed("Не могу связаться с моделью.")
            print(flush=True)
            if args.stats:
                print("  [распознавание %.2f с]%s" % (heard, _stats_line(brain.stats, first or 0, time.monotonic() - t)))
            speaker.finish()
            mic.unmute()
            last_reply = time.monotonic()
    except KeyboardInterrupt:
        print()
    finally:
        mic.close()


def cmd_listen(config, args):
    from .listen import main

    main()


def cmd_warmup(config, args):
    _warmup(_brain(config))


def cmd_memory(config, args, memory=None):
    memory = memory or Memory(config.db)
    if getattr(args, "forget", None):
        print("забыто" if memory.forget(args.forget) else "нет такого")
        return
    facts = memory.facts()
    for fact_id, text in facts:
        print("[%d] %s" % (fact_id, text))
    if not facts:
        print("память пуста")


def cmd_notes(config, args, memory=None):
    memory = memory or Memory(config.db)
    query = " ".join(getattr(args, "query", None) or [])
    notes = memory.find_notes(query, limit=20)
    for n in notes:
        print("[%d] %s%s" % (n["id"], n["title"] + ": " if n["title"] else "", n["text"]))
    if not notes:
        print("заметок нет")


def cmd_server(config, args):
    from .server import main as serve

    serve(config)


def cmd_voiceprint(config, args):
    """The owner's voice on the laptop's side: show it, reset it, or record / check it from WAV files."""
    from .voiceprint import NEEDED, OwnerCheck

    owner = OwnerCheck(config.models, config.voiceprint, "log" if config.speaker == "off" else config.speaker,
                       config.speaker_threshold)
    if owner.problem:
        sys.exit(owner.problem)
    vp = owner.voiceprint
    if args.action == "reset":
        vp.reset()
        print("голос владельца сброшен")
        return
    if args.action in ("enroll", "score"):
        import numpy as np
        import soundfile as sf

        for name in args.files:
            samples, rate = sf.read(name, dtype="float32")
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            if rate != 16000:
                samples = np.interp(np.linspace(0, len(samples) - 1, int(len(samples) * 16000 / rate)),
                                    np.arange(len(samples)), samples).astype(np.float32)
            if args.action == "enroll":
                why = owner.enroll(samples)
                print("%s: %s" % (name, why or "записано"))
            else:
                score, is_owner = owner.judge(samples)
                print("%s: %s" % (name, "не оценить" if score is None else "%.2f — %s" % (score, "владелец" if is_owner else "не владелец")))
    print("голос владельца: %d фраз из %d нужных, режим %s, порог %.2f" % (vp.count, NEEDED, config.speaker, config.speaker_threshold))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="orpheus", description="Орфей — голосовой помощник")
    parser.add_argument("--stats", action="store_true", help="печатать задержки и скорость")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("voice", help="голосовой режим через микрофон этой машины (по умолчанию)")
    sub.add_parser("server", help="сервер для телефона (docs/android-protocol.md)")
    sub.add_parser("chat", help="текстовый чат в терминале")
    sub.add_parser("listen", help="только распознавание речи")
    sub.add_parser("warmup", help="загрузить модель в Ollama и прогреть кэш")
    mem = sub.add_parser("memory", help="что Орфей помнит")
    mem.add_argument("--forget", type=int, metavar="ID")
    notes = sub.add_parser("notes", help="заметки; с аргументами — поиск")
    notes.add_argument("query", nargs="*")
    vp = sub.add_parser("voiceprint", help="голос владельца: show | reset | enroll WAV… | score WAV…")
    vp.add_argument("action", choices=["show", "reset", "enroll", "score"], nargs="?", default="show")
    vp.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    config = Config()
    handler = {"server": cmd_server, "chat": cmd_chat, "listen": cmd_listen, "warmup": cmd_warmup,
               "memory": cmd_memory, "notes": cmd_notes, "voiceprint": cmd_voiceprint}.get(args.cmd, cmd_voice)
    try:
        handler(config, args)
    except LLMError as exc:
        sys.exit("ошибка: %s" % exc)


if __name__ == "__main__":
    main()
