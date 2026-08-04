"""Server configuration, driven entirely by environment variables.

Every knob here is settable from docker-compose.yml, so swapping the ASR
backend or model never requires a code change or an image rebuild.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BackendName = Literal["parakeet", "whisper", "qwen", "dummy"]

#: Sensible default model per backend. Without this, switching ASR_BACKEND
#: alone would carry the previous backend's model name across and fail in a
#: confusing way (e.g. faster-whisper being handed a parakeet model id).
DEFAULT_MODELS: dict[str, str] = {
    "parakeet": "nemo-parakeet-tdt-0.6b-v3",
    "whisper": "small.en",
    "qwen": "Qwen/Qwen3-ASR-0.6B",
    "dummy": "none",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # --- Backend selection ---
    # "parakeet" is the default: a transducer decoder, so it avoids the
    # per-token autoregressive cost that makes whisper/qwen slow on CPU.
    asr_backend: BackendName = "parakeet"

    # Model id, interpreted per-backend. Leave unset to get the sensible
    # default for whichever backend is selected -- see DEFAULT_MODELS below.
    # Setting it explicitly overrides that.
    #   parakeet -> onnx-asr model name  (nemo-parakeet-tdt-0.6b-v3)
    #   whisper  -> faster-whisper size  (small.en, medium, large-v3)
    #   qwen     -> HF repo id           (Qwen/Qwen3-ASR-0.6B)
    asr_model: str | None = None

    # Quantisation. int8 roughly halves both memory and latency on CPU for a
    # small accuracy cost -- the right default for dictation.
    asr_quantization: str | None = "int8"

    # Threads for ONNX Runtime / CTranslate2. 0 = let the runtime decide.
    # Worth tuning: more threads is not always faster for a 0.6B model.
    asr_threads: int = 0

    # Language hint. None = auto-detect. Setting this explicitly (e.g. "en")
    # is faster and avoids mis-detection on short utterances.
    asr_language: str | None = "en"

    # --- Voice activity detection ---
    # Trims leading/trailing silence before inference: less audio to process,
    # and it suppresses Whisper's habit of hallucinating text on pure silence.
    vad_enabled: bool = True
    vad_threshold: float = 0.5
    # Speech onset is detected slightly late (measured ~0.3s on real audio),
    # so pad the detected region or the first word gets clipped.
    vad_pad_ms: int = 300

    # --- Request limits ---
    max_audio_seconds: float = 300.0

    # --- Server ---
    host: str = "0.0.0.0"  # inside the container only; compose binds to loopback
    port: int = 8000
    log_level: str = "info"

    @property
    def model(self) -> str:
        """The model id to load: explicit ASR_MODEL, else the backend default."""
        return self.asr_model or DEFAULT_MODELS[self.asr_backend]


settings = Settings()
