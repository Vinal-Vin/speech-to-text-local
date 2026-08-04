"""Global push-to-talk hotkey listener (pynput).

Push-to-talk needs press AND release events, so this uses a raw Listener with
its own state machine rather than pynput's GlobalHotKeys helper (which only
fires once on activation and cannot express "held").

Key detail: Windows auto-repeats key-down events while a key is held, so
on_press fires continuously. The `_held` flag makes start-recording
idempotent -- without it every repeat would restart the recording and you
would capture only the final few milliseconds.
"""

from __future__ import annotations

import logging
from typing import Callable

from pynput import keyboard

log = logging.getLogger(__name__)


def parse_hotkey(name: str):
    """Resolve a config string to a pynput key object.

    Accepts special key names ("ctrl_r", "f9", "scroll_lock") or a single
    character ("`").
    """
    name = name.strip().lower()
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    valid = ", ".join(sorted(k.name for k in keyboard.Key))
    raise ValueError(f"Unknown hotkey {name!r}.\nValid special keys: {valid}\nOr a single character.")


def _matches(key, target) -> bool:
    if key == target:
        return True
    # pynput reports character keys as KeyCode; compare by char when possible.
    if isinstance(target, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode):
        return target.char is not None and key.char == target.char
    return False


class PushToTalkListener:
    """Calls on_press_start() when the hotkey goes down, on_release_stop() when up."""

    def __init__(
        self,
        hotkey: str,
        on_press_start: Callable[[], None],
        on_release_stop: Callable[[], None],
    ) -> None:
        self.target = parse_hotkey(hotkey)
        self.hotkey_name = hotkey
        self._on_start = on_press_start
        self._on_stop = on_release_stop
        self._held = False
        self._listener: keyboard.Listener | None = None

    def _handle_press(self, key) -> None:
        if not _matches(key, self.target) or self._held:
            return  # ignore OS key-repeat while held
        self._held = True
        try:
            self._on_start()
        except Exception:
            log.exception("error starting recording")
            self._held = False

    def _handle_release(self, key) -> None:
        if not _matches(key, self.target) or not self._held:
            return
        self._held = False
        try:
            self._on_stop()
        except Exception:
            log.exception("error stopping recording")

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._handle_press, on_release=self._handle_release
        )
        self._listener.start()
        log.info("push-to-talk armed: hold %s to dictate", self.hotkey_name)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def join(self) -> None:
        if self._listener is not None:
            self._listener.join()
