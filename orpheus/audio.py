"""Microphone capture cut into phrases by Silero VAD."""

import queue
import sys

import numpy as np
import sherpa_onnx

from .config import SAMPLE_RATE, Config

WINDOW = 512  # samples per VAD step at 16 kHz


def make_vad(config: Config):
    if not config.vad_model.exists():
        sys.exit("нет %s, запусти scripts/download_models.sh" % config.vad_model)
    vad_config = sherpa_onnx.VadModelConfig()
    vad_config.silero_vad.model = str(config.vad_model)
    vad_config.silero_vad.threshold = config.vad_threshold
    vad_config.silero_vad.min_silence_duration = config.silence
    vad_config.silero_vad.min_speech_duration = config.min_speech
    vad_config.silero_vad.max_speech_duration = config.max_speech
    vad_config.sample_rate = SAMPLE_RATE
    vad_config.num_threads = 1
    return sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=config.max_speech + 10)


class Microphone:
    """An always-open input stream. While muted (Orpheus is speaking) the sound is dropped,
    so Orpheus does not hear and answer itself."""

    def __init__(self, config: Config | None = None):
        import sounddevice as sd

        self.config = config or Config()
        self.vad = make_vad(self.config)
        self.chunks = queue.Queue()
        self.muted = False
        device = self.config.device
        device = int(device) if device and device.isdigit() else device
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=WINDOW, device=device, callback=self._callback)
        self.stream.start()
        self.pending = np.zeros(0, dtype=np.float32)

    def _callback(self, indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        if not self.muted:
            self.chunks.put(indata[:, 0].copy())

    def mute(self):
        self.muted = True

    def unmute(self):
        """Start listening again from a clean state: drop whatever was queued meanwhile."""
        while not self.chunks.empty():
            self.chunks.get_nowait()
        self.vad.reset()
        self.pending = np.zeros(0, dtype=np.float32)
        self.muted = False

    def phrase(self):
        """Block until the next spoken phrase and return it as float32 samples."""
        while True:
            if not self.vad.empty():
                samples = np.array(self.vad.front.samples, dtype=np.float32)
                self.vad.pop()
                return samples
            self.pending = np.concatenate([self.pending, self.chunks.get()])
            while len(self.pending) >= WINDOW:
                self.vad.accept_waveform(self.pending[:WINDOW])
                self.pending = self.pending[WINDOW:]

    def close(self):
        self.stream.close()


def phrases(config: Config | None = None):
    """Yield one float32 array per spoken phrase, forever."""
    mic = Microphone(config)
    try:
        while True:
            yield mic.phrase()
    finally:
        mic.close()
