"""Text to speech: Piper voices through sherpa-onnx, or Vosk TTS, spoken sentence by sentence.

Synthesis and playback run in two threads, so sentence N+1 is synthesized while N is playing
and the first sentence starts as soon as the model has written it.
"""

import queue
import re
import sys
import threading

from .config import Config
from .numbers import speakable

END = re.compile(r"([.!?…]+[»\")]*)(\s+|$)|\n+")
MARKUP = re.compile(r"[*_#`>|~]+")
# the model once said "Данные из Википедии — [ru.wikipedia.org](https://ru.wikipedia.org/…)": the site is
# said by its name, never letter by letter as an address
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://(?:www\.)?([^/\s)]+)\S*")


def clean(text):
    text = URL.sub(r"\1", LINK.sub(r"\1", text))
    return re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", MARKUP.sub(" ", text))).strip()


PAUSE = re.compile(r"[,;:—–](?=\s)")


class Sentences:
    """Cut a stream of text pieces into sentences worth speaking on their own.

    With [first_clause], the very first piece may end at a comma or a dash once it is long enough:
    the reply starts sounding after its first clause instead of its whole first sentence
    (at ~9 tokens/s a long first sentence kept the owner waiting 3-4 s in silence)."""

    def __init__(self, min_len=12, max_len=180, first_clause=0):
        self.buf = ""
        self.min_len, self.max_len = min_len, max_len
        self.first_clause = first_clause  # 0: off; else the shortest first piece cut at a pause
        self.started = False

    def feed(self, piece):
        self.buf += piece
        out = []
        while True:
            cut = None
            for m in END.finditer(self.buf):
                if m.group(1) and not m.group(2) and re.search(r"\d$", self.buf[:m.start()]):
                    break  # "3." at the very end may still become "3.5" ("Сейчас поищу." is whole: said at once)
                if len(self.buf[:m.end()].strip()) >= self.min_len:
                    cut = m.end()
                    break
            if self.first_clause and not self.started and (cut is None or cut > self.first_clause * 3):
                # the first sound should come soon: a long first sentence starts with its first clause
                for m in PAUSE.finditer(self.buf, 0, cut or len(self.buf)):
                    if len(self.buf[:m.end()].strip()) >= self.first_clause:
                        cut = m.end()
                        break
            if cut is None and len(self.buf) > self.max_len:
                cut = max(self.buf.rfind(", ", 0, self.max_len), self.buf.rfind(" ", 0, self.max_len)) + 1 or self.max_len
            if cut is None:
                return out
            sentence = clean(self.buf[:cut])
            self.buf = self.buf[cut:]
            if sentence:
                out.append(sentence)
                self.started = True

    def flush(self):
        rest, self.buf = clean(self.buf), ""
        return [rest] if rest else []


class Voice:
    """Piper through sherpa-onnx, or Vosk TTS (a "vosk-model-tts-…" folder): text -> (float32 samples, sample rate)."""

    def __new__(cls, config: Config):
        if cls is Voice and config.voice_dir.name.startswith("vosk-model-tts"):
            return super().__new__(VoskVoice)
        return super().__new__(cls)

    def __init__(self, config: Config):
        import sherpa_onnx

        d = config.voice_dir
        onnx = sorted(d.glob("*.onnx"))
        if not onnx:
            sys.exit("нет голоса %s, запусти scripts/download_models.sh" % d)
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(onnx[0]), tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")),
                num_threads=config.tts_threads),
            max_num_sentences=1)
        self.tts = sherpa_onnx.OfflineTts(tts_config)
        self.speed = config.speed

    def synth(self, text):
        # numbers as words that agree with the text around them: espeak-ng would read bare digits
        audio = self.tts.generate(speakable(text), sid=0, speed=self.speed)
        return audio.samples, audio.sample_rate


