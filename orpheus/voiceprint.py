"""Whose voice is it: Orpheus answers its owner, not the TV or the guests.

A speaker-embedding model (3D-Speaker CAM++ through sherpa-onnx, 28 MB, ~35 ms a phrase on the
laptop's CPU) turns a phrase into a vector; the owner's voiceprint is the mean of such vectors from
a few phrases he read once in the app. A phrase is the owner's when its vector points the same way
(cosine similarity above a threshold).

Measured here (28.09) with Piper voices as "people": one voice enrolled from 5 phrases scored
0.62–0.82 on 20 other phrases of its own (0.56 and up with noise at 10 dB), other voices 0.03–0.47,
a real reader 0.11–0.19. The default threshold 0.5 sits in that gap; real people and the phone's
microphone differ, so the server first runs in the "log" mode and the threshold is set from the
owner's real scores. It is a filter against other voices, not a lock: a recording of the owner passes.

Modes (ORPHEUS_SPEAKER): off · log (answer everyone, write the score to the journal) ·
strict (a phrase that is not the owner's gets no answer).
"""

import json
import os
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE

MODEL_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
NEEDED = 5                 # phrases read at enrollment
MIN_ENROLL_SEC = 1.2       # an enrollment phrase shorter than this says too little about the voice
MIN_JUDGE_SEC = 0.8        # shorter phrases ("да", "стоп") are not judged: they pass
ADAPT_MARGIN = 0.15        # a phrase this far above the threshold teaches the voiceprint a little
ADAPT_WEIGHT = 0.05
ADAPT_LEASH = 0.85         # the adapted voiceprint never drifts further than this from the enrolled one


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class Voiceprint:
    """The owner's voice as vectors, kept in a small JSON file. No model inside: easy to test."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.phrases: list[np.ndarray] = []
        self.current: np.ndarray | None = None
        self.adapted = 0
        # the microphone it was read into: "headset", "phone", "mixed", or None (not said: an older app)
        self.mic: str | None = None
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.phrases = [unit(p) for p in data.get("phrases", [])]
            self.current = unit(data["current"]) if data.get("current") else self.anchor
            self.adapted = int(data.get("adapted", 0))
            self.mic = data.get("mic")

    @property
    def count(self):
        return len(self.phrases)

    @property
    def ready(self):
        return self.count >= NEEDED

    @property
    def anchor(self):
        """The voiceprint as enrolled: the mean of the phrases read."""
        return unit(np.mean(self.phrases, axis=0)) if self.phrases else None

    def add(self, embedding, mic=None):
        if mic is not None:
            self.mic = mic if not self.phrases or self.mic in (None, mic) else "mixed"
        self.phrases.append(unit(embedding))
        self.current = self.anchor
        self.adapted = 0
        self.save()

    def reset(self):
        self.phrases, self.current, self.adapted, self.mic = [], None, 0, None
        if self.path.exists():
            self.path.unlink()

    def score(self, embedding) -> float | None:
        if not self.ready or self.current is None:
            return None
        return float(unit(embedding) @ self.current)

    def adapt(self, embedding, score, threshold):
        """Follow the owner's voice through the day and between rooms, slowly and on a leash."""
        if score is None or score < threshold + ADAPT_MARGIN:
            return False
        moved = unit((1 - ADAPT_WEIGHT) * self.current + ADAPT_WEIGHT * unit(embedding))
        if float(moved @ self.anchor) < ADAPT_LEASH:
            return False
        self.current = moved
        self.adapted += 1
        if self.adapted % 10 == 0:  # not a disk write on every phrase
            self.save()
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "version": 1, "model": MODEL_FILE, "adapted": self.adapted, "mic": self.mic,
            "phrases": [p.round(6).tolist() for p in self.phrases],
            "current": self.current.round(6).tolist() if self.current is not None else None,
        }))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


class OwnerCheck:
    """The model and the voiceprint together; what the server asks about each phrase."""

    def __init__(self, models_dir: Path, voiceprint_path: Path, mode="log", threshold=0.5, embed=None):
        self.mode = mode if mode in ("off", "log", "strict") else "log"
        self.threshold = threshold
        self.voiceprint = Voiceprint(voiceprint_path)
        self._embed = embed
        self.problem = None
        if self._embed is None and self.mode != "off":
            model = Path(models_dir) / MODEL_FILE
            if not model.exists():
                self.problem = "нет модели голоса %s (scripts/download_models.sh)" % model.name
                self.mode = "off"
            else:
                import sherpa_onnx

                extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                    sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=1))

                def embed(samples):
                    stream = extractor.create_stream()
                    stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
                    stream.input_finished()
                    return np.array(extractor.compute(stream), dtype=np.float32)

                self._embed = embed

    @property
    def active(self):
        return self.mode != "off" and self.voiceprint.ready

    def enroll(self, samples, mic=None) -> str | None:
        """Add one read phrase ([mic]: "headset" or "phone" when the app says); returns why it was not taken, or None."""
        if self._embed is None:
            return self.problem or "проверка голоса выключена"
        if len(samples) < MIN_ENROLL_SEC * SAMPLE_RATE:
            return "слишком коротко, прочитай фразу целиком"
        self.voiceprint.add(self._embed(samples), mic)
        return None

    def learns(self, mic) -> bool:
        """Should the voiceprint follow a phrase heard by [mic]? Only one of the microphone it was read into
        (another one scores the owner 0.30-0.50 and would drag it away). A print of an unknown microphone was
        read in the buds, as «Мой голос» asks; a phrase of an unknown microphone comes from an older app."""
        if mic is None:
            return True
        return mic == (self.voiceprint.mic or "headset")

    def judge(self, samples, learn=True):
        """(score or None, is it the owner?) — None and True when the phrase can't be judged.
        learn=False: the voiceprint does not follow this phrase (heard by another microphone than it
        was recorded with: the phone's own, 0.30-0.50 for the owner against a print from the buds)."""
        if not self.active or len(samples) < MIN_JUDGE_SEC * SAMPLE_RATE:
            return None, True
        embedding = self._embed(samples)
        score = self.voiceprint.score(embedding)
        owner = score is None or score >= self.threshold
        if owner and learn:
            self.voiceprint.adapt(embedding, score, self.threshold)
        return score, owner

    def refuses(self, owner: bool, strict: bool = False) -> bool:
        """Should this phrase go unanswered? strict: the phone asks for it for this phrase (in the buds)."""
        return (self.mode == "strict" or (strict and self.mode != "off")) and not owner
