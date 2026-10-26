"""Main window: wires the store (data) to the sidebar, the note list and the live editor."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import (
    QEasingCurve, QProcess, QRegularExpression, QSettings, Qt, QTimer, QUrl, QVariantAnimation,
)
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QStackedWidget, QVBoxLayout,
    QWidget,
)

from . import mac_chrome, paths
from .editor import LiveEditor
from .icons import make_icon, set_button_icon
from .models import DEFAULT_NOTE_TITLE, TITLE_ALLOWED_CHARS, Note, now_iso, sanitize_title
from .store import NoteStore
from .styles import DARK, LIGHT, Fonts, build_qss, set_current_theme
from .widgets import (
    TITLEBAR_CENTER, TRAFFIC_LIGHTS_LEFT, TRAFFIC_LIGHTS_WIDTH, FolderDialog, HairlineSplitter, MessageDialog, NotesPanel, PathLabel,
    Scope, Sidebar, SidebarCard, ThemeSwitch, WindowDragFilter, format_long_date, soft_shadow, styled_panel,
    tool_button,
)

AUTOSAVE_MS = 2000
SAVED_INDICATOR_MS = 2000
COUNTS_DEBOUNCE_MS = 300
BACKUP_CHECK_MS = 60 * 60 * 1000
PANEL_ANIMATION_MS = 200
DEFAULT_SIDEBAR_WIDTH = 250
DEFAULT_LIST_WIDTH = 320
PILL = 34  # toolbar pill height; pills are centered on the traffic lights' line

# Draw the window's content under a transparent title bar (Qt 6.9+, macOS), so the
# traffic lights sit inside the sidebar card as in Notes. Elsewhere: a normal title bar.
EXPANDED_TITLE_BAR = sys.platform == "darwin" and hasattr(Qt.WindowType, "ExpandedClientAreaHint")


def _pill(*buttons) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Pill")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.setSpacing(4)
    for button in buttons:
        button.setFixedHeight(PILL - 6)
        layout.addWidget(button)
    frame.setFixedHeight(PILL)
    return frame


class MainWindow(QMainWindow):
    def __init__(self, store: NoteStore, settings: QSettings, fonts: Fonts):
        super().__init__()
        self.store = store
        self.settings = settings
        self.fonts = fonts
        self.theme = DARK if settings.value("dark_mode", False, type=bool) else LIGHT
        set_current_theme(self.theme)

        self.current_note: Optional[Note] = None
        self.scope: Scope = ("all", None)
        self._dirty = False
        self._loading = False
        self._search = ""
        self._sidebar_width = DEFAULT_SIDEBAR_WIDTH
        self._sidebar_target = True  # where the sidebar is (or is animating) to: shown or hidden
        self._sidebar_progress = 1.0
        self._animation: Optional[QVariantAnimation] = None

        self.setWindowTitle("Simple Notes")
        self.setMinimumSize(760, 420)
        if EXPANDED_TITLE_BAR:
            self.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
            # Layouts would otherwise keep clear of the (now invisible) title bar.
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self._traffic_width = TRAFFIC_LIGHTS_WIDTH if EXPANDED_TITLE_BAR else 0
        self._build_ui()
        self._build_menus()
        self._build_timers()
        self._connect()
        self.apply_theme()
        self._restore_state()

    # ----- construction ---------------------------------------------------

    def _build_ui(self) -> None:
        self.location_label = PathLabel(self._display_path(paths.db_path()))
        self.location_label.setToolTip(
            "All notes are stored in this local SQLite database.\nClick to show it in "
            + ("Finder" if sys.platform == "darwin" else "your file manager") + ".")
        self.location_label.clicked.connect(self._reveal_database)

        self.sidebar = Sidebar(self.location_label)
        # Card top + half the header = the traffic lights' center line.
        self.sidebar.set_header_height(2 * (TITLEBAR_CENTER - SidebarCard.GAP), self._traffic_width)
        self.sidebar_column = SidebarCard(self.sidebar)

        self.notes_panel = NotesPanel()
        self.notes_panel.set_header_height(2 * TITLEBAR_CENTER + 4, self._traffic_width + 40)

        self.editor_panel = QWidget()
        styled_panel(self.editor_panel, "EditorPanel")
        panel_layout = QVBoxLayout(self.editor_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self._build_toolbar())

        # The page: a big title above the writing column, the date and counts bottom right.
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
        title_layout.setContentsMargins(48, 28, 48, 4)
        title_layout.addWidget(self.title_edit)
        page_layout.addWidget(self.title_row)
        self.editor = LiveEditor(self.theme, self.fonts)
        page_layout.addWidget(self.editor, 1)
        self.date_label = QLabel("")
        self.date_label.setObjectName("DateLabel")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.date_label.setContentsMargins(16, 6, 20, 10)
        page_layout.addWidget(self.date_label)
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

        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.notes_panel)
        self.splitter.addWidget(self.editor_panel)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(1, 1)
        self.notes_panel.setMinimumWidth(220)
        self.editor_panel.setMinimumWidth(320)

        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        styled_panel(central, "EditorPanel")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar_column)
        row.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        # Empty parts of the header rows move the window, like a title bar.
        self._drag_filter = WindowDragFilter(self)
        for widget in (self.sidebar.header, self.notes_panel.header, self.toolbar):
            widget.installEventFilter(self._drag_filter)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._native_chrome():
            mac_chrome.hide_title(self)
        self._place_traffic_lights()

    def _native_chrome(self) -> bool:
        return EXPANDED_TITLE_BAR and QApplication.platformName() == "cocoa"  # winId() is an NSView only there

    def _place_traffic_lights(self) -> None:
        """AppKit resets the traffic lights on every relayout, so this runs after each
        resize or state change, deferred until AppKit has done its own layout."""
        if self._native_chrome() and not self.isFullScreen():
            QTimer.singleShot(0, lambda: mac_chrome.place_traffic_lights(self, TITLEBAR_CENTER, TRAFFIC_LIGHTS_LEFT))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_traffic_lights()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        self._place_traffic_lights()

    def _build_toolbar(self) -> QWidget:
        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(16, TITLEBAR_CENTER - PILL // 2, 16, 0)
        bar.setSpacing(12)
        bar.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.compose_button = tool_button("", "New note (Ctrl+N)")
        self.compose_pill = _pill(self.compose_button)
        bar.addWidget(self.compose_pill)
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("SavedLabel")
        self.saved_label.setFixedHeight(PILL)
        bar.addWidget(self.saved_label)
        bar.addStretch(1)

        self.markdown_button = tool_button("", "Show markdown (Ctrl+Shift+M)")
        self.markdown_button.setCheckable(True)
        self.format_pill = _pill(self.markdown_button)
        bar.addWidget(self.format_pill)
        bar.addStretch(1)

        self.theme_switch = ThemeSwitch()
        self.theme_switch.setChecked(self.theme is DARK)
        switch_box = QWidget()
        switch_box.setFixedHeight(PILL)
        switch_layout = QHBoxLayout(switch_box)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.addWidget(self.theme_switch)
        bar.addWidget(switch_box)
        self.more_button = tool_button("", "More")
        self.more_pill = _pill(self.more_button)
        bar.addWidget(self.more_pill)

        self.search_field = QLineEdit()
        self.search_field.setObjectName("SearchField")
        self.search_field.setPlaceholderText("Search")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedSize(240, PILL)
        self._search_icon = self.search_field.addAction(make_icon("search", self.theme.muted),
                                                        QLineEdit.ActionPosition.LeadingPosition)
        bar.addWidget(self.search_field)
        self.toolbar.setFixedHeight(TITLEBAR_CENTER + PILL // 2 + 6)
        return self.toolbar

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
        action(file_menu, "New Note", lambda: self.new_note(self._scope_folder()), QKeySequence.StandardKey.New)
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

        side = self.sidebar
        side.scope_selected.connect(self._on_scope_selected)
        side.new_note_requested.connect(self.new_note)
        side.new_folder_requested.connect(self.new_folder)
        side.edit_folder_requested.connect(self.edit_folder)
        side.delete_folder_requested.connect(self.delete_folder)
        side.notes_moved.connect(self._move_notes)
        side.folder_dropped.connect(self._on_folder_dropped)
        side.collapse_requested.connect(self.toggle_sidebar)
        self.sidebar_column.resized.connect(self._on_sidebar_resized)

        notes = self.notes_panel
        notes.note_selected.connect(self.open_note)
        notes.new_note_requested.connect(lambda: self.new_note(self._scope_folder()))
        notes.new_folder_requested.connect(self.new_folder)
        notes.edit_folder_requested.connect(self.edit_folder)
        notes.delete_folder_requested.connect(self.delete_folder)
        notes.delete_note_requested.connect(self.delete_note)
        notes.move_note_requested.connect(lambda note_id, folder_id: self._move_notes([note_id], folder_id))
        notes.pin_toggled.connect(self._toggle_pin)
        notes.notes_dropped.connect(self._on_notes_dropped)
        notes.expand_requested.connect(self.toggle_sidebar)

        self.compose_button.clicked.connect(lambda: self.new_note(self._scope_folder()))
        self.markdown_button.toggled.connect(self._set_show_markdown)
        self.more_button.clicked.connect(self._editor_menu)
        self.theme_switch.clicked.connect(self.set_dark_mode)
        self.search_field.textChanged.connect(self._on_search_changed)

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
        t = self.theme
        set_current_theme(t)
        QApplication.instance().setStyleSheet(build_qss(t))
        self.editor.set_theme(t)
        self.sidebar.set_theme(t)
        self.sidebar_column.set_theme(t)
        self.notes_panel.set_theme(t)
        self.splitter.set_theme(t)
        self.theme_switch.set_theme(t)
        for button, icon in ((self.compose_button, "compose"), (self.markdown_button, "code"),
                             (self.more_button, "more")):
            set_button_icon(button, icon, t.text, t.text, 18)
        self._search_icon.setIcon(make_icon("search", t.muted))
        dark = t is DARK
        for widget in (self.compose_pill, self.format_pill, self.more_pill, self.search_field):
            soft_shadow(widget, dark)

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

    # ----- sidebar and note list (observer callback) ---------------------

    def _scope_folder(self) -> Optional[int]:
        """Where new notes go: the selected folder, else the standalone notes."""
        return self.scope[1] if self.scope[0] == "folder" else None

    def refresh_explorer(self) -> None:
        folders = self.store.list_folders()
        if self.scope[0] == "folder" and self.scope[1] not in {f.id for f in folders}:
            self.scope = ("all", None)
        per_folder, standalone, total = self.store.note_counts()
        self.sidebar.refresh(folders, per_folder, standalone, total, self.scope)

        kind, folder_id = self.scope
        if self._search:
            notes = self.store.list_notes(("all", None), self._search)
            title = "Search"
            subtitle = f"{len(notes)} result{'s' if len(notes) != 1 else ''}"
        else:
            notes = self.store.list_notes(self.scope)
            names = {f.id: f.name for f in folders}
            title = {"all": "All Notes", "standalone": "Notes"}.get(kind) or names.get(folder_id, "")
            subtitle = f"{len(notes)} note{'s' if len(notes) != 1 else ''}"
        if self._search or kind == "all":
            # Across groups there's no manual order: pinned first, then most recently edited.
            notes.sort(key=lambda n: n.modified_date, reverse=True)
            notes.sort(key=lambda n: not n.is_pinned)
        current_id = self.current_note.id if self.current_note else None
        self.notes_panel.refresh(notes, folders, self.scope, title, subtitle, current_id,
                                 reorderable=not self._search and kind != "all")

    def _on_scope_selected(self, scope: Scope) -> None:
        self.flush_save()
        self.scope = (scope[0], scope[1])
        self.settings.setValue("scope", "folder:%d" % scope[1] if scope[0] == "folder" else scope[0])
        if self._search:
            self.search_field.clear()  # refreshes
        else:
            self.refresh_explorer()
        ids = self.notes_panel.note_ids()
        if self.current_note is None or self.current_note.id not in ids:
            if ids:
                self.open_note(ids[0])
                self.notes_panel.select_note(ids[0])
            else:
                self._show_empty()

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
        self.notes_panel.select_note(None)
        self.settings.remove("last_note_id")

    def new_note(self, folder_id: Optional[int] = None) -> None:
        self.flush_save()
        if self._search:
            self.search_field.clear()  # make sure the new note is visible
        if folder_id != self._scope_folder() and self.scope[0] != "all":
            self.scope = ("folder", folder_id) if folder_id is not None else ("standalone", None)
        note_id = self.store.create_note(DEFAULT_NOTE_TITLE, folder_id)
        if note_id is None:
            return
        self.open_note(note_id)
        self.refresh_explorer()
        self.notes_panel.select_note(note_id)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def delete_note(self, note_id: int) -> None:
        note = self.store.get_note(note_id)
        if note is None:
            return
        answer = MessageDialog.ask(self, "Delete Note", f"Delete “{note.title}”?", "This cannot be undone.",
                                   [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], default="cancel")
        if answer != "delete":
            return
        ids = self.notes_panel.note_ids()
        neighbour = None
        if note_id in ids and len(ids) > 1:  # open the next note in the list, like Notes
            index = ids.index(note_id)
            neighbour = ids[index + 1] if index + 1 < len(ids) else ids[index - 1]
        was_open = self.current_note is not None and self.current_note.id == note_id
        if was_open:
            self._show_empty()
        self.store.delete_note(note_id)
        if was_open and neighbour is not None:
            self.open_note(neighbour)
            self.notes_panel.select_note(neighbour)

    def _toggle_pin(self, note_id: int) -> None:
        note = self.store.get_note(note_id)
        if note is not None:
            self.store.set_pinned(note_id, not note.is_pinned)
            if self.current_note and self.current_note.id == note_id:
                self.current_note.is_pinned = not note.is_pinned

    def _move_notes(self, note_ids: List[int], folder_id: Optional[int]) -> None:
        self.flush_save()
        for note_id in note_ids:
            self.store.move_note(note_id, folder_id)
            if self.current_note and self.current_note.id == note_id:
                self.current_note.folder_id = folder_id

    def _on_notes_dropped(self, note_ids: List[int], anchor: str, anchor_id: Optional[int]) -> None:
        """Insert the dragged notes into the shown group's order at the drop point and save it."""
        folder_id = self._scope_folder()
        scope = ("folder", folder_id) if folder_id is not None else ("standalone", None)
        group = self.store.list_notes(scope)  # full group, in display order
        moving = [n for n in (self.store.get_note(i) for i in note_ids) if n is not None]
        if not moving:
            return
        moving_ids = {n.id for n in moving}
        rest = [n for n in group if n.id not in moving_ids]
        ids = [n.id for n in rest]
        if anchor in ("before", "after") and anchor_id in ids:
            index = ids.index(anchor_id) + (1 if anchor == "after" else 0)
        else:
            index = len(rest)
        # Pinned notes always sort first, so keep each dragged note on its side of that line.
        pinned = sum(1 for n in rest if n.is_pinned)
        index = min(index, pinned) if all(n.is_pinned for n in moving) else max(index, pinned)
        ordered = rest[:index] + moving + rest[index:]
        self.store.reorder_notes(folder_id, [n.id for n in ordered])

    def _on_folder_dropped(self, folder_id: int, anchor: str, anchor_id: int) -> None:
        """Move folder_id before/after anchor_id in the sidebar's folder order."""
        ids = [f.id for f in self.store.list_folders() if f.id != folder_id]
        if anchor_id not in ids:
            return
        index = ids.index(anchor_id) + (1 if anchor == "after" else 0)
        self.store.reorder_folders(ids[:index] + [folder_id] + ids[index:])

    def _on_search_changed(self, text: str) -> None:
        self._search = text.strip()
        self.flush_save()  # search runs in SQL, so unsaved edits must be written first
        self.refresh_explorer()
        self.editor.set_search(text)

    def _focus_search(self) -> None:
        self.search_field.setFocus()
        self.search_field.selectAll()

    # ----- toolbar ---------------------------------------------------------

    def _editor_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(self.markdown_action)
        if self.current_note:
            note = self.store.get_note(self.current_note.id) or self.current_note
            menu.addSeparator()
            self.notes_panel.note_menu(note, menu)
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    # ----- editing & saving ----------------------------------------------

    def _on_title_edited(self, text: str) -> None:
        if not self.current_note:
            return
        title = sanitize_title(text)
        self.current_note.title = title
        if self.store.update_note_title(self.current_note.id, title):
            self.current_note.modified_date = now_iso()
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
            self.current_note.modified_date = now_iso()
            self.editor.document().setModified(False)
            self._dirty = False
            self._show_saved()

    def flush_save(self) -> None:
        self.autosave_timer.stop()
        self.save_current()

    def _show_saved(self) -> None:
        self.saved_label.setText("Saved")
        self.saved_timer.start()
        self._update_counts()

    def _update_counts(self) -> None:
        self.counts_timer.stop()
        if not self.current_note:
            self.date_label.setText("")
            return
        words = self.editor.word_count()
        chars = self.editor.character_count()
        self.date_label.setText(f"{format_long_date(self.current_note.modified_date)}   ·   "
                                f"{words} word{'s' if words != 1 else ''}, {chars} characters")

    # ----- folders --------------------------------------------------------

    def _folder_names(self, exclude_id: Optional[int] = None) -> List[str]:
        return [f.name for f in self.store.list_folders() if f.id != exclude_id]

    def new_folder(self) -> None:
        names = self._folder_names()
        name, n = "New Folder", 2
        while name.lower() in {x.lower() for x in names}:
            name, n = f"New Folder {n}", n + 1
        dialog = FolderDialog(self, "New Folder", name, existing_names=names)
        if dialog.exec():
            folder_id = self.store.create_folder(dialog.folder_name(), dialog.color_tag())
            if folder_id is not None:
                self._on_scope_selected(("folder", folder_id))

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
        answer = MessageDialog.ask(
            self, "Delete Folder", f"Delete folder “{folder.name}”?",
            "Its notes will move to Notes, unless you delete them too.",
            [("cancel", "Cancel", ""), ("purge", "Delete Folder and Notes", "destructive"),
             ("keep", "Delete Folder", "primary")], default="keep")
        if answer not in ("keep", "purge"):
            return
        delete_notes = answer == "purge"
        if delete_notes and self.current_note and self.current_note.folder_id == folder_id:
            self._show_empty()
        self.flush_save()
        self.store.delete_folder(folder_id, delete_notes)
        if self.current_note:
            refreshed = self.store.get_note(self.current_note.id)
            if refreshed:
                self.current_note.folder_id = refreshed.folder_id

    # ----- sidebar --------------------------------------------------------

    def _set_sidebar_progress(self, progress: float) -> None:
        """0 = hidden, 1 = open at the user's width. The note list's header makes room
        for the traffic lights (and a show-sidebar button) as the sidebar goes."""
        self._sidebar_progress = progress
        self.sidebar_column.setFixedWidth(round(self._sidebar_width * progress))
        self.notes_panel.set_lead_progress(1.0 - progress)

    def _on_sidebar_resized(self, width: int) -> None:
        self._sidebar_width = width
        self._set_sidebar_progress(1.0)

    def toggle_sidebar(self) -> None:
        """Slide the sidebar card out to the left, or back in. A toggle while it's
        animating reverses it."""
        if self._animation is not None and self._animation.state() == QVariantAnimation.State.Running:
            self._animation.stop()  # stop() doesn't emit finished
        showing = not self._sidebar_target
        self._sidebar_target = showing
        column = self.sidebar_column
        column.show()
        column.freeze(self._sidebar_width)  # the card keeps its open width and slides
        start, end = self._sidebar_progress, (1.0 if showing else 0.0)

        def step(t) -> None:
            self._set_sidebar_progress(start + (end - start) * t)

        def done() -> None:
            column.freeze(None)
            column.setVisible(showing)  # hidden: keep Tab focus out of it

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
        MessageDialog.ask(self, "Simple Notes", message, "Your other notes are unaffected.",
                          [("ok", "OK", "primary")], default="ok")

    # ----- persistence of window state -----------------------------------

    def _restore_state(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1280, 800)
        width = self.settings.value("sidebar_width", DEFAULT_SIDEBAR_WIDTH, type=int)
        self._sidebar_width = max(SidebarCard.MIN_WIDTH, min(SidebarCard.MAX_WIDTH, width))
        visible = self.settings.value("sidebar_visible", True, type=bool)
        self._sidebar_target = visible
        self._set_sidebar_progress(1.0 if visible else 0.0)
        self.sidebar_column.setVisible(visible)
        list_width = self.settings.value("list_width", DEFAULT_LIST_WIDTH, type=int)
        self.splitter.setSizes([list_width, max(400, self.width() - list_width)])

        saved_scope = str(self.settings.value("scope", "all") or "all")
        if saved_scope.startswith("folder:") and saved_scope[7:].isdigit():
            self.scope = ("folder", int(saved_scope[7:]))
        elif saved_scope == "standalone":
            self.scope = ("standalone", None)
        self.markdown_button.setChecked(self.settings.value("show_markdown", False, type=bool))

        last_id = self.settings.value("last_note_id", None)
        note = self.store.get_note(int(last_id)) if str(last_id or "").isdigit() else None
        if note is not None:
            self.open_note(note.id)
        self.refresh_explorer()
        if note is None:
            self._show_empty()
        else:
            self.notes_panel.select_note(note.id)

    def closeEvent(self, event) -> None:
        self.flush_save()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("sidebar_visible", self._sidebar_target)
        self.settings.setValue("sidebar_width", self._sidebar_width)
        self.settings.setValue("list_width", self.splitter.sizes()[0])
        self.settings.sync()
        super().closeEvent(event)
