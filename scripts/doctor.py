"""Phase 0: check this machine can actually run the dictation client.

Run this FIRST, on the Windows host, before setting anything else up:

    python scripts\\doctor.py

On a managed/corporate machine the three things most likely to block this
project are (a) a policy-muted microphone, (b) endpoint security blocking
global keyboard hooks, and (c) endpoint security blocking synthetic input.
This script tests all three in a couple of minutes so you find out now rather
than after building everything.

Exit code 0 = everything needed works. 1 = something essential failed.
"""

from __future__ import annotations

import importlib
import platform
import socket
import subprocess
import sys
import time

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    symbol = {PASS: "[ok]", FAIL: "[FAIL]", WARN: "[warn]"}[status]
    print(f"  {symbol:7} {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# --- 1. Interpreter ----------------------------------------------------------


def check_python() -> None:
    section("1. Python")
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 9)
    record(
        PASS if ok else FAIL,
        f"Python {v.major}.{v.minor}.{v.micro}",
        "" if ok else "3.9+ required",
    )
    record(
        PASS if platform.system() == "Windows" else WARN,
        f"Platform: {platform.system()} ({platform.machine()})",
        "" if platform.system() == "Windows" else "client targets Windows; injection tests will be skipped",
    )


# --- 2. Dependencies ---------------------------------------------------------


def check_imports() -> None:
    section("2. Client dependencies")
    required = {
        "pynput": "global hotkey",
        "sounddevice": "microphone capture",
        "numpy": "audio buffers",
        "httpx": "server communication",
    }
    if platform.system() == "Windows":
        required["win32clipboard"] = "clipboard access (pywin32)"
    optional = {"pystray": "tray icon", "PIL": "tray icon rendering"}

    for mod, why in required.items():
        try:
            importlib.import_module(mod)
            record(PASS, f"import {mod}", why)
        except Exception as exc:
            record(FAIL, f"import {mod}", f"{why} -- {exc}")

    for mod, why in optional.items():
        try:
            importlib.import_module(mod)
            record(PASS, f"import {mod}", why)
        except Exception:
            record(WARN, f"import {mod}", f"{why} -- optional, client runs without it")


# --- 3. Microphone -----------------------------------------------------------


def check_microphone() -> None:
    section("3. Microphone")
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        record(FAIL, "microphone", f"cannot import audio stack: {exc}")
        return

    try:
        devices = sd.query_devices()
        inputs = [d for d in devices if d["max_input_channels"] > 0]
        if not inputs:
            record(FAIL, "input devices", "no input devices found -- mic may be disabled by policy")
            return
        record(PASS, f"{len(inputs)} input device(s)", inputs[0]["name"])
    except Exception as exc:
        record(FAIL, "enumerate devices", str(exc))
        return

    print("\n  Recording 2 seconds -- please say something...")
    try:
        rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        peak = float(np.abs(rec).max())
        rms = float(np.sqrt((rec.astype("float64") ** 2).mean()))
        if peak < 0.001:
            record(
                FAIL,
                "captured audio",
                f"peak {peak:.4f} -- silence. Mic muted, disabled by policy, or no permission?",
            )
        elif peak < 0.02:
            record(WARN, "captured audio", f"peak {peak:.4f} -- very quiet; check input level")
        else:
            record(PASS, "captured audio", f"peak {peak:.3f}, rms {rms:.3f}")
    except Exception as exc:
        record(FAIL, "record", f"{exc} -- microphone access may be blocked")


# --- 4. Global hotkey --------------------------------------------------------


def check_hotkey(timeout: float = 12.0) -> None:
    section("4. Global hotkey (keyboard hook)")
    try:
        from pynput import keyboard
    except Exception as exc:
        record(FAIL, "pynput", str(exc))
        return

    seen = {"press": False, "release": False}

    def on_press(key):
        if key == keyboard.Key.ctrl_r:
            seen["press"] = True

    def on_release(key):
        if key == keyboard.Key.ctrl_r:
            seen["release"] = True
            return False

    print(f"\n  Press and release RIGHT CTRL within {timeout:.0f}s...")
    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
    except Exception as exc:
        record(FAIL, "install keyboard hook", f"{exc} -- endpoint security may be blocking it")
        return

    deadline = time.time() + timeout
    while time.time() < deadline and not seen["release"]:
        time.sleep(0.05)
    listener.stop()

    if seen["press"] and seen["release"]:
        record(PASS, "push-to-talk events", "press and release both received")
    elif seen["press"]:
        record(WARN, "push-to-talk events", "press seen but not release")
    else:
        record(
            FAIL,
            "push-to-talk events",
            "no events -- key not pressed, or the hook is blocked by policy",
        )


