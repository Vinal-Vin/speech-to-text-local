"""Audio decoding: whatever the client uploads -> 16 kHz mono float32."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf

TARGET_SR = 16000


class AudioError(ValueError):
    """Raised when uploaded audio cannot be decoded or is out of bounds."""


def decode(data: bytes, max_seconds: float | None = None) -> tuple[np.ndarray, int]:
    """Decode WAV/FLAC/OGG bytes to mono float32 at 16 kHz.

    Returns (waveform, sample_rate). The client already sends 16 kHz mono, so
    the downmix and resample paths below are defensive -- they exist so a
    different mic or a curl'd test file cannot silently produce garbage.
    """
    try:
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AudioError(f"could not decode audio: {exc}") from exc

    if audio.size == 0:
        raise AudioError("audio contains no samples")

    # Downmix to mono.
    audio = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]

    if max_seconds is not None and len(audio) / sr > max_seconds:
        raise AudioError(
            f"audio is {len(audio) / sr:.1f}s, exceeding the {max_seconds:.0f}s limit"
        )

    if sr != TARGET_SR:
        audio = _resample(audio, sr, TARGET_SR)
        sr = TARGET_SR

    return np.ascontiguousarray(audio, dtype=np.float32), sr


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resample.

    Deliberately dependency-free: adding scipy/librosa to the base image just
    for a fallback path that the real client never hits isn't worth ~100 MB.
    """
    duration = len(audio) / src_sr
    dst_len = int(round(duration * dst_sr))
    if dst_len <= 0:
        raise AudioError("audio too short to resample")
    src_idx = np.linspace(0, len(audio) - 1, num=dst_len, dtype=np.float64)
    return np.interp(src_idx, np.arange(len(audio)), audio).astype(np.float32)


def to_pcm16_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 [-1, 1] to little-endian 16-bit PCM (what Silero wants)."""
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
