"""Persist simple UI prefs (rotation, Game Boy options)."""

from __future__ import annotations

import json
from pathlib import Path

from picam.paths import data_dir


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.is_file():
        return {"rotation": 0, "gb_lcd": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"rotation": 0, "gb_lcd": True}
    if not isinstance(data, dict):
        return {"rotation": 0, "gb_lcd": True}
    rotation = int(data.get("rotation") or 0) % 360
    if rotation not in {0, 90, 180, 270}:
        rotation = 0
    return {
        "rotation": rotation,
        "gb_lcd": bool(data.get("gb_lcd", True)),
    }


def save_settings(settings: dict) -> None:
    path = settings_path()
    payload = {
        "rotation": int(settings.get("rotation") or 0) % 360,
        "gb_lcd": bool(settings.get("gb_lcd", True)),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
