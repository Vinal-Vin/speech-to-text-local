"""Client configuration, loaded from TOML.

Looked up in order:
  1. --config PATH
  2. %APPDATA%\\stt-local\\config.toml
  3. ./config.toml  (next to the repo, handy while developing)
  4. built-in defaults
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class Config:
    # --- Server ---
    server_url: str = "http://127.0.0.1:8760"
    request_timeout_s: float = 120.0

    # --- Hotkey ---
    # pynput key name. Push-to-talk: hold to record, release to transcribe.
    # Right Ctrl is a good default: rarely bound, easy to hold, and distinct
    # from the left Ctrl used in ordinary shortcuts.
    hotkey: str = "ctrl_r"
    # Ignore accidental taps shorter than this. Without it, brushing the key
    # fires a pointless round-trip to the server.
    min_record_ms: int = 300
    # Safety stop so a stuck key cannot record forever.
    max_record_s: float = 300.0

    # --- Audio ---
    sample_rate: int = 16000
    # None = system default input device. Run with --list-devices to see indices.
    input_device: int | None = None

    # --- Insertion ---
    # "paste"          -> clipboard + synthetic Ctrl+V   (fast, Unicode-safe)
    # "type"           -> synthetic per-character typing (for apps refusing paste)
    # "clipboard_only" -> copy only, you press Ctrl+V    (works even if the
    #                     machine blocks synthetic input entirely)
    insert_mode: str = "paste"
    # Delay before restoring the previous clipboard. Some apps read the
    # clipboard lazily; restoring too fast makes the paste land empty.
    clipboard_restore_delay_ms: int = 300
    restore_clipboard: bool = True
    # Appended after each transcript, e.g. " " to keep dictating inline.
    append_text: str = ""

    # --- Feedback ---
    sound_cues: bool = True
    tray_icon: bool = True

    # --- Text post-processing ---
    # Applied to every transcript, in order. Invaluable for agentic coding
    # work, where the model reliably mis-hears the same identifiers.
    # Example: replacements = { "get hub" = "GitHub", "pie test" = "pytest" }
    replacements: dict[str, str] = field(default_factory=dict)


def _config_search_paths(explicit: str | None) -> list[Path]:
    paths = []
    if explicit:
        paths.append(Path(explicit))
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "stt-local" / "config.toml")
    paths.append(Path.cwd() / "config.toml")
    return paths


def load_config(explicit: str | None = None) -> tuple[Config, Path | None]:
    """Return (config, path_it_came_from). Missing file is fine -> defaults."""
    known = {f.name for f in fields(Config)}

    for path in _config_search_paths(explicit):
        if not path.is_file():
            continue
        with path.open("rb") as fh:
            data = tomllib.load(fh)

        unknown = set(data) - known
        if unknown:
            # Warn rather than fail: a typo'd key silently doing nothing is a
            # genuinely confusing way to lose an afternoon.
            print(
                f"[config] warning: ignoring unknown key(s) in {path}: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
        return Config(**{k: v for k, v in data.items() if k in known}), path

    return Config(), None


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    """Case-insensitive whole-string replacements for known mis-hearings."""
    if not text or not replacements:
        return text
    import re

    for wrong, right in replacements.items():
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
    return text
