"""faster-whisper (CTranslate2) backend -- the known-good fallback.

Slower than Parakeet on CPU because of its autoregressive decoder, but it is
mature, covers 99 languages, and is the easiest of the three to reason about
when something looks wrong. Useful as a correctness reference.

Requires: docker compose build --build-arg INSTALL_WHISPER=1
"""

from __future__ import annotations

import logging

import numpy as np

from .base import ASRBackend

log = logging.getLogger(__name__)


class WhisperBackend(ASRBackend):
    def __init__(self, settings) -> None:
        self.settings = settings
        self.name = f"whisper:{settings.model}"
        self._model = None

    def load(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on build args
            raise RuntimeError(
                "faster-whisper is not installed in this image. Rebuild with:\n"
                "  docker compose build --build-arg INSTALL_WHISPER=1"
            ) from exc

        # int8 is the CPU sweet spot; float32 roughly doubles latency.
        compute_type = self.settings.asr_quantization or "int8"

        log.info("loading whisper model=%s compute_type=%s", self.settings.model, compute_type)
        self._model = WhisperModel(
            self.settings.model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=self.settings.asr_threads,
        )
        log.info("whisper ready")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if self._model is None:
            raise RuntimeError("WhisperBackend.load() was not called")
        if sample_rate != 16000:
            raise ValueError(f"whisper backend requires 16 kHz audio, got {sample_rate}")

        segments, _info = self._model.transcribe(
            audio,
            language=self.settings.asr_language,
            beam_size=1,  # greedy: dictation favours latency over the last 1% of accuracy
            # We already run VAD ourselves upstream, so leave it off here to
            # avoid trimming the audio twice.
            vad_filter=False,
            condition_on_previous_text=False,  # avoids repetition loops on short clips
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
