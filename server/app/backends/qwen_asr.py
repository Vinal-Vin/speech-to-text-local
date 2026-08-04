"""Qwen3-ASR backend (Alibaba) -- multilingual, dialect and singing-voice capable.

IMPORTANT expectation-setting for CPU-only use:

Qwen3-ASR is an autoregressive LLM-style decoder. Its headline figures
(~92 ms TTFT, RTF ~0.064) are measured with vLLM on a GPU. On CPU it pays a
forward pass per generated token -- exactly the cost Parakeet's transducer
decoder avoids -- so expect materially higher latency here. It is provided so
you can measure that difference on your own hardware and speech rather than
take anyone's word for it. See scripts/benchmark.py.

Requires: docker compose build --build-arg INSTALL_QWEN=1
"""

from __future__ import annotations

import logging

import numpy as np

from .base import ASRBackend

log = logging.getLogger(__name__)


class QwenASRBackend(ASRBackend):
    def __init__(self, settings) -> None:
        self.settings = settings
        self.name = f"qwen:{settings.model}"
        self._model = None

    def load(self) -> None:
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:  # pragma: no cover - depends on build args
            raise RuntimeError(
                "qwen-asr is not installed in this image. Rebuild with:\n"
                "  docker compose build --build-arg INSTALL_QWEN=1"
            ) from exc

        if self.settings.asr_threads > 0:
            torch.set_num_threads(self.settings.asr_threads)

        # The upstream example uses bfloat16 on cuda. On CPU, bfloat16 falls
        # back to slow or unimplemented kernels for several ops, so float32 is
        # both faster and safer here.
        log.info("loading qwen3-asr model=%s (cpu, float32)", self.settings.model)
        self._model = Qwen3ASRModel.from_pretrained(
            self.settings.model,
            dtype=torch.float32,
            device_map="cpu",
            max_inference_batch_size=1,  # dictation is strictly one utterance at a time
            max_new_tokens=256,
        )
        log.info("qwen3-asr ready")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if self._model is None:
            raise RuntimeError("QwenASRBackend.load() was not called")

        # transcribe() accepts a (waveform, sample_rate) tuple, so we can pass
        # the array straight through with no temp file.
        results = self._model.transcribe(
            audio=(audio, sample_rate),
            language=self.settings.asr_language,
        )
        if not results:
            return ""
        return (results[0].text or "").strip()