# --- 5. Synthetic input (the big one) ----------------------------------------


def check_synthetic_input() -> None:
    section("5. Synthetic input + clipboard  [the most likely corporate blocker]")
    if platform.system() != "Windows":
        record(WARN, "synthetic input", "skipped -- not running on Windows")
        return

    try:
        import win32clipboard
        import win32con
    except Exception as exc:
        record(FAIL, "pywin32", str(exc))
        return

    marker = f"stt-local-doctor-{int(time.time())}"

    # 5a. clipboard write/read
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, marker)
        finally:
            win32clipboard.CloseClipboard()

        win32clipboard.OpenClipboard()
        try:
            got = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

        if got == marker:
            record(PASS, "clipboard write/read", "clipboard_only mode will work")
        else:
            record(FAIL, "clipboard write/read", f"wrote {marker!r}, read {got!r}")
    except Exception as exc:
        record(FAIL, "clipboard", str(exc))
        return

    # 5b. SendInput struct sanity -- a wrong cbSize makes SendInput silently
    # do nothing, so verify the layout before blaming policy for a failure.
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "client"))
        from stt_client.inject import INPUT
        import ctypes

        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        actual = ctypes.sizeof(INPUT)
        record(
            PASS if actual == expected else FAIL,
            "SendInput struct size",
            f"{actual} bytes (expected {expected})",
        )
    except Exception as exc:
        record(WARN, "SendInput struct size", f"could not verify: {exc}")

    # 5c. actually send a keystroke
    print(
        "\n  Testing synthetic keystrokes.\n"
        "  Open Notepad (or any text field), click into it, and leave the cursor there.\n"
        "  This will paste a test marker in 6 seconds. Do not type meanwhile."
    )
    for i in range(6, 0, -1):
        print(f"    {i}...", end="\r", flush=True)
        time.sleep(1)
    print("    sending...   ")

    try:
        from stt_client.inject import Injector

        Injector(mode="paste", restore_clipboard=False).insert(marker)
        record(
            PASS,
            "SendInput dispatched",
            "check the target window -- if the marker appeared, 'paste' mode works",
        )
        print(
            f"\n  >>> Did {marker!r} appear in your text field?\n"
            "      YES -> insert_mode = \"paste\" (the default) will work.\n"
            "      NO  -> synthetic input is blocked, or the target window was\n"
            "             elevated (UIPI). Set insert_mode = \"clipboard_only\"\n"
            "             in your config; everything else still works."
        )
    except Exception as exc:
        record(FAIL, "SendInput", f"{exc} -- use insert_mode = \"clipboard_only\"")


# --- 6. Server side ----------------------------------------------------------


def check_docker_and_port() -> None:
    section("6. Docker and networking")
    try:
        out = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if out.returncode == 0:
            record(PASS, "docker daemon", f"server {out.stdout.strip()}")
        else:
            record(FAIL, "docker daemon", (out.stderr or out.stdout).strip()[:160])
    except FileNotFoundError:
        record(FAIL, "docker", "docker not on PATH")
    except Exception as exc:
        record(FAIL, "docker", str(exc))

    # Is 8760 free, or already serving our container?
    with socket.socket() as s:
        s.settimeout(1.5)
        in_use = s.connect_ex(("127.0.0.1", 8760)) == 0

    if not in_use:
        record(PASS, "port 8760", "free")
        return

    try:
        import httpx

        r = httpx.get("http://127.0.0.1:8760/health", timeout=5.0)
        if r.status_code == 200:
            record(PASS, "port 8760", f"stt-server already running: {r.json()}")
        elif r.status_code == 503:
            record(WARN, "port 8760", "stt-server up but still loading its model")
        else:
            record(WARN, "port 8760", f"something else is listening (HTTP {r.status_code})")
    except Exception:
        record(WARN, "port 8760", "in use by a non-HTTP service -- change the port in compose")


def main() -> int:
    print("=" * 68)
    print("  stt-local doctor -- checking this machine can run the client")
    print("=" * 68)

    check_python()
    check_imports()
    check_microphone()
    check_hotkey()
    check_synthetic_input()
    check_docker_and_port()

    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]

    print("\n" + "=" * 68)
    print(f"  {len(results) - len(fails) - len(warns)} passed, {len(warns)} warnings, {len(fails)} failed")
    print("=" * 68)

    if fails:
        print("\nBlocking issues:")
        for _, name, detail in fails:
            print(f"  - {name}: {detail}")
        print(
            "\nNote: a failure in section 5 does NOT sink the project --\n"
            "set insert_mode = \"clipboard_only\" and you keep the whole\n"
            "workflow at the cost of pressing Ctrl+V yourself."
        )
        return 1

    print("\nAll good. Next:  docker compose up -d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
