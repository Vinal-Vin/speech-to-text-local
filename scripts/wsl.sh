#!/usr/bin/env bash
#
# Drive the Windows dictation client from a WSL shell.
#
#   ./scripts/wsl.sh doctor          # run the environment checks
#   ./scripts/wsl.sh setup           # install the client
#   ./scripts/wsl.sh setup -Embeddable
#   ./scripts/wsl.sh run             # start the client
#   ./scripts/wsl.sh run --print-only
#
# IMPORTANT -- what this script is and is not:
#
# It does NOT run the client inside WSL. That is impossible, not merely
# awkward: the client needs the Windows microphone, a Windows global keyboard
# hook, and Windows SendInput to type into Windows applications. WSL cannot
# send a keystroke to a Windows window at all.
#
# What it does is use WSL interop to launch a *Windows* process from your WSL
# shell. The work happens on the Windows side with full Windows API access;
# this script is only a convenient front door so you do not have to switch to
# a PowerShell window.
#
# The server is unaffected -- `docker compose up -d` runs natively in WSL.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }

# --- 0. Help, before any environment checks ----------------------------------
# Asking what a script does must work everywhere, including on a machine where
# the checks below would refuse to run.
case "${1:-}" in
  ""|-h|--help|help)
    sed -n '3,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

# --- 1. Must be WSL, with interop enabled ------------------------------------

if ! grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    red "This does not look like WSL."
    cat <<'EOF'

This launcher exists to start Windows processes from a WSL shell. On a plain
Linux machine there is no Windows side to launch, and the dictation client
cannot run: it depends on the Windows microphone, keyboard hook and SendInput.

The SERVER does run anywhere:  docker compose up -d
EOF
    exit 1
fi

if ! command -v powershell.exe >/dev/null 2>&1; then
    red "WSL interop is not available (cannot find powershell.exe)."
    cat <<'EOF'

WSL can normally execute Windows binaries directly. If that is disabled, this
launcher cannot work and you should run the setup from a Windows PowerShell
window instead.

To re-enable interop, ensure /etc/wsl.conf contains:

    [interop]
    enabled = true
    appendWindowsPath = true

then run `wsl --shutdown` from Windows and reopen your WSL shell.
EOF
    exit 1
fi

# --- 2. The repo should live on the Windows filesystem -----------------------
#
# A clone inside the WSL filesystem is reachable from Windows only through the
# \\wsl.localhost\ share. Windows Python can run from there, but every file
# read crosses a network redirector: setup is slow, and the venv it builds is
# a Windows venv living on a Linux filesystem, which is a reliable source of
# permission oddities. /mnt/c is the real Windows disk seen from WSL, so a
# clone there is genuinely local to both sides.

if [[ "$ROOT" != /mnt/[a-z]/* ]]; then
    ylw "WARNING: this repo is inside the WSL filesystem, not on the Windows disk."
    cat <<EOF

  current : $ROOT
  seen by Windows as: \\\\wsl.localhost\\...  (a network share)

Windows can reach it, but setup will be slow and permission errors are common.
Strongly recommended -- re-clone onto the Windows disk, which both sides see
as a local path:

  cd /mnt/c/Users/\$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')
  git clone https://github.com/Vinal-Vin/speech-to-text-local
  cd speech-to-text-local && ./scripts/wsl.sh setup

EOF
    # Only prompt when there is a human to answer. Piped or scripted runs must
    # not hang forever waiting on stdin that will never arrive.
    if [[ -t 0 ]]; then
        read -r -p "Continue anyway? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] || exit 1
    else
        ylw "Non-interactive shell -- continuing anyway."
    fi
fi

# --- 3. Dispatch -------------------------------------------------------------

win_path() { wslpath -w "$1"; }

CMD="${1:-}"; shift || true

case "$CMD" in
  setup)
    # -ExecutionPolicy Bypass applies to this invocation only; it changes no
    # machine setting, which matters on a managed device where unsigned
    # scripts are blocked.
    grn "Launching Windows PowerShell to set up the client..."
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
        -File "$(win_path "$ROOT/scripts/setup-client.ps1")" "$@"
    ;;

  doctor|run)
    # Must be the WINDOWS python, never the WSL one. Resolve it in this order:
    # the venv created by setup, then the embeddable build, then whatever
    # python.exe is on the Windows PATH.
    PY=""
    for candidate in \
        "$ROOT/.venv/Scripts/python.exe" \
        "$ROOT/.python-embed/python.exe"; do
        [[ -x "$candidate" || -f "$candidate" ]] && { PY="$candidate"; break; }
    done

    if [[ -z "$PY" ]]; then
        if command -v python.exe >/dev/null 2>&1; then
            PY="$(command -v python.exe)"
            ylw "No client venv found; falling back to Windows python.exe on PATH."
            ylw "Run './scripts/wsl.sh setup' first for a proper install."
        else
            red "No Windows Python found."
            echo "Run './scripts/wsl.sh setup' first (add -Embeddable if Python cannot be installed)."
            exit 1
        fi
    fi

    if [[ "$CMD" == "doctor" ]]; then
        grn "Running doctor.py as a Windows process..."
        exec "$PY" "$(win_path "$ROOT/scripts/doctor.py")" "$@"
    fi

    grn "Starting the dictation client as a Windows process..."
    echo "(Ctrl+C here stops it.)"
    cd "$ROOT/client"
    exec "$PY" -m stt_client "$@"
    ;;

  *)
    red "Unknown command: $CMD"
    echo "Use: doctor | setup | run   (or --help)"
    exit 1
    ;;
esac
