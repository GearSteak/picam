#!/bin/sh
# One-shot Pi Cam install for Raspberry Pi OS.
#   bash install.sh
set -eu
cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0")")"
ROOT=$(pwd)

if [ "$(uname -s)" != "Linux" ]; then
    echo "Copy this folder to the Pi, then run: bash install.sh" >&2
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    echo "Run as your normal user (not sudo). The script will ask for sudo when needed." >&2
    exit 1
fi

echo "Installing camera packages..."
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    python3-tk \
    python3-pil \
    python3-numpy \
    python3-opencv \
    python3-picamera2 || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pil.imagetk || true

chmod +x "$ROOT/pi-cam.sh" "$ROOT/install.sh"

echo "Setting up Python env..."
rm -rf "$ROOT/.venv"
python3 -m venv --system-site-packages "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

echo "Adding desktop icon + login autostart..."
mkdir -p "$HOME/Desktop" "$HOME/.local/share/applications" "$HOME/.config/autostart"

write_entry() {
    autostart=$1
    target=$2
    if [ "$autostart" = "1" ]; then
        exec_line="sh -c \"sleep 8; exec \\\"$ROOT/pi-cam.sh\\\"\""
        extra="X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
"
    else
        exec_line="\"$ROOT/pi-cam.sh\""
        extra=""
    fi
    cat > "$target" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Pi Cam
Comment=Photos, video, panorama, and Game Boy Camera filters
Exec=$exec_line
Path=$ROOT
Icon=camera-photo
Terminal=false
Categories=AudioVideo;Photography;
StartupNotify=true
$extra
EOF
    chmod +x "$target"
}

write_entry 0 "$HOME/Desktop/Pi Cam.desktop"
write_entry 0 "$HOME/.local/share/applications/pi-cam.desktop"
write_entry 1 "$HOME/.config/autostart/pi-cam.desktop"
if command -v gio >/dev/null 2>&1; then
    gio set "$HOME/Desktop/Pi Cam.desktop" metadata::trusted true || true
fi

echo
echo "Done. Double-click Pi Cam on the desktop, or run: $ROOT/pi-cam.sh"
echo "It will also open on login."
