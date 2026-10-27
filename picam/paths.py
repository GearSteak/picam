"""Where captures and logs live."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("PICAM_DATA")
    if override:
        path = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / "picam"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        path = base / "picam"
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    path = data_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def photos_dir() -> Path:
    path = media_dir() / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def videos_dir() -> Path:
    path = media_dir() / "videos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def panoramas_dir() -> Path:
    path = media_dir() / "panoramas"
    path.mkdir(parents=True, exist_ok=True)
    return path