# Vosk TTS knows Russian letters only: "SOS" in a reply broke it (KeyError 'o', 27.09) and the phone
# waited for the rest of the reply. Latin is said as Russian would: an acronym by its letters, a word as read.
LETTER_NAMES = dict(zip("abcdefghijklmnopqrstuvwxyz", ["эй", "би", "си", "ди", "и", "эф", "джи", "эйч", "ай", "джей", "кей",
                                                     "эл", "эм", "эн", "о", "пи", "кью", "ар", "эс", "ти", "ю", "ви",
                                                     "дабл ю", "экс", "уай", "зед"]))
LETTER_SOUNDS = dict(zip("abcdefghijklmnopqrstuvwxyz", ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н",
                                                      "о", "п", "к", "р", "с", "т", "у", "в", "в", "кс", "й", "з"]))
LATIN = re.compile(r"[A-Za-z]+")
# what Vosk has sounds for; the rest (quotes too: «команду "Стоп"» broke it, 27.09) is left out before it
NOT_RUSSIAN = re.compile(r"[^А-Яа-яЁё0-9\s.,!?;:\-—–]")


def russian_letters(text):
    """"в SOS по физике" -> "в эс о эс по физике"; "Wi-Fi" -> "ви-фи"."""
    def said(m):
        word = m.group(0)
        if word.isupper() and len(word) <= 5:
            return " ".join(LETTER_NAMES[c] for c in word.lower())
        return "".join(LETTER_SOUNDS[c] for c in word.lower())
    return LATIN.sub(said, text)


class VoskVoice(Voice):
    """Vosk TTS (alphacep): several speakers in one model, the one after the colon in ORPHEUS_VOICE.
    On the laptop 0.7-multi takes 0.2-0.8 s a phrase and ~0.7 GB of memory."""

    def __init__(self, config: Config):
        from vosk_tts import Model, Synth

        d = config.voice_dir
        if not (d / "model.onnx").exists():
            sys.exit("нет голоса %s" % d)
        model = Model(model_path=str(d))
        self.tts = Synth(model)
        self.rate = model.config["audio"]["sample_rate"]
        self.speaker = config.voice_speaker
        self.speed = config.speed

    def synth(self, text):
        import numpy as np

        said = NOT_RUSSIAN.sub(" ", russian_letters(speakable(text)))
        try:
            audio = self.tts.synth_audio(said, speaker_id=self.speaker, speech_rate=self.speed)
        except (KeyError, ValueError, IndexError):  # a sign it has no sound for: said without it
            audio = self.tts.synth_audio(NOT_RUSSIAN.sub(" ", said), speaker_id=self.speaker, speech_rate=self.speed)
        return np.asarray(audio, dtype=np.float32) / 32768, self.rate


class Speaker:
    """Speaks through this machine's own speakers."""

    def __init__(self, config: Config):
        self.voice = Voice(config)
        self.texts = queue.Queue()
        self.audio = queue.Queue()
        self.splitter = Sentences()
        threading.Thread(target=self._synth, daemon=True).start()
        threading.Thread(target=self._play, daemon=True).start()

    def feed(self, piece):
        for sentence in self.splitter.feed(piece):
            self.texts.put(sentence)

    def say(self, text):
        self.feed(text)
        self.finish()

    def finish(self):
        """The reply is complete: speak what is left and block until playback ends."""
        for sentence in self.splitter.flush():
            self.texts.put(sentence)
        self.texts.join()
        self.audio.join()

    def _synth(self):
        while True:
            text = self.texts.get()
            try:
                self.audio.put(self.voice.synth(text))
            except Exception as exc:
                print("tts: %s" % exc, file=sys.stderr)
            finally:
                self.texts.task_done()

    def _play(self):
        import numpy as np

        try:
            import sounddevice as sd
        except OSError as exc:  # no PortAudio: keep draining the queue so finish() never hangs
            print("звук недоступен: %s (sudo apt install libportaudio2)" % exc, file=sys.stderr)
            sd = None
        while True:
            samples, rate = self.audio.get()
            try:
                if sd:
                    sd.play(np.asarray(samples, dtype=np.float32), rate)
                    sd.wait()
            except Exception as exc:
                print("звук: %s" % exc, file=sys.stderr)
            finally:
                self.audio.task_done()
