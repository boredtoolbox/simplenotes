"""Platform-appropriate locations for app data and assets."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "noteapp"
ICON_FILENAME = "simplenotesiconimage.jpg"
# Transparent, square version made from ICON_FILENAME by packaging/make_icon.py (preferred).
PROCESSED_ICON_FILENAME = "simplenotesicon.png"
DB_FILENAME = "notes.db"

# Directory containing noteapp.py (the project root), or the bundle's resource
# directory when running as a PyInstaller-built SimpleNotes.app.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_DIR = Path(sys._MEIPASS)
else:
    PROJECT_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where the database, settings and backups live.

    NOTEAPP_DATA_DIR overrides the location (useful for testing).
    """
    override = os.environ.get("NOTEAPP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home()) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def backups_dir() -> Path:
    return data_dir() / "backups"


def settings_path() -> Path:
    return data_dir() / "settings.ini"


def ensure_data_dirs() -> None:
    """Create the data directory tree. Raises OSError on permission problems."""
    data_dir().mkdir(parents=True, exist_ok=True)
    backups_dir().mkdir(parents=True, exist_ok=True)


def find_icon() -> Optional[Path]:
    """Locate the app icon: the processed PNG, else the source image next to the
    script, else the source image in the home directory."""
    for candidate in (PROJECT_DIR / PROCESSED_ICON_FILENAME, PROJECT_DIR / ICON_FILENAME,
                      Path.home() / ICON_FILENAME):
        if candidate.is_file():
            return candidate
    return None
