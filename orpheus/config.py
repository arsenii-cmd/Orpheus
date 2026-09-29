"""Paths and tunables. Every value can be overridden by an ORPHEUS_* environment variable."""

import os
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_RATE = 16000
ASR_MODEL = "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"


def _env(name, default, cast=str):
    value = os.environ.get("ORPHEUS_" + name)
    return default if value in (None, "") else cast(value)


def _from_env(name, default, cast=str):
    return field(default_factory=lambda: _env(name, default, cast))


DATA_DIR = Path.home() / ".local/share/orpheus"


@dataclass
class Config:
    models: Path = _from_env("MODELS", DATA_DIR / "models", lambda v: Path(v).expanduser())
    db: Path = _from_env("DB", DATA_DIR / "orpheus.db", lambda v: Path(v).expanduser())
    # "Личное" lives in a file of its own, encrypted with a key only the phone keeps (secure.py)
    personal_db: Path = _from_env("PERSONAL_DB", DATA_DIR / "personal.db.enc", lambda v: Path(v).expanduser())
    threads: int = _from_env("THREADS", os.cpu_count() or 4, int)

    # Microphone and VAD: how long a pause ends a phrase, the shortest sound still counted as speech.
    device: str | None = _from_env("MIC", None)
    silence: float = _from_env("SILENCE", 0.6, float)
    min_speech: float = _from_env("MIN_SPEECH", 0.25, float)
    max_speech: float = _from_env("MAX_SPEECH", 20, float)
    vad_threshold: float = _from_env("VAD_THRESHOLD", 0.5, float)

    # Language model served by Ollama. num_ctx must stay the same on every request, or Ollama reloads the model.
    ollama: str = _from_env("OLLAMA", "http://127.0.0.1:11434")
    model: str = _from_env("MODEL", "huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M")
    num_ctx: int = _from_env("CTX", 4096, int)
    temperature: float = _from_env("TEMPERATURE", 0.6, float)
    llm_threads: int = _from_env("LLM_THREADS", 0, int)  # 0: let Ollama decide
    think: str = _from_env("THINK", "")  # "0"/"1" only for hybrid models such as qwen3:4b or Gemma 4 ("0": no hidden reasoning)
    # prompt tokens the GPU reads in one go: at 512, one go took ~10 s on the Vega 8 and the kernel reset the GPU
    # under it (twice: "ring comp_1.1.0 timeout"); at 128 each go is ~2 s
    batch: int = _from_env("BATCH", 128, int)

    # The Planner's local copy (plannerd serve --local-only on this laptop); "-" turns it off.
    planner: str = _from_env("PLANNER", "http://127.0.0.1:47211/api")

    # The internet (weather, search) only through this proxy (e.g. a local sing-box to an exit
    # node). Empty: straight out. "-": no internet at all.
    proxy: str = _from_env("PROXY", "")
    city: str = _from_env("CITY", "Москва")  # the weather when no city is named
    # SearXNG on this laptop (e.g. http://127.0.0.1:8888): searched first, it asks many engines through the
    # proxy itself; DuckDuckGo stays as the fallback (it shut the exit node out with a captcha now and then)
    searxng: str = _from_env("SEARXNG", "")

    # A conversation idle this long starts over with a fresh memory snapshot.
    idle_reset: float = _from_env("IDLE_RESET", 600, float)

    # Speech synthesis
    # Piper voices, compared by how well GigaAM recognises what they say (10 phrases):
    # irina 17% errors, ruslan 20%, dmitri 22%, denis 24% (and denis sounded unpleasant).
    # Or a Vosk TTS model with its speaker after a colon: "vosk-model-tts-ru-0.7-multi:3" (chosen
    # on a Ryzen 5 3500U; the heavier 0.9 took 7-10 s a phrase there).
    voice: str = _from_env("VOICE", "vits-piper-ru_RU-ruslan-medium")
    voice_female: str = _from_env("VOICE_FEMALE", "vits-piper-ru_RU-irina-medium")
    speed: float = _from_env("SPEED", 1.0, float)
    tts_threads: int = _from_env("TTS_THREADS", 1, int)

    # Whose voice (voiceprint.py): "off", "log" (answer everyone, journal the score) or "strict"
    # (answer the owner only). The threshold comes from the owner's scores in the journal.
    speaker: str = _from_env("SPEAKER", "log")
    speaker_threshold: float = _from_env("SPEAKER_THRESHOLD", 0.5, float)
    voiceprint: Path = _from_env("VOICEPRINT", DATA_DIR / "voiceprint.json", lambda v: Path(v).expanduser())

    # Wake word: when set, a phrase is answered only if it starts with it,
    # or if it comes within follow_up seconds after Orpheus finished speaking.
    wake: str = _from_env("WAKE", "орфей")  # "-" turns it off: then every phrase is answered
    follow_up: float = _from_env("FOLLOW_UP", 8, float)

    @property
    def asr_dir(self):
        return self.models / ASR_MODEL

    @property
    def vad_model(self):
        return self.models / "silero_vad.onnx"

    @property
    def voice_dir(self):
        return self.models / self.voice.split(":")[0]

    @property
    def voice_speaker(self):
        name, _, speaker = self.voice.partition(":")
        return int(speaker or 0)
