"""System tray icon showing dictation state.

With push-to-talk there is no window to look at, so this plus the sound cues
are the only feedback that recording actually started.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Colour per state -- readable at 16x16 in the tray.
_COLOURS = {
    "idle": (110, 110, 118),
    "recording": (220, 60, 60),
    "transcribing": (235, 170, 40),
    "error": (150, 40, 160),
}


class Tray:
    def __init__(self, on_quit: Callable[[], None], tooltip: str = "stt-local") -> None:
        self._on_quit = on_quit
        self._tooltip = tooltip
        self._icon = None
        self._state = "idle"

    def _image(self, state: str):
        from PIL import Image, ImageDraw

        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, size - 4, size - 4], fill=_COLOURS.get(state, _COLOURS["idle"]))
        # A small mic glyph so the icon reads as dictation, not a generic dot.
        d.rounded_rectangle([26, 16, 38, 38], radius=6, fill=(255, 255, 255, 235))
        d.arc([20, 26, 44, 46], start=0, end=180, fill=(255, 255, 255, 235), width=4)
        d.line([32, 46, 32, 52], fill=(255, 255, 255, 235), width=4)
        return img

    def start(self) -> None:
        import pystray

        self._icon = pystray.Icon(
            "stt-local",
            self._image("idle"),
            self._tooltip,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: f"State: {self._state}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            ),
        )
        # run_detached keeps the tray on its own thread so the hotkey listener
        # and the main loop stay responsive.
        self._icon.run_detached()

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        self._state = state
        if self._icon is None:
            return
        try:
            self._icon.icon = self._image(state)
            if tooltip:
                self._icon.title = tooltip
        except Exception as exc:
            log.debug("could not update tray icon: %s", exc)

    def notify(self, message: str, title: str = "stt-local") -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as exc:
            log.debug("could not show notification: %s", exc)

    def _quit(self) -> None:
        try:
            self._on_quit()
        finally:
            self.stop()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
