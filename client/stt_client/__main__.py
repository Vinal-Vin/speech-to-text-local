"""Entry point: wires hotkey -> recorder -> server -> cursor insertion."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from .config import Config, apply_replacements, load_config
from .hotkey import PushToTalkListener
from .recorder import Recorder, list_devices, to_wav_bytes
from .transport import ServerError, Transport

log = logging.getLogger("stt-client")


def _beep(freq: int, ms: int, enabled: bool) -> None:
    """Non-blocking audio cue. Windows-only; silently skipped elsewhere."""
    if not enabled:
        return
    try:
        import winsound

        threading.Thread(
            target=lambda: winsound.Beep(freq, ms), daemon=True
        ).start()
    except Exception:
        pass


class App:
    def __init__(self, cfg: Config, print_only: bool = False) -> None:
        self.cfg = cfg
        self.print_only = print_only
        self.recorder = Recorder(
            sample_rate=cfg.sample_rate,
            device=cfg.input_device,
            max_seconds=cfg.max_record_s,
        )
        self.transport = Transport(cfg.server_url, timeout_s=cfg.request_timeout_s)
        self.injector = None
        if not print_only:
            from .inject import Injector

            self.injector = Injector(
                mode=cfg.insert_mode,
                restore_clipboard=cfg.restore_clipboard,
                restore_delay_ms=cfg.clipboard_restore_delay_ms,
            )
        self.tray = None
        self._start_time = 0.0
        self._busy = threading.Lock()

    # --- hotkey callbacks ---------------------------------------------------

    def on_start(self) -> None:
        if self.recorder.is_recording:
            return
        self._start_time = time.perf_counter()
        try:
            self.recorder.start()
        except Exception as exc:
            log.error("could not open microphone: %s", exc)
            self._set_state("error", f"Microphone error: {exc}")
            return
        _beep(880, 60, self.cfg.sound_cues)
        self._set_state("recording", "Recording...")

    def on_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        held_ms = (time.perf_counter() - self._start_time) * 1000

        if held_ms < self.cfg.min_record_ms:
            log.info("ignored %.0fms tap (min %dms)", held_ms, self.cfg.min_record_ms)
            self._set_state("idle", "Ready")
            return

        if audio.size == 0:
            log.warning("no audio captured")
            self._set_state("idle", "Ready")
            return

        _beep(660, 60, self.cfg.sound_cues)
        self._set_state("transcribing", "Transcribing...")
        # Off the hotkey thread: pynput's listener must not block, or you drop
        # the next keypress while a transcript is in flight.
        threading.Thread(target=self._transcribe_and_insert, args=(audio,), daemon=True).start()

    # --- worker -------------------------------------------------------------

    def _transcribe_and_insert(self, audio) -> None:
        with self._busy:
            t0 = time.perf_counter()
            try:
                wav = to_wav_bytes(audio, self.cfg.sample_rate)
                text = self.transport.transcribe(wav)
            except ServerError as exc:
                log.error("%s", exc)
                self._set_state("error", str(exc).splitlines()[0])
                if self.tray:
                    self.tray.notify(str(exc))
                return
            except Exception as exc:
                log.exception("unexpected transcription failure")
                self._set_state("error", str(exc))
                return

            text = apply_replacements(text, self.cfg.replacements)
            elapsed = (time.perf_counter() - t0) * 1000
            duration = self.recorder.duration_of(audio)

            if not text.strip():
                log.info("no speech detected in %.1fs of audio", duration)
                self._set_state("idle", "Ready (no speech)")
                return

            log.info("%.1fs audio -> %d chars in %.0fms", duration, len(text), elapsed)

            if self.print_only:
                print(f"\n>>> {text}\n", flush=True)
            else:
                out = text + self.cfg.append_text
                try:
                    self.injector.insert(out)
                except Exception as exc:
                    log.exception("insertion failed")
                    self._set_state("error", f"Insertion failed: {exc}")
                    return

            self._set_state("idle", "Ready")

    def _set_state(self, state: str, tooltip: str | None = None) -> None:
        if self.tray:
            self.tray.set_state(state, tooltip)

    def close(self) -> None:
        self.transport.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stt_client", description="Local push-to-talk dictation")
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("--list-devices", action="store_true", help="list audio input devices and exit")
    p.add_argument(
        "--print-only",
        action="store_true",
        help="print transcripts to the console instead of inserting them (Phase 2 testing)",
    )
    p.add_argument("--no-tray", action="store_true", help="run without a tray icon")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_devices:
        print(list_devices())
        return 0

    cfg, cfg_path = load_config(args.config)
    print(f"config: {cfg_path or 'built-in defaults'}")
    print(f"server: {cfg.server_url}")
    print(f"hotkey: hold [{cfg.hotkey}] to dictate")
    print(f"insert: {cfg.insert_mode}" + ("  (print-only mode)" if args.print_only else ""))

    app = App(cfg, print_only=args.print_only)

    # Check the server up front: far better to say so now than to have the
    # first dictation fail after the user has already spoken.
    health = app.transport.health()
    if health is None:
        print(
            f"\n  WARNING: no ASR server at {cfg.server_url}\n"
            "  Start it with:  docker compose up -d\n"
            "  (continuing anyway -- it will be retried on each dictation)\n",
            file=sys.stderr,
        )
    else:
        print(f"server ready: backend={health.get('backend')} vad={health.get('vad')}")

    listener = PushToTalkListener(cfg.hotkey, app.on_start, app.on_stop)

    stopping = threading.Event()

    def quit_app() -> None:
        stopping.set()

    if cfg.tray_icon and not args.no_tray:
        try:
            from .tray import Tray

            app.tray = Tray(on_quit=quit_app, tooltip="stt-local: ready")
            app.tray.start()
            app.tray.set_state("idle", "Ready")
        except Exception as exc:
            log.warning("tray unavailable (%s); continuing without it", exc)

    try:
        listener.start()
    except Exception as exc:
        print(f"\nERROR: could not register the global hotkey: {exc}", file=sys.stderr)
        print("Run scripts/doctor.py to diagnose.", file=sys.stderr)
        return 1

    print("\nready -- press Ctrl+C here to quit\n")
    try:
        while not stopping.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        listener.stop()
        if app.tray:
            app.tray.stop()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
