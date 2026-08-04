"""A backend with no ML dependencies at all.

Exists so the whole pipeline -- client hotkey, capture, HTTP contract,
clipboard insertion -- can be developed and tested without downloading
multi-gigabyte weights, and so the test suite runs anywhere (including CI and
sandboxes with no Hugging Face access).

Set ASR_BACKEND=dummy. It reports the audio it received instead of
transcribing it, which is enough to prove every link in the chain works.
"""

from __future__ import annotations

import numpy as np

from .base import ASRBackend


class DummyBackend(ASRBackend):
    def __init__(self, settings) -> None:
        self.settings = settings
        self.name = "dummy:no-model"

    def load(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        duration = len(audio) / sample_rate
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        return f"[dummy backend] received {duration:.2f}s of audio at {sample_rate} Hz, peak {peak:.3f}"
