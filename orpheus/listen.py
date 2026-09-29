"""Speak into the microphone and see what Orpheus hears: python -m orpheus.listen"""

import time

from .audio import phrases
from .config import SAMPLE_RATE, Config
from .stt import Recognizer


def main():
    config = Config()
    t = time.monotonic()
    rec = Recognizer(config)
    print("модель загружена за %.1f с, говори (Ctrl+C — выход)" % (time.monotonic() - t), flush=True)
    try:
        for samples in phrases(config):
            t = time.monotonic()
            text = rec.transcribe(samples)
            took = time.monotonic() - t
            if text:
                print("[%.1f с речи, %.2f с] %s" % (len(samples) / SAMPLE_RATE, took, text), flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
