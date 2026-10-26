"""Application bootstrap: data directory, database (with recovery), icon, fonts."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from . import paths
from .database import Database, DatabaseAccessError, DatabaseCorruptError, latest_backup
from .main_window import MainWindow
from .store import NoteStore
from .styles import Fonts, build_qss, current_theme
from .widgets import MenuStyler, MessageDialog

MONO_CANDIDATES = ["SF Mono", "Menlo", "JetBrains Mono", "DejaVu Sans Mono", "Ubuntu Mono",
                   "Consolas", "Liberation Mono"]


def load_icon_pixmap() -> Optional[QPixmap]:
    """Load simplenotesiconimage.jpg, or None if it's missing or unreadable."""
    path = paths.find_icon()
    if path is None:
        return None
    pixmap = QPixmap(str(path))
    return None if pixmap.isNull() else pixmap


def pick_fonts() -> Fonts:
    families = set(QFontDatabase.families())
    ui = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    mono = next((f for f in MONO_CANDIDATES if f in families),
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
    return Fonts(ui=ui, mono=mono)


def _alert(text: str, info: str) -> None:
    MessageDialog.ask(None, "Simple Notes", text, info, [("ok", "OK", "primary")], default="ok")


def open_database() -> Optional[Database]:
    """Open the database, offering recovery from the latest backup if it's damaged."""
    db_file = paths.db_path()
    try:
        return Database(db_file)
    except DatabaseAccessError as exc:
        _alert("Cannot open the notes database.", f"{exc}\n\nCheck the folder's permissions and try again.")
        return None
    except DatabaseCorruptError as exc:
        reason = str(exc)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    quarantined = db_file.with_name(f"{db_file.name}.corrupt-{stamp}")
    backup = latest_backup(paths.backups_dir())
    if backup is not None:
        message = (f"The notes database appears to be damaged ({reason}).\n\n"
                   f"Restore from the most recent backup ({backup.name})? The damaged file will be "
                   f"kept as {quarantined.name}.")
    else:
        message = (f"The notes database appears to be damaged ({reason}) and no backup exists.\n\n"
                   f"Start with an empty database? The damaged file will be kept as {quarantined.name}.")
    answer = MessageDialog.ask(None, "Simple Notes", message, "",
                               [("no", "Quit", ""), ("yes", "Restore" if backup is not None else "Start Empty",
                                                     "primary")], default="yes")
    if answer != "yes":
        return None
    try:
        db_file.replace(quarantined)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(db_file) + suffix).unlink(missing_ok=True)
        if backup is not None:
            shutil.copy2(backup, db_file)
        return Database(db_file)
    except (OSError, DatabaseAccessError, DatabaseCorruptError) as exc:
        _alert("Recovery failed.", str(exc))
        return None


def run(argv: List[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Simple Notes")
    app.setOrganizationName("noteapp")
    menu_styler = MenuStyler(app)  # rounded corners on every menu, Qt's own context menus included
    app.installEventFilter(menu_styler)

    fonts = pick_fonts()
    ui_font = QFont(fonts.ui)
    ui_font.setPixelSize(13)
    app.setFont(ui_font)
    app.setStyleSheet(build_qss(current_theme()))  # startup dialogs; the window applies the saved theme

    logo = load_icon_pixmap()
    if logo is not None:
        app.setWindowIcon(QIcon(logo))  # title bar, Dock / taskbar, app switcher

    try:
        paths.ensure_data_dirs()
    except OSError as exc:
        _alert(f"Cannot create the data folder {paths.data_dir()}.", str(exc))
        return 1

    db = open_database()
    if db is None:
        return 1
    store = NoteStore(db)
    store.backup_if_needed(paths.backups_dir())

    settings = QSettings(str(paths.settings_path()), QSettings.Format.IniFormat)
    window = MainWindow(store, settings, fonts)
    if logo is not None:
        window.setWindowIcon(QIcon(logo))
    window.show()
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv))
