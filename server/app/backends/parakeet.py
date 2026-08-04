"""NVIDIA Parakeet TDT via onnx-asr -- the default CPU backend.

Parakeet uses a transducer (TDT) decoder rather than the autoregressive
encoder-decoder that Whisper and Qwen3-ASR use. It emits tokens without a
per-token forward pass over the whole decoder, which is the specific reason
it is so much faster on CPU. That is why it ships as the default here.
"""

from __future__ import annotations

import logging

import numpy as np

from .base import ASRBackend

log = logging.getLogger(__name__)


class ParakeetBackend(ASRBackend):
    def __init__(self, settings) -> None:
        self.settings = settings
        self.name = f"parakeet:{settings.model}"
        self._model = None

    def load(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        sess_options = None
        if self.settings.asr_threads > 0:
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = self.settings.asr_threads
            sess_options.inter_op_num_threads = 1

        log.info(
            "loading parakeet model=%s quantization=%s",
            self.settings.model,
            self.settings.asr_quantization,
        )
        self._model = onnx_asr.load_model(
            self.settings.model,
            quantization=self.settings.asr_quantization,
            providers=["CPUExecutionProvider"],
            sess_options=sess_options,
        )
        log.info("parakeet ready")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if self._model is None:
            raise RuntimeError("ParakeetBackend.load() was not called")
        # onnx-asr accepts a float32 numpy waveform directly -- no temp file.
        result = self._model.recognize(audio, sample_rate=sample_rate)
        return (result or "").strip()
