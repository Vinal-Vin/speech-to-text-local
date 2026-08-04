"""The one interface every ASR backend implements.

Keeping this deliberately tiny is what makes the backends swappable: the
server only ever needs `load()` once at startup and `transcribe()` per
request. Anything model-specific stays inside the backend module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class ASRBackend(ABC):
    """Transcribes 16 kHz mono float32 audio to text."""

    #: Human-readable id reported by /health, e.g. "parakeet:nemo-parakeet-tdt-0.6b-v3"
    name: str = "unknown"

    @abstractmethod
    def load(self) -> None:
        """Load model weights into memory.

        Called exactly once during application startup, never per request --
        loading takes seconds while inference takes well under one.
        """

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe mono float32 audio in [-1, 1]. Returns text (may be "")."""


def get_backend(name: str, settings) -> ASRBackend:
    """Import and construct a backend by name.

    Imports are deliberately deferred into this function. Each backend pulls a
    heavy and mutually exclusive dependency tree (ONNX Runtime vs CTranslate2
    vs torch), so a top-level import would force every user to install all of
    them. This way, selecting parakeet never touches the qwen or whisper stack
    -- and an uninstalled backend costs nothing until it is actually selected.
    """
    if name == "parakeet":
        from .parakeet import ParakeetBackend

        return ParakeetBackend(settings)
    if name == "whisper":
        from .whisper import WhisperBackend

        return WhisperBackend(settings)
    if name == "qwen":
        from .qwen_asr import QwenASRBackend

        return QwenASRBackend(settings)
    if name == "dummy":
        from .dummy import DummyBackend

        return DummyBackend(settings)
    raise ValueError(
        f"Unknown ASR_BACKEND {name!r}. Expected one of: parakeet, whisper, qwen, dummy."
    )
