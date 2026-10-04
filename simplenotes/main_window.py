"""Main window: wires the store (data) to the explorer sidebar and the live editor."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import (
    QEasingCurve, QProcess, QRegularExpression, QSettings, Qt, QTimer, QUrl, QVariantAnimation,
)
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import paths
from .editor import LiveEditor
from .icons import set_button_icon
from .models import DEFAULT_NOTE_TITLE, TITLE_ALLOWED_CHARS, Note, sanitize_title
from .store import NoteStore
from .styles import DARK, LIGHT, Fonts, build_qss
from .widgets import (
    FloatingPanel, FolderDialog, NoteExplorer, PathLabel, ThemeSwitch, styled_panel, tool_button,
)

AUTOSAVE_MS = 2000
SAVED_INDICATOR_MS = 2000
COUNTS_DEBOUNCE_MS = 300
BACKUP_CHECK_MS = 60 * 60 * 1000
PANEL_ANIMATION_MS = 180  # ~11 frames at 60 fps; 100 ms (6 frames) looked jerky
DEFAULT_SIDEBAR_WIDTH = 270
SIDEBAR_TOP_GAP = 24  # space between the editor's header bar and the top of the sidebar card

SIDEBAR, EDITOR = 0, 1


class MainWindow(QMainWindow):
    def __init__(self, store: NoteStore, settings: QSettings, fonts: Fonts):
        super().__init__()
        self.store = store
        self.settings = settings
        self.fonts = fonts
        self.theme = DARK if settings.value("dark_mode", False, type=bool) else LIGHT

        self.current_note: Optional[Note] = None
        self._dirty = False
        self._loading = False
        self._search = ""
        self._sidebar_width = DEFAULT_SIDEBAR_WIDTH
        self._sidebar_target = True  # where the sidebar is (or is animating) to: shown or hidden
        self._animation: Optional[QVariantAnimation] = None

        self.setWindowTitle("Simple Notes")
        self.setMinimumSize(560, 400)
        self._build_ui()
        self._build_menus()
        self._build_timers()
        self._connect()
        self.apply_theme()
        self._restore_state()

    # ----- construction ---------------------------------------------------

    def _build_ui(self) -> None:
        self.explorer = NoteExplorer()

        self.editor_panel = QWidget()
        styled_panel(self.editor_panel, "EditorPanel")
        panel_layout = QVBoxLayout(self.editor_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QWidget()
        styled_panel(header, "EditorHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 14, 6)
        header_layout.setSpacing(6)
        header_layout.addStretch(1)
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("SavedLabel")
        header_layout.addWidget(self.saved_label)
        self.markdown_button = tool_button("</>", "Show markdown (Ctrl+Shift+M)")
        self.markdown_button.setCheckable(True)
        header_layout.addWidget(self.markdown_button)
        self.theme_switch = ThemeSwitch()
        self.theme_switch.setChecked(self.theme is DARK)
        header_layout.addWidget(self.theme_switch)
        panel_layout.addWidget(header)

        # The page: a big title above the writing column, like Notion.
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("TitleEdit")
        self.title_edit.setPlaceholderText(DEFAULT_NOTE_TITLE)
        self.title_edit.setMaxLength(200)
        self.title_edit.setValidator(QRegularExpressionValidator(QRegularExpression(
            "^[" + TITLE_ALLOWED_CHARS + "]*$",
            QRegularExpression.PatternOption.UseUnicodePropertiesOption,
        )))
        self.title_row = QWidget()
        title_layout = QHBoxLayout(self.title_row)
        title_layout.setContentsMargins(48, 36, 48, 4)
        title_layout.addWidget(self.title_edit)
        page_layout.addWidget(self.title_row)
        self.editor = LiveEditor(self.theme, self.fonts)
        page_layout.addWidget(self.editor, 1)

        footer = QWidget()
        styled_panel(footer, "Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 6)
        self.location_label = PathLabel(self._display_path(paths.db_path()))
        self.location_label.setToolTip(
            "All notes are stored in this local SQLite database.\nClick to show it in "
            + ("Finder" if sys.platform == "darwin" else "your file manager") + ".")
        self.location_label.clicked.connect(self._reveal_database)
        footer_layout.addWidget(self.location_label, 1)
        footer_layout.addSpacing(16)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        footer_layout.addWidget(self.count_label)
        page_layout.addWidget(footer)
        self.page = page

        empty = QWidget()
        styled_panel(empty, "EmptyState")
        empty_layout = QVBoxLayout(empty)
        shortcut = QKeySequence("Ctrl+N").toString(QKeySequence.SequenceFormat.NativeText)
        hint = QLabel(f"No note selected\n\nPress {shortcut} to create one")
        hint.setObjectName("EmptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(hint)
        self.empty_state = empty

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(page)
        self.content_stack.addWidget(empty)
        panel_layout.addWidget(self.content_stack, 1)

        # The card starts a little below the editor's header bar.
        self.sidebar_frame = FloatingPanel(self.explorer, top=header.sizeHint().height() + SIDEBAR_TOP_GAP)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("MainSplitter")
        self.splitter.setHandleWidth(6)  # invisible gap beside the floating sidebar; still draggable
        self.splitter.addWidget(self.sidebar_frame)
        self.splitter.addWidget(self.editor_panel)
        self.splitter.setCollapsible(SIDEBAR, False)  # it minimizes to a tile instead
        self.splitter.setCollapsible(EDITOR, False)
        self.splitter.setStretchFactor(EDITOR, 1)
        self.setCentralWidget(self.splitter)

    def _build_menus(self) -> None:
        def action(menu, text, slot, shortcut=None, checkable=False):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.setCheckable(checkable)
            act.triggered.connect(slot)
            menu.addAction(act)
            return act

        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        action(file_menu, "New Note", lambda: self.new_note(None), QKeySequence.StandardKey.New)
        action(file_menu, "New Folder…", self.new_folder, "Ctrl+Shift+N")
        file_menu.addSeparator()
        action(file_menu, "Delete Note…", lambda: self.current_note and self.delete_note(self.current_note.id))
        file_menu.addSeparator()
        action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)

        edit_menu = bar.addMenu("&Edit")
        action(edit_menu, "Search Notes", self._focus_search, QKeySequence.StandardKey.Find)

        view_menu = bar.addMenu("&View")
        action(view_menu, "Toggle Sidebar", self.toggle_sidebar, "Ctrl+1")
        self.markdown_action = action(view_menu, "Show Markdown", self.markdown_button.click,
                                      "Ctrl+Shift+M", checkable=True)
        view_menu.addSeparator()
        self.dark_action = action(view_menu, "Dark Mode", lambda: self.set_dark_mode(self.dark_action.isChecked()),
                                  "Ctrl+Shift+D", checkable=True)
        self.dark_action.setChecked(self.theme is DARK)

    def _build_timers(self) -> None:
        self.autosave_timer = QTimer(self, singleShot=True, interval=AUTOSAVE_MS)
        self.autosave_timer.timeout.connect(self.save_current)
        self.counts_timer = QTimer(self, singleShot=True, interval=COUNTS_DEBOUNCE_MS)
        self.counts_timer.timeout.connect(self._update_counts)
        self.saved_timer = QTimer(self, singleShot=True, interval=SAVED_INDICATOR_MS)
        self.saved_timer.timeout.connect(lambda: self.saved_label.setText(""))
        self.backup_timer = QTimer(self, interval=BACKUP_CHECK_MS)
        self.backup_timer.timeout.connect(lambda: self.store.backup_if_needed(paths.backups_dir()))
        self.backup_timer.start()

    def _connect(self) -> None:
        s = self.store
        s.folders_changed.connect(self.refresh_explorer)
        s.notes_changed.connect(self.refresh_explorer)
        s.note_saved.connect(lambda _id: self.refresh_explorer())
        s.error_occurred.connect(self._show_error)

        e = self.explorer
        e.note_selected.connect(self.open_note)
        e.new_note_requested.connect(self.new_note)
        e.new_folder_requested.connect(self.new_folder)
        e.edit_folder_requested.connect(self.edit_folder)
        e.delete_folder_requested.connect(self.delete_folder)
        e.delete_note_requested.connect(self.delete_note)
        e.move_note_requested.connect(self.store.move_note)
        e.pin_toggled.connect(self._toggle_pin)
        e.notes_dropped.connect(self._on_notes_dropped)
        e.folder_dropped.connect(self._on_folder_dropped)
        e.search_changed.connect(self._on_search_changed)
        e.collapse_requested.connect(self.toggle_sidebar)
        e.expanded_changed.connect(lambda ids: self.settings.setValue("expanded_folders", [str(i) for i in ids]))

        self.markdown_button.toggled.connect(self._set_show_markdown)
        self.theme_switch.clicked.connect(self.set_dark_mode)

        self.title_edit.textEdited.connect(self._on_title_edited)
        self.title_edit.editingFinished.connect(self._on_title_finished)
        self.title_edit.returnPressed.connect(self.editor.setFocus)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.column_margin_changed.connect(self._align_title)

    @staticmethod
    def _display_path(path: Path) -> str:
        """The path with the home folder shortened to ~."""
        try:
            return "~/" + str(path.relative_to(Path.home()))
        except ValueError:
            return str(path)

    def _reveal_database(self) -> None:
        path = paths.db_path()
        if sys.platform == "darwin":
            QProcess.startDetached("open", ["-R", str(path)])  # Finder, with the file selected
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _align_title(self, left: int) -> None:
        margins = self.title_row.layout().contentsMargins()
        self.title_row.layout().setContentsMargins(left, margins.top(), left, margins.bottom())

    # ----- theme ----------------------------------------------------------

    def apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(build_qss(self.theme))
        self.editor.set_theme(self.theme)
        self.explorer.set_theme(self.theme)
        self.sidebar_frame.set_theme(self.theme)
        self.theme_switch.set_theme(self.theme)

    def set_dark_mode(self, dark: bool) -> None:
        """Single entry point for the header switch, View menu item and Ctrl+Shift+D."""
        self.theme = DARK if dark else LIGHT
        self.settings.setValue("dark_mode", dark)
        self.dark_action.setChecked(dark)
        if self.theme_switch.isChecked() != dark:
            self.theme_switch.setChecked(dark)
        self.apply_theme()

    def _set_show_markdown(self, show: bool) -> None:
        self.markdown_action.setChecked(show)
        self.settings.setValue("show_markdown", show)
        self.editor.set_reveal_all(show)

    # ----- explorer (observer callback) ----------------------------------

    def refresh_explorer(self) -> None:
        folders = self.store.list_folders()
        notes = self.store.list_notes(("all", None), self._search)
        current_id = self.current_note.id if self.current_note else None
        self.explorer.refresh(folders, notes, current_id, self._search)

    # ----- notes ----------------------------------------------------------

    def open_note(self, note_id: int) -> None:
        if self.current_note and self.current_note.id == note_id:
            return
        self.flush_save()
        note = self.store.get_note(note_id)
        if note is None:
            self._show_empty()
            return
        self.current_note = note
        self._loading = True
        self.title_edit.setText(note.title)
        self.editor.set_markdown(note.markdown_content)
        self._loading = False
        self._dirty = False
        self.title_edit.setEnabled(True)
        self.content_stack.setCurrentWidget(self.page)
        self._update_counts()
        self.settings.setValue("last_note_id", note.id)
        self.settings.sync()

    def _show_empty(self) -> None:
        self.current_note = None
        self._dirty = False
        self.autosave_timer.stop()
        self._loading = True
        self.title_edit.clear()
        self.editor.set_markdown("")
        self._loading = False
        self.title_edit.setEnabled(False)
        self.content_stack.setCurrentWidget(self.empty_state)
        self.settings.remove("last_note_id")

    def new_note(self, folder_id: Optional[int] = None) -> None:
        self.flush_save()
        if self._search:
            self.explorer.search.clear()  # make sure the new note is visible
        if folder_id is not None:
            self.explorer.expand_folder(folder_id)
        note_id = self.store.create_note(DEFAULT_NOTE_TITLE, folder_id)
        if note_id is None:
            return
        self.open_note(note_id)
        self.explorer.select_note(note_id)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def delete_note(self, note_id: int) -> None:
        note = self.store.get_note(note_id)
        if note is None:
            return
        if not self._confirm_delete_note(note):
            return
        if self.current_note and self.current_note.id == note_id:
            self._show_empty()
        self.store.delete_note(note_id)

    def _confirm_delete_note(self, note: Note) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete Note")
        box.setText(f"Delete “{note.title}”?")
        box.setInformativeText("This cannot be undone.")
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is delete

    def _toggle_pin(self, note_id: int) -> None:
        note = self.store.get_note(note_id)
        if note is not None:
            self.store.set_pinned(note_id, not note.is_pinned)

    def _on_notes_dropped(self, note_ids: List[int], folder_id: Optional[int], anchor: str,
                          anchor_id: Optional[int]) -> None:
        """Insert the dragged notes into folder_id's order at the drop point and save it."""
        scope = ("folder", folder_id) if folder_id is not None else ("standalone", None)
        group = self.store.list_notes(scope)  # full group, unfiltered by search, in display order
        moving = [n for n in (self.store.get_note(i) for i in note_ids) if n is not None]
        if not moving:
            return
        moving_ids = {n.id for n in moving}
        rest = [n for n in group if n.id not in moving_ids]
        ids = [n.id for n in rest]
        if anchor == "top":
            index = 0
        elif anchor in ("before", "after") and anchor_id in ids:
            index = ids.index(anchor_id) + (1 if anchor == "after" else 0)
        else:
            index = len(rest)
        # Pinned notes always sort first, so keep each dragged note on its side of that line.
        pinned = sum(1 for n in rest if n.is_pinned)
        index = min(index, pinned) if all(n.is_pinned for n in moving) else max(index, pinned)
        ordered = rest[:index] + moving + rest[index:]
        self.store.reorder_notes(folder_id, [n.id for n in ordered])
        if folder_id is not None:
            self.explorer.expand_folder(folder_id)

    def _on_folder_dropped(self, folder_id: int, anchor: str, anchor_id: int) -> None:
        """Move folder_id before/after anchor_id in the sidebar's folder order."""
        ids = [f.id for f in self.store.list_folders() if f.id != folder_id]
        if anchor_id not in ids:
            return
        index = ids.index(anchor_id) + (1 if anchor == "after" else 0)
        self.store.reorder_folders(ids[:index] + [folder_id] + ids[index:])

    def _on_search_changed(self, text: str) -> None:
        self._search = text
        self.flush_save()  # search runs in SQL, so unsaved edits must be written first
        self.refresh_explorer()
        self.editor.set_search(text)

    def _focus_search(self) -> None:
        if not self._sidebar_visible():
            self.toggle_sidebar()
        self.explorer.search.setFocus()
        self.explorer.search.selectAll()

    # ----- editing & saving ----------------------------------------------

    def _on_title_edited(self, text: str) -> None:
        if not self.current_note:
            return
        title = sanitize_title(text)
        self.current_note.title = title
        if self.store.update_note_title(self.current_note.id, title):
            self._show_saved()

    def _on_title_finished(self) -> None:
        if self.current_note and not self.title_edit.text().strip():
            self.title_edit.setText(DEFAULT_NOTE_TITLE)

    def _on_text_changed(self) -> None:
        if self._loading or not self.current_note:
            return
        # Re-highlighting can emit textChanged without an edit, so ask the document
        # whether its text really changed (cheap, unlike comparing the full text).
        self._dirty = self.editor.document().isModified()
        if self._dirty and not self.autosave_timer.isActive():  # every 2s while typing
            self.autosave_timer.start()
        self.counts_timer.start()  # word count once typing pauses, not per keystroke

    def save_current(self) -> None:
        if not self.current_note or not self._dirty:
            return
        text = self.editor.markdown()
        if self.store.update_note_content(self.current_note.id, text, self.editor.word_count()):
            self.current_note.markdown_content = text
            self.editor.document().setModified(False)
            self._dirty = False
            self._show_saved()

    def flush_save(self) -> None:
        self.autosave_timer.stop()
        self.save_current()

    def _show_saved(self) -> None:
        self.saved_label.setText("Saved")
        self.saved_timer.start()

    def _update_counts(self) -> None:
        self.counts_timer.stop()
        words = self.editor.word_count() if self.current_note else 0
        chars = self.editor.character_count() if self.current_note else 0
        self.count_label.setText(f"{words} word{'s' if words != 1 else ''} · {chars} characters")

    # ----- folders --------------------------------------------------------

    def _folder_names(self, exclude_id: Optional[int] = None) -> List[str]:
        return [f.name for f in self.store.list_folders() if f.id != exclude_id]

    def new_folder(self) -> None:
        dialog = FolderDialog(self, "New Folder", existing_names=self._folder_names())
        if dialog.exec():
            folder_id = self.store.create_folder(dialog.folder_name(), dialog.color_tag())
            if folder_id is not None:
                self.explorer.expand_folder(folder_id)

    def edit_folder(self, folder_id: int) -> None:
        folder = self.store.get_folder(folder_id)
        if folder is None:
            return
        dialog = FolderDialog(self, "Rename Folder", folder.name, folder.color_tag,
                              existing_names=self._folder_names(exclude_id=folder_id))
        if dialog.exec():
            self.store.update_folder(folder_id, dialog.folder_name(), dialog.color_tag())

    def delete_folder(self, folder_id: int) -> None:
        folder = self.store.get_folder(folder_id)
        if folder is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete Folder")
        box.setText(f"Delete folder “{folder.name}”?")
        box.setInformativeText("Notes will become standalone.")
        keep = box.addButton("Delete Folder", QMessageBox.ButtonRole.AcceptRole)
        purge = box.addButton("Delete Folder and Notes", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (keep, purge):
            return
        delete_notes = clicked is purge
        if delete_notes and self.current_note and self.current_note.folder_id == folder_id:
            self._show_empty()
        self.flush_save()
        self.store.delete_folder(folder_id, delete_notes)
        if self.current_note:
            refreshed = self.store.get_note(self.current_note.id)
            if refreshed:
                self.current_note.folder_id = refreshed.folder_id

    # ----- sidebar --------------------------------------------------------

    def _sidebar_visible(self) -> bool:
        return self._sidebar_target  # open, or animating open

    def _apply_sidebar_state(self, shown: bool) -> None:
        """Settle the column at rest: open at the user's width, or minimized to a tile."""
        frame = self.sidebar_frame
        minimized_width = frame.minimized_column_width()
        frame.setMinimumWidth(minimized_width)
        width = self._sidebar_width if shown else minimized_width
        total = sum(self.splitter.sizes()) or self.width()
        self.splitter.setSizes([width, max(200, total - width)])
        frame.set_progress(1.0 if shown else 0.0)
        self.explorer.set_minimized(not shown)
        self.splitter.handle(EDITOR).setEnabled(shown)  # no resizing a minimized tile

    def toggle_sidebar(self) -> None:
        """Minimize the sidebar to a tile around its toggle button, or open it again.

        The toggle button stays in place throughout; the column width and the card's
        size animate together from one progress value."""
        frame = self.sidebar_frame
        animating = self._animation is not None and self._animation.state() == QVariantAnimation.State.Running
        if animating:
            self._animation.stop()  # stop() doesn't emit finished; a second toggle reverses
        elif self._sidebar_target:
            self._sidebar_width = self.splitter.sizes()[SIDEBAR]  # remember the user's width
        showing = not self._sidebar_target
        self._sidebar_target = showing
        self.explorer.set_minimized(not showing)
        self.splitter.handle(EDITOR).setEnabled(False)

        open_width = self._sidebar_width
        minimized_width = frame.minimized_column_width()
        total = sum(self.splitter.sizes())
        start, end = frame.progress, (1.0 if showing else 0.0)
        frame.begin_animation(open_width)

        def step(t) -> None:
            progress = start + (end - start) * t
            column = round(minimized_width + (open_width - minimized_width) * progress)
            self.splitter.setSizes([column, total - column])
            frame.set_progress(progress)

        def done() -> None:
            frame.end_animation()
            self._apply_sidebar_state(showing)

        anim = QVariantAnimation(self)
        anim.setDuration(round(PANEL_ANIMATION_MS * max(0.3, abs(end - start))))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        self._animation = anim
        anim.start()

    # ----- errors ---------------------------------------------------------

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Simple Notes", f"{message}\n\nYour other notes are unaffected.")

    # ----- persistence of window state -----------------------------------

    def _restore_state(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1200, 760)
        width = self.settings.value("sidebar_width", DEFAULT_SIDEBAR_WIDTH, type=int)
        self._sidebar_width = width if width > 60 else DEFAULT_SIDEBAR_WIDTH
        visible = self.settings.value("sidebar_visible", True, type=bool)
        self.splitter.setSizes([self._sidebar_width, max(400, self.width() - self._sidebar_width)])
        self._sidebar_target = visible

        expanded = self.settings.value("expanded_folders", []) or []
        if isinstance(expanded, str):
            expanded = [expanded]
        self.explorer.set_expanded(int(x) for x in expanded if str(x).isdigit())
        self.markdown_button.setChecked(self.settings.value("show_markdown", False, type=bool))
        self.refresh_explorer()
        self._apply_sidebar_state(visible)

        last_id = self.settings.value("last_note_id", None)
        note = self.store.get_note(int(last_id)) if last_id not in (None, "") else None
        if note is not None:
            self.open_note(note.id)
            self.explorer.select_note(note.id)
        else:
            self._show_empty()

    def closeEvent(self, event) -> None:
        self.flush_save()
        sizes = self.splitter.sizes()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("sidebar_visible", self._sidebar_target)
        open_now = self._sidebar_target and (self._animation is None or
                                             self._animation.state() != QVariantAnimation.State.Running)
        self.settings.setValue("sidebar_width", sizes[SIDEBAR] if open_now else self._sidebar_width)
        self.settings.sync()
        super().closeEvent(event)
