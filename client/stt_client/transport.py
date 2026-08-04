"""HTTP transport to the containerised ASR server."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class ServerError(RuntimeError):
    """Raised with a message suitable for showing directly to the user."""


class Transport:
    def __init__(self, base_url: str, timeout_s: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        # Connect fast (the server is on loopback, so a slow connect means
        # it is simply not running) but allow a long read for inference.
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=3.0),
            headers={"User-Agent": "stt-local-client"},
        )

    def health(self) -> dict | None:
        """Return the health payload, or None if the server is unreachable."""
        try:
            r = self._client.get(f"{self.base_url}/health", timeout=5.0)
            if r.status_code == 200:
                return r.json()
            log.debug("health returned %s: %s", r.status_code, r.text[:200])
            return None
        except httpx.HTTPError as exc:
            log.debug("health check failed: %s", exc)
            return None

    def transcribe(self, wav_bytes: bytes) -> str:
        try:
            r = self._client.post(
                f"{self.base_url}/transcribe",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            )
        except httpx.ConnectError as exc:
            raise ServerError(
                f"Cannot reach the ASR server at {self.base_url}.\n"
                "Is the container running?  docker compose up -d"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise ServerError(
                "The server did not respond in time. If this is the first run "
                "it may still be downloading model weights -- check "
                "'docker compose logs -f'."
            ) from exc
        except httpx.HTTPError as exc:
            raise ServerError(f"Request to the ASR server failed: {exc}") from exc

        if r.status_code == 503:
            raise ServerError("The model is still loading. Try again in a moment.")
        if r.status_code >= 400:
            detail = r.text[:300]
            try:
                detail = r.json().get("detail", detail)
            except Exception:
                pass
            raise ServerError(f"Server error {r.status_code}: {detail}")

        payload = r.json()
        log.debug(
            "server: %sms for %.2fs audio via %s",
            payload.get("latency_ms"),
            payload.get("duration_s", 0.0),
            payload.get("backend"),
        )
        return payload.get("text", "")

    def close(self) -> None:
        self._client.close()
