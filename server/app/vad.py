"""Silence trimming with Silero VAD (ONNX, via pysilero-vad).

Two reasons this is worth doing before inference:

1. Less audio to process. Push-to-talk always captures dead air at both ends
   -- the gap between pressing the key and starting to speak, and between
   finishing and releasing. Trimming it is a direct latency win.
2. Whisper-family models hallucinate confident text on pure silence
   ("Thank you.", "Subtitles by ..."). Removing silence removes the trigger.

Measured behaviour worth knowing: on an 11s clip padded with 1s of silence at
each end, Silero reported speech starting at 1.34s when the true onset was
1.00s. It ramps up rather than firing instantly, so the detected region MUST
be padded outwards or the first word gets clipped. Hence vad_pad_ms.
"""

from __future__ import annotations

import logging

import numpy as np

from .audio import to_pcm16_bytes

log = logging.getLogger(__name__)


class VAD:
    def __init__(self, threshold: float = 0.5, pad_ms: int = 300) -> None:
        self.threshold = threshold
        self.pad_ms = pad_ms
        self._detector = None

    def load(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        # The ONNX model ships inside the package -- no download, no torch.
        self._detector = SileroVoiceActivityDetector()
        log.info("silero vad ready (threshold=%.2f pad=%dms)", self.threshold, self.pad_ms)

    def trim(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
        """Return audio cropped to the padded speech region.

        Returns None to mean "this recording contains no speech at all" --
        the caller should skip inference entirely and return empty text. That
        is the case VAD exists to catch: running a Whisper-family model over
        pure silence is what produces confident hallucinations like
        "Thank you." or "Subtitles by the Amara.org community".

        Any *failure* (detector unavailable, audio too short, exception) falls
        back to returning the audio untrimmed rather than None, so a broken
        VAD degrades to transcribing everything and never silently eats the
        user's words.
        """
        if self._detector is None:
            return audio

        chunk = self._detector.chunk_samples()  # 512 samples @ 16 kHz
        if len(audio) < chunk:
            return audio

        try:
            self._detector.reset()
            pcm = to_pcm16_bytes(audio)
            chunk_bytes = self._detector.chunk_bytes()

            speech_idx = [
                i
                for i, off in enumerate(range(0, len(pcm) - chunk_bytes + 1, chunk_bytes))
                if self._detector(pcm[off : off + chunk_bytes]) >= self.threshold
            ]
        except Exception as exc:
            # Never let a VAD failure cost the user their transcript.
            log.warning("vad failed, using untrimmed audio: %s", exc)
            return audio

        if not speech_idx:
            # Genuinely no speech anywhere in the clip: the user held the key
            # and said nothing. Inserting nothing is the correct outcome.
            log.info("vad found no speech in %.2fs of audio", len(audio) / sample_rate)
            return None

        pad = int(sample_rate * self.pad_ms / 1000)
        start = max(0, speech_idx[0] * chunk - pad)
        end = min(len(audio), (speech_idx[-1] + 1) * chunk + pad)

        log.debug(
            "vad trimmed %.2fs -> %.2fs", len(audio) / sample_rate, (end - start) / sample_rate
        )
        return audio[start:end]
