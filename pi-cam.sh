#!/bin/sh
# Launch Pi Cam (run install.sh once first).
set -eu
cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0")")"
ROOT=$(pwd)

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/picam"
mkdir -p "$DATA"
LOG="$DATA/startup.log"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "Pi Cam is not installed yet. Run: bash install.sh" | tee "$LOG" >&2
    exit 1
fi

pkill -f '/python(3)? -m picam' 2>/dev/null || true
pkill -f 'python(3)? -m picam' 2>/dev/null || true
sleep 0.3

set +e
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
"$ROOT/.venv/bin/python" -m picam "$@" >"$LOG" 2>&1
status=$?
set -e
if [ "$status" -ne 0 ]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Pi Cam" --text="Could not start. See $LOG" 2>/dev/null || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "Pi Cam" "Could not start. See $LOG" 2>/dev/null || true
    fi
fi
exit "$status"
