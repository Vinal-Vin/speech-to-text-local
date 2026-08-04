"""Server tests. No model weights or network access needed.

    pip install pytest
    pytest tests/ -v

Everything here runs against the `dummy` backend, so it works on any machine
including CI and locked-down environments with no Hugging Face access.
"""

from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.audio import AudioError, decode, to_pcm16_bytes  # noqa: E402
from app.config import DEFAULT_MODELS, Settings  # noqa: E402
from app.backends.base import get_backend  # noqa: E402
from app.vad import VAD  # noqa: E402


# --- helpers -----------------------------------------------------------------


def make_wav(audio: np.ndarray, sr: int = 16000, channels: int = 1) -> bytes:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def speechlike(seconds: float, sr: int = 16000) -> np.ndarray:
    """Amplitude-modulated harmonic stack -- enough structure for Silero."""
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sig = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate([130, 260, 520, 1040]))
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 4 * t))  # ~4 Hz syllable rate
    return (sig * envelope * 0.3).astype(np.float32)


# --- audio decoding ----------------------------------------------------------


def test_decode_mono_16k_roundtrip():
    original = speechlike(1.0)
    audio, sr = decode(make_wav(original))
    assert sr == 16000
    assert audio.dtype == np.float32
    assert len(audio) == len(original)
    assert np.abs(audio - original).max() < 1e-3  # 16-bit quantisation only


def test_decode_resamples_to_16k():
    audio, sr = decode(make_wav(speechlike(1.0, sr=48000), sr=48000))
    assert sr == 16000
    assert abs(len(audio) - 16000) <= 1


def test_decode_downmixes_stereo():
    mono = speechlike(0.5)
    stereo = np.stack([mono, mono], axis=1).reshape(-1)
    audio, _ = decode(make_wav(stereo, channels=2))
    assert audio.ndim == 1
    assert abs(len(audio) - len(mono)) <= 1


def test_decode_rejects_garbage():
    with pytest.raises(AudioError):
        decode(b"this is definitely not audio")


def test_decode_enforces_max_duration():
    with pytest.raises(AudioError, match="exceeding"):
        decode(make_wav(speechlike(3.0)), max_seconds=1.0)


def test_pcm16_conversion_clips():
    out = to_pcm16_bytes(np.array([2.0, -2.0, 0.0], dtype=np.float32))
    assert np.frombuffer(out, dtype="<i2").tolist() == [32767, -32767, 0]


# --- configuration -----------------------------------------------------------


@pytest.mark.parametrize("backend", ["parakeet", "whisper", "qwen", "dummy"])
def test_each_backend_resolves_its_own_default_model(backend):
    # Regression guard: switching ASR_BACKEND alone must not carry the
    # previous backend's model id across.
    assert Settings(asr_backend=backend).model == DEFAULT_MODELS[backend]


def test_explicit_model_overrides_default():
    assert Settings(asr_backend="whisper", asr_model="large-v3").model == "large-v3"


def test_empty_model_falls_back_to_default():
    # docker-compose passes ASR_MODEL as "" when unset.
    assert Settings(asr_backend="whisper", asr_model="").model == "small.en"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="Unknown ASR_BACKEND"):
        get_backend("nope", Settings())


# --- VAD ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def vad():
    v = VAD(threshold=0.5, pad_ms=300)
    v.load()
    return v


def test_vad_returns_none_for_pure_silence(vad):
    # This is what stops Whisper-family models hallucinating on silence.
    assert vad.trim(np.zeros(16000 * 3, dtype=np.float32)) is None


def test_vad_passes_short_audio_through(vad):
    tiny = np.zeros(100, dtype=np.float32)  # shorter than one 512-sample chunk
    assert vad.trim(tiny) is tiny


def test_vad_trims_silence_around_speech(vad):
    import soundfile as sf

    fixture = ROOT / "tests" / "fixtures" / "speech.wav"
    if not fixture.exists():
        pytest.skip("no real-speech fixture available")
    speech, sr = sf.read(fixture, dtype="float32")
    padded = np.concatenate([np.zeros(2 * sr, np.float32), speech, np.zeros(2 * sr, np.float32)])

    trimmed = vad.trim(padded, sr)
    assert trimmed is not None
    # Should shed most of the 4s of padding but keep essentially all speech.
    assert len(trimmed) < len(padded)
    assert len(trimmed) / sr >= (len(speech) / sr) * 0.9


# --- API ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(monkeypatch_module=None):
    from fastapi.testclient import TestClient

    import app.config as config_module
    import app.main as main_module

    config_module.settings.asr_backend = "dummy"
    main_module.settings.asr_backend = "dummy"
    with TestClient(main_module.app) as c:
        yield c


def test_health_reports_ready(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["backend"] == "dummy:no-model"


def test_transcribe_returns_text_for_speech(client):
    r = client.post("/transcribe", files={"file": ("a.wav", make_wav(speechlike(2.0)), "audio/wav")})
    assert r.status_code == 200
    body = r.json()
    assert body["text"]
    assert body["duration_s"] == pytest.approx(2.0, abs=0.05)


def test_transcribe_returns_empty_for_silence(client):
    silence = np.zeros(16000 * 2, dtype=np.float32)
    r = client.post("/transcribe", files={"file": ("s.wav", make_wav(silence), "audio/wav")})
    assert r.status_code == 200
    assert r.json()["text"] == ""


def test_transcribe_rejects_empty_upload(client):
    r = client.post("/transcribe", files={"file": ("e.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_transcribe_rejects_non_audio(client):
    r = client.post("/transcribe", files={"file": ("x.bin", b"not audio", "audio/wav")})
    assert r.status_code == 400
