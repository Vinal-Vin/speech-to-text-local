"""Microphone capture via sounddevice (PortAudio).

Records into an in-memory list of frames while the hotkey is held, then
encodes to WAV bytes. Nothing touches disk -- your dictation never lands in a
temp file.
"""

from __future__ import annotations

import io
import logging
import threading
import wave

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        device: int | None = None,
        max_seconds: float = 300.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.max_seconds = max_seconds
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._max_frames = int(sample_rate * max_seconds)
        self._n_samples = 0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            # Overflows are common and usually harmless; log once at debug.
            log.debug("audio status: %s", status)
        with self._lock:
            if self._n_samples >= self._max_frames:
                return  # hard cap: a stuck key must not exhaust memory
            self._frames.append(indata.copy())
            self._n_samples += len(indata)

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._frames = []
            self._n_samples = 0
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
            blocksize=0,  # let PortAudio pick; lowest-latency default
        )
        self._stream.start()
        log.debug("recording started")

    def stop(self) -> np.ndarray:
        """Stop capture and return the recorded mono float32 waveform."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.warning("error closing audio stream: %s", exc)

        with self._lock:
            frames = self._frames
            self._frames = []
            self._n_samples = 0

        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1).astype(np.float32)

    def duration_of(self, audio: np.ndarray) -> float:
        return len(audio) / self.sample_rate


def to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Encode mono float32 [-1, 1] as a 16-bit PCM WAV in memory."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def list_devices() -> str:
    return str(sd.query_devices())
