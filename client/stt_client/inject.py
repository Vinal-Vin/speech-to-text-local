"""Insert text at the current cursor position on Windows.

Three modes, because on a locked-down machine any one of them may be blocked:

  paste           clipboard + synthetic Ctrl+V. The default. One atomic
                  operation, correct for Unicode/emoji/newlines, and instant
                  regardless of transcript length.
  type            synthetic per-character typing via KEYEVENTF_UNICODE. For
                  apps that refuse paste (some terminals, some secure fields).
                  Slow for long text -- roughly length x delay.
  clipboard_only  copy only; you press Ctrl+V yourself. The guaranteed escape
                  hatch if endpoint security blocks synthetic input entirely.

Known limitation (UIPI): a non-elevated process cannot send input to an
elevated window. If the target app runs as administrator, insertion silently
does nothing -- this is a Windows security boundary, not a bug here. Run the
client elevated too, or use clipboard_only.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

# --- Win32 SendInput plumbing -------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_V = 0x56

# ULONG_PTR is pointer-sized: 8 bytes on 64-bit Python, 4 on 32-bit. Getting
# this wrong silently corrupts the struct layout and SendInput does nothing.
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTunion(ctypes.Union):
    # All three members are declared even though only `ki` is ever used.
    # The union's size is set by its LARGEST member (MOUSEINPUT: 32 bytes on
    # x64, 24 on x86), which makes sizeof(INPUT) 40 / 28 respectively.
    # SendInput validates cbSize against exactly that and refuses the call if
    # it disagrees -- so a hand-rolled padding field of the wrong width breaks
    # injection entirely, silently, on every keystroke.
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    user32 = ctypes.windll.user32
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        err = ctypes.get_last_error()
        log.warning("SendInput sent %d/%d events (error %s)", sent, len(inputs), err)
    return sent


def _vk_event(vk: int, keyup: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if keyup else 0, 0, 0)
    return inp


def _unicode_events(ch: str) -> list[INPUT]:
    """Key down/up events for a single character.

    SendInput's KEYEVENTF_UNICODE carries one UTF-16 code unit per event, so
    anything outside the BMP (emoji, some CJK extensions) must be split into
    a surrogate pair and sent as two events.
    """
    code = ord(ch)
    if code <= 0xFFFF:
        units = [code]
    else:
        offset = code - 0x10000
        units = [0xD800 + (offset >> 10), 0xDC00 + (offset & 0x3FF)]

    events: list[INPUT] = []
    for unit in units:
        for keyup in (False, True):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
            inp.ki = KEYBDINPUT(0, unit, flags, 0, 0)
            events.append(inp)
    return events


# --- Clipboard ----------------------------------------------------------------


def _get_clipboard_text() -> str | None:
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
        try:
            import win32con

            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        # Another process can hold the clipboard open; this is transient and
        # only costs us the ability to restore prior contents.
        log.debug("could not read clipboard: %s", exc)
        return None


def _set_clipboard_text(text: str, retries: int = 5) -> bool:
    import win32clipboard
    import win32con

    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception as exc:
            # The clipboard is a single global resource; brief contention with
            # another app is normal, so back off and retry rather than fail.
            log.debug("clipboard set attempt %d failed: %s", attempt + 1, exc)
            time.sleep(0.02 * (attempt + 1))
    log.error("could not set clipboard after %d attempts", retries)
    return False


# --- Public API ---------------------------------------------------------------


class Injector:
    def __init__(
        self,
        mode: str = "paste",
        restore_clipboard: bool = True,
        restore_delay_ms: int = 300,
        type_delay_ms: int = 1,
    ) -> None:
        if mode not in ("paste", "type", "clipboard_only"):
            raise ValueError(f"unknown insert_mode {mode!r}")
        self.mode = mode
        self.restore_clipboard = restore_clipboard
        self.restore_delay_ms = restore_delay_ms
        self.type_delay_ms = type_delay_ms

    def insert(self, text: str) -> bool:
        """Insert text at the cursor. Returns True if something was inserted."""
        if not text:
            # Never clobber the clipboard for an empty transcript.
            return False
        if self.mode == "type":
            return self._type(text)
        return self._paste(text, paste_key=self.mode == "paste")

    def _paste(self, text: str, paste_key: bool) -> bool:
        previous = _get_clipboard_text() if self.restore_clipboard else None

        if not _set_clipboard_text(text):
            return False

        if not paste_key:
            log.info("copied %d chars to clipboard (press Ctrl+V to insert)", len(text))
            return True  # clipboard_only: leave it there, do not restore

        # Small settle before pasting: the foreground window has just regained
        # focus after the hotkey release, and some apps drop input sent too
        # soon after.
        time.sleep(0.05)
        _send(
            [
                _vk_event(VK_CONTROL, False),
                _vk_event(VK_V, False),
                _vk_event(VK_V, True),
                _vk_event(VK_CONTROL, True),
            ]
        )

        if self.restore_clipboard and previous is not None:
            # Restore on a timer: the target app may read the clipboard lazily,
            # so restoring immediately can make the paste land empty.
            time.sleep(self.restore_delay_ms / 1000)
            _set_clipboard_text(previous)

        return True

    def _type(self, text: str) -> bool:
        delay = self.type_delay_ms / 1000
        for ch in text:
            if ch == "\n":
                _send([_vk_event(0x0D, False), _vk_event(0x0D, True)])  # VK_RETURN
            else:
                _send(_unicode_events(ch))
            if delay:
                time.sleep(delay)
        return True
