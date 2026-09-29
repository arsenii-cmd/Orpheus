"""Russian speech to text: GigaAM v3 (RNNT with punctuation) through sherpa-onnx, CPU only."""

import sys

import numpy as np
import sherpa_onnx

from .config import SAMPLE_RATE, Config


class Recognizer:
    def __init__(self, config: Config | None = None):
        config = config or Config()
        d = config.asr_dir
        files = [d / "encoder.int8.onnx", d / "decoder.onnx", d / "joiner.onnx", d / "tokens.txt"]
        missing = [str(f) for f in files if not f.exists()]
        if missing:
            sys.exit("нет файлов модели, запусти scripts/download_models.sh:\n  " + "\n  ".join(missing))
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(files[0]),
            decoder=str(files[1]),
            joiner=str(files[2]),
            tokens=str(files[3]),
            model_type="nemo_transducer",
            decoding_method="greedy_search",
            num_threads=config.threads,
        )

    def transcribe(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> str:
        """Float32 mono samples in [-1, 1] -> text."""
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, np.ascontiguousarray(samples, dtype=np.float32))
        self._rec.decode_stream(stream)
        return stream.result.text.strip()
