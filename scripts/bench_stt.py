#!/usr/bin/env python3
"""Word error rate and speed on your own recordings.

Put pairs into a folder: phrase1.wav + phrase1.txt (what was actually said), ...
    python scripts/bench_stt.py samples/
"""

import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orpheus.stt import Recognizer  # noqa: E402


def words(text):
    text = text.lower().replace("ё", "е")
    return re.findall(r"[\w-]+", text)


def edit_distance(ref, hyp):
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    wavs = sorted(p for p in folder.glob("*.wav") if p.with_suffix(".txt").exists())
    if not wavs:
        sys.exit("в %s нет пар .wav + .txt" % folder)
    rec = Recognizer()
    errors = total = 0
    audio_sec = decode_sec = 0.0
    for wav in wavs:
        samples, sr = sf.read(wav, dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        t = time.monotonic()
        hyp = rec.transcribe(samples, sr)
        decode_sec += time.monotonic() - t
        audio_sec += len(samples) / sr
        ref = wav.with_suffix(".txt").read_text(encoding="utf-8")
        e, n = edit_distance(words(ref), words(hyp)), len(words(ref))
        errors, total = errors + e, total + n
        print("%-24s WER %5.1f%%  %s" % (wav.name, 100 * e / max(n, 1), hyp))
    print("\nитого: WER %.1f%% на %d словах, RTF %.3f (%.1f с аудио за %.1f с)"
          % (100 * errors / max(total, 1), total, decode_sec / audio_sec, audio_sec, decode_sec))


if __name__ == "__main__":
    main()
