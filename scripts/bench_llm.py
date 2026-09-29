#!/usr/bin/env python3
"""How fast Orpheus answers, and how much the prompt cache saves.

    python scripts/bench_llm.py

1. first request: loads the model (if not loaded yet) and reads the whole prompt;
2. the same conversation continued: only the new phrase is read (cache hit);
3. the same phrase with a changed system prompt: the whole prompt is read again (cache miss).
The difference between 2 and 3 is what the cache-friendly prompt layout buys on every phrase.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orpheus.brain import Brain  # noqa: E402
from orpheus.config import Config  # noqa: E402
from orpheus.memory import Memory  # noqa: E402

QUESTIONS = ["Привет, как дела?", "Сколько будет семнадцать умножить на три?", "Расскажи коротко, что такое чёрная дыра."]


def timed(brain, text):
    t = time.monotonic()
    first = None
    reply = ""
    for piece in brain.ask(text):
        first = first or time.monotonic() - t
        reply += piece
    s = brain.stats
    rate = s.get("eval_count", 0) / max(s.get("eval_duration", 1) / 1e9, 1e-9)
    prompt_rate = s.get("prompt_eval_count", 0) / max(s.get("prompt_eval_duration", 1) / 1e9, 1e-9)
    print("  первое слово %5.2f с | прочитано %4d ток. промпта (%.0f ток/с) | генерация %.1f ток/с | %s" % (
        first or 0, s.get("prompt_eval_count", 0), prompt_rate, rate, reply.strip()[:60]))
    return first or 0


def main():
    config = Config()
    brain = Brain(config, Memory(":memory:"))
    print("модель:", config.model)
    t = time.monotonic()
    brain.warmup()
    print("загрузка и прогрев: %.1f с\n" % (time.monotonic() - t))

    print("с кэшем (разговор продолжается):")
    hits = [timed(brain, q) for q in QUESTIONS]

    print("\nбез кэша (системный промпт каждый раз другой):")
    misses = []
    for i, q in enumerate(QUESTIONS):
        brain.reset()
        brain.system = "(%d) " % (i + time.time_ns()) + brain.system
        misses.append(timed(brain, q))

    print("\nв среднем до первого слова: с кэшем %.2f с, без кэша %.2f с" % (
        sum(hits) / len(hits), sum(misses) / len(misses)))


if __name__ == "__main__":
    main()
