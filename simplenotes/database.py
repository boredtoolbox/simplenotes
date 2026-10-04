"""SQLite data access layer. Pure Python: no Qt imports here."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .models import DEFAULT_NOTE_TITLE, Folder, Note, count_words, now_iso

# A note-list scope: ("all", None), ("standalone", None) or ("folder", folder_id).
Scope = Tuple[str, Optional[int]]

SCHEMA_VERSION = 3  # 2: notes.position, 3: folders.position (manual order)

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    color_tag TEXT,
    created_date TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    markdown_content TEXT NOT NULL DEFAULT '',
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    created_date TEXT NOT NULL,
    modified_date TEXT NOT NULL,
    word_count INTEGER NOT NULL DEFAULT 0,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id);
"""


class DatabaseError(Exception):
    """Base class for database problems surfaced to the UI."""


class DatabaseCorruptError(DatabaseError):
    """The database file exists but is unreadable or fails its integrity check."""


class DatabaseAccessError(DatabaseError):
    """The database file cannot be opened (permissions, missing directory, ...)."""


def _folder(row: sqlite3.Row) -> Folder:
    return Folder(row["id"], row["name"], row["color_tag"], row["created_date"], bool(row["is_archived"]),
                  row["position"])


def _note(row: sqlite3.Row) -> Note:
    return Note(
        row["id"], row["title"], row["markdown_content"], row["folder_id"],
        row["created_date"], row["modified_date"], row["word_count"], bool(row["is_pinned"]), row["position"],
    )


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        try:
            self.conn = sqlite3.connect(str(self.path))
        except sqlite3.OperationalError as exc:
            raise DatabaseAccessError(f"Cannot open {self.path}: {exc}") from exc
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA foreign_keys = ON")
            result = self.conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise DatabaseCorruptError(result)
            self._init_schema()
        except DatabaseCorruptError:
            self.conn.close()
            raise
        except sqlite3.OperationalError as exc:
            self.conn.close()
            raise DatabaseAccessError(f"Cannot use {self.path}: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            self.conn.close()
            raise DatabaseCorruptError(str(exc)) from exc

    def _init_schema(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'notes'").fetchone()[0]
        if has_tables and version < SCHEMA_VERSION:
            self._backup_before_migration(version)
        with self.conn:
            self.conn.executescript(SCHEMA)
            columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(notes)")}
            if "position" not in columns:
                self._migrate_add_position()
            folder_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(folders)")}
            if "position" not in folder_columns:
                self._migrate_add_folder_position()
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _backup_before_migration(self, version: int) -> None:
        """Copy the database aside before upgrading it. Deliberately not named notes-*.db,
        so daily-backup pruning and crash recovery never pick it up."""
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = sqlite3.connect(str(backup_dir / f"premigration-v{version}-to-v{SCHEMA_VERSION}-{stamp}.db"))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def _migrate_add_position(self) -> None:
        """v1 -> v2: seed the manual order from the old automatic one (pinned, then newest)."""
        self.conn.execute("ALTER TABLE notes ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        rows = self.conn.execute(
            "SELECT id, folder_id FROM notes ORDER BY is_pinned DESC, modified_date DESC, id DESC"
        ).fetchall()
        next_position: Dict[Optional[int], int] = {}
        for row in rows:
            position = next_position.get(row["folder_id"], 0)
            next_position[row["folder_id"]] = position + 1
            self.conn.execute("UPDATE notes SET position = ? WHERE id = ?", (position, row["id"]))

    def _migrate_add_folder_position(self) -> None:
        """v2 -> v3: seed the manual folder order from the old alphabetical one."""
        self.conn.execute("ALTER TABLE folders ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        rows = self.conn.execute("SELECT id FROM folders ORDER BY name COLLATE NOCASE").fetchall()
        self.conn.executemany("UPDATE folders SET position = ? WHERE id = ?",
                              [(index, row["id"]) for index, row in enumerate(rows)])

    def _top_position(self, folder_id: Optional[int]) -> int:
        """A position that sorts before every note in the group (new and moved notes go on top)."""
        row = self.conn.execute(
            "SELECT COALESCE(MIN(position), 0) - 1 FROM notes WHERE folder_id IS ?", (folder_id,)
        ).fetchone()
        return row[0]

    def close(self) -> None:
        self.conn.close()

    # ----- folders -------------------------------------------------------

    def list_folders(self, include_archived: bool = False) -> List[Folder]:
        sql = "SELECT * FROM folders"
        if not include_archived:
            sql += " WHERE is_archived = 0"
        sql += " ORDER BY position, name COLLATE NOCASE"
        return [_folder(r) for r in self.conn.execute(sql)]

    def get_folder(self, folder_id: int) -> Optional[Folder]:
        row = self.conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        return _folder(row) if row else None

    def create_folder(self, name: str, color_tag: Optional[str] = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO folders (name, color_tag, created_date, position)"
                " VALUES (?, ?, ?, (SELECT COALESCE(MAX(position), -1) + 1 FROM folders))",
                (name.strip(), color_tag, now_iso()),
            )
        return cur.lastrowid

    def update_folder(self, folder_id: int, name: str, color_tag: Optional[str]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE folders SET name = ?, color_tag = ? WHERE id = ?",
                (name.strip(), color_tag, folder_id),
            )

    def reorder_folders(self, folder_ids: List[int]) -> None:
        """Set the sidebar order of folders to folder_ids."""
        with self.conn:
            self.conn.executemany("UPDATE folders SET position = ? WHERE id = ?",
                                  [(index, folder_id) for index, folder_id in enumerate(folder_ids)])

    def delete_folder(self, folder_id: int, delete_notes: bool = False) -> None:
        """Delete a folder. Its notes become standalone (ON DELETE SET NULL) unless delete_notes."""
        with self.conn:
            if delete_notes:
                self.conn.execute("DELETE FROM notes WHERE folder_id = ?", (folder_id,))
            self.conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    def note_counts(self) -> Tuple[Dict[int, int], int, int]:
        """Returns (notes per folder id, standalone count, total count)."""
        per_folder: Dict[int, int] = {}
        standalone = 0
        for row in self.conn.execute("SELECT folder_id, COUNT(*) AS n FROM notes GROUP BY folder_id"):
            if row["folder_id"] is None:
                standalone = row["n"]
            else:
                per_folder[row["folder_id"]] = row["n"]
        return per_folder, standalone, standalone + sum(per_folder.values())

    # ----- notes ---------------------------------------------------------

    def get_note(self, note_id: int) -> Optional[Note]:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _note(row) if row else None

    def list_notes(self, scope: Scope = ("all", None), search: str = "") -> List[Note]:
        """Notes in a scope: pinned first, then in manual (drag-and-drop) order."""
        clauses, params = [], []
        kind, folder_id = scope
        if kind == "standalone":
            clauses.append("folder_id IS NULL")
        elif kind == "folder":
            clauses.append("folder_id = ?")
            params.append(folder_id)
        search = search.strip()
        if search:
            like = f"%{_like_escape(search)}%"
            clauses.append("(title LIKE ? ESCAPE '\\' OR markdown_content LIKE ? ESCAPE '\\')")
            params += [like, like]
        sql = "SELECT * FROM notes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY is_pinned DESC, position, id DESC"
        return [_note(r) for r in self.conn.execute(sql, params)]

    def create_note(self, title: str = DEFAULT_NOTE_TITLE, folder_id: Optional[int] = None,
                    content: str = "") -> int:
        stamp = now_iso()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO notes (title, markdown_content, folder_id, created_date, modified_date, word_count,"
                " position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, content, folder_id, stamp, stamp, count_words(content), self._top_position(folder_id)),
            )
        return cur.lastrowid

    def update_note_content(self, note_id: int, content: str, word_count: Optional[int] = None) -> None:
        """word_count may be passed in when the caller already knows it (the editor keeps
        a running count), which spares re-counting a long note on every autosave."""
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET markdown_content = ?, word_count = ?, modified_date = ? WHERE id = ?",
                (content, count_words(content) if word_count is None else word_count, now_iso(), note_id),
            )

    def update_note_title(self, note_id: int, title: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET title = ?, modified_date = ? WHERE id = ?", (title, now_iso(), note_id)
            )

    def move_note(self, note_id: int, folder_id: Optional[int]) -> None:
        """Move a note to the top of another folder (None = standalone)."""
        with self.conn:
            self.conn.execute("UPDATE notes SET folder_id = ?, position = ? WHERE id = ?",
                              (folder_id, self._top_position(folder_id), note_id))

    def reorder_notes(self, folder_id: Optional[int], note_ids: List[int]) -> None:
        """Make note_ids the full, ordered contents of a folder (None = standalone).
        Notes dragged in from elsewhere change folder; modified dates are untouched."""
        with self.conn:
            self.conn.executemany(
                "UPDATE notes SET folder_id = ?, position = ? WHERE id = ?",
                [(folder_id, index, note_id) for index, note_id in enumerate(note_ids)],
            )

    def set_pinned(self, note_id: int, pinned: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE notes SET is_pinned = ? WHERE id = ?", (int(pinned), note_id))

    def delete_note(self, note_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    # ----- backups -------------------------------------------------------

    def backup_if_needed(self, backup_dir: Path, keep: int = 30) -> Optional[Path]:
        """Write today's backup if it doesn't exist yet, then prune old ones."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"notes-{date.today().isoformat()}.db"
        if target.exists():
            return None
        tmp = backup_dir / (target.name + ".tmp")
        dest = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
        tmp.replace(target)
        for old in sorted(backup_dir.glob("notes-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return target


def latest_backup(backup_dir: Path) -> Optional[Path]:
    backups = sorted(backup_dir.glob("notes-*.db")) if backup_dir.is_dir() else []
    return backups[-1] if backups else None
