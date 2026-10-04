"""Observable wrapper around the Database.

The UI never talks to the Database directly: it calls the store, and the store
emits Qt signals so every view refreshes itself (observer pattern).
"""
from __future__ import annotations

import functools
import sqlite3
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .database import Database, Scope
from .models import Folder, Note


def _guarded(default=None):
    """Turn database exceptions into an error_occurred signal instead of a crash."""
    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except (sqlite3.Error, OSError) as exc:
                self.error_occurred.emit(f"{method.__name__.replace('_', ' ').capitalize()} failed: {exc}")
                return default
        return wrapper
    return decorator


class NoteStore(QObject):
    folders_changed = pyqtSignal()       # folder created/renamed/deleted
    notes_changed = pyqtSignal()         # note created/deleted/moved/pinned (counts may change)
    note_saved = pyqtSignal(int)         # a note's title or content was saved
    error_occurred = pyqtSignal(str)

    def __init__(self, db: Database, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db

    # ----- reads ---------------------------------------------------------

    @_guarded(default=[])
    def list_folders(self) -> List[Folder]:
        return self.db.list_folders()

    @_guarded(default=({}, 0, 0))
    def note_counts(self):
        return self.db.note_counts()

    @_guarded(default=[])
    def list_notes(self, scope: Scope, search: str = "") -> List[Note]:
        return self.db.list_notes(scope, search)

    @_guarded()
    def get_note(self, note_id: int) -> Optional[Note]:
        return self.db.get_note(note_id)

    @_guarded()
    def get_folder(self, folder_id: int) -> Optional[Folder]:
        return self.db.get_folder(folder_id)

    # ----- folder writes -------------------------------------------------

    @_guarded()
    def create_folder(self, name: str, color_tag: Optional[str]) -> Optional[int]:
        folder_id = self.db.create_folder(name, color_tag)
        self.folders_changed.emit()
        return folder_id

    @_guarded(default=False)
    def update_folder(self, folder_id: int, name: str, color_tag: Optional[str]) -> bool:
        self.db.update_folder(folder_id, name, color_tag)
        self.folders_changed.emit()
        return True

    @_guarded(default=False)
    def reorder_folders(self, folder_ids: List[int]) -> bool:
        self.db.reorder_folders(folder_ids)
        self.folders_changed.emit()
        return True

    @_guarded(default=False)
    def delete_folder(self, folder_id: int, delete_notes: bool) -> bool:
        self.db.delete_folder(folder_id, delete_notes)
        self.folders_changed.emit()
        return True

    # ----- note writes ---------------------------------------------------

    @_guarded()
    def create_note(self, title: str, folder_id: Optional[int]) -> Optional[int]:
        note_id = self.db.create_note(title, folder_id)
        self.notes_changed.emit()
        return note_id

    @_guarded(default=False)
    def update_note_content(self, note_id: int, content: str, word_count: Optional[int] = None) -> bool:
        self.db.update_note_content(note_id, content, word_count)
        self.note_saved.emit(note_id)
        return True

    @_guarded(default=False)
    def update_note_title(self, note_id: int, title: str) -> bool:
        self.db.update_note_title(note_id, title)
        self.note_saved.emit(note_id)
        return True

    @_guarded(default=False)
    def move_note(self, note_id: int, folder_id: Optional[int]) -> bool:
        self.db.move_note(note_id, folder_id)
        self.notes_changed.emit()
        return True

    @_guarded(default=False)
    def reorder_notes(self, folder_id: Optional[int], note_ids: List[int]) -> bool:
        self.db.reorder_notes(folder_id, note_ids)
        self.notes_changed.emit()
        return True

    @_guarded(default=False)
    def set_pinned(self, note_id: int, pinned: bool) -> bool:
        self.db.set_pinned(note_id, pinned)
        self.notes_changed.emit()
        return True

    @_guarded(default=False)
    def delete_note(self, note_id: int) -> bool:
        self.db.delete_note(note_id)
        self.notes_changed.emit()
        return True

    @_guarded()
    def backup_if_needed(self, backup_dir: Path) -> Optional[Path]:
        return self.db.backup_if_needed(backup_dir)
