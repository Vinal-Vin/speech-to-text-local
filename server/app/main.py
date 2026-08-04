"""FastAPI transcription service.

Deliberately a single endpoint doing one thing: audio in, text out. All the
model-specific complexity lives behind ASRBackend, so this file stays
readable and the client contract never changes when you swap models.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from .audio import AudioError, decode
from .backends.base import get_backend
from .config import settings
from .vad import VAD

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("stt")

state: dict = {"backend": None, "vad": None, "ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the model ONCE at startup. Loading costs seconds; inference costs
    # well under one. Doing this per request would dominate latency entirely.
    backend = get_backend(settings.asr_backend, settings)
    log.info("loading backend %s ...", backend.name)
    t0 = time.perf_counter()
    backend.load()
    log.info("backend ready in %.1fs", time.perf_counter() - t0)
    state["backend"] = backend

    if settings.vad_enabled:
        vad = VAD(threshold=settings.vad_threshold, pad_ms=settings.vad_pad_ms)
        vad.load()
        state["vad"] = vad

    state["ready"] = True
    yield
    state["ready"] = False


app = FastAPI(title="speech-to-text-local", version="1.0.0", lifespan=lifespan)


class Health(BaseModel):
    status: str
    backend: str
    vad: bool


class Transcription(BaseModel):
    text: str
    duration_s: float
    latency_ms: int
    backend: str


@app.get("/health", response_model=Health)
def health() -> Health:
    if not state["ready"]:
        # 503 rather than 200 so the client's "is the server up?" check is
        # honest while a multi-gigabyte model is still loading.
        raise HTTPException(status_code=503, detail="model still loading")
    return Health(status="ok", backend=state["backend"].name, vad=state["vad"] is not None)


@app.get("/info")
def info() -> dict:
    return {
        "backend": settings.asr_backend,
        "model": settings.model,
        "quantization": settings.asr_quantization,
        "language": settings.asr_language,
        "threads": settings.asr_threads,
        "vad_enabled": settings.vad_enabled,
        "max_audio_seconds": settings.max_audio_seconds,
        "ready": state["ready"],
    }


@app.post("/transcribe", response_model=Transcription)
async def transcribe(file: UploadFile = File(...)) -> Transcription:
    if not state["ready"]:
        raise HTTPException(status_code=503, detail="model still loading")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")

    t0 = time.perf_counter()
    try:
        audio, sr = decode(raw, max_seconds=settings.max_audio_seconds)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duration = len(audio) / sr

    if state["vad"] is not None:
        trimmed = state["vad"].trim(audio, sr)
        if trimmed is None or trimmed.size == 0:
            # No speech detected -- skip inference entirely. Saves a pointless
            # model run and, critically, avoids hallucinated text on silence.
            log.info("no speech detected in %.2fs of audio; returning empty", duration)
            return Transcription(
                text="",
                duration_s=duration,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                backend=state["backend"].name,
            )
        audio = trimmed

    try:
        text = state["backend"].transcribe(audio, sr)
    except Exception as exc:
        log.exception("transcription failed")
        raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc

    latency_ms = int((time.perf_counter() - t0) * 1000)
    log.info("transcribed %.2fs audio in %dms -> %d chars", duration, latency_ms, len(text))
    return Transcription(
        text=text, duration_s=duration, latency_ms=latency_ms, backend=state["backend"].name
    )
