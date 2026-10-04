"""Sidebar explorer (folders + notes), theme switch and dialog widgets."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QEvent, QTimer, QEasingCurve, QMimeData, QRect, QRectF, QRegularExpression, QSize, Qt, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPixmap, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QAbstractButton, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QSizePolicy,
    QStyle, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QVBoxLayout, QWidget,
)

from .icons import paint_icon, set_button_icon
from .models import FOLDER_COLORS, MAX_FOLDER_NAME_LENGTH, Folder, Note, is_valid_folder_name
from .styles import Theme

NOTE_MIME = "application/x-simplenotes-note-ids"
FOLDER_MIME = "application/x-simplenotes-folder-id"


def color_dot(color: Optional[str], size: int = 10) -> QIcon:
    """A small filled circle (or a transparent placeholder, to keep text aligned)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    if color:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(1, 1, size - 2, size - 2)
        painter.end()
    return QIcon(pixmap)


def format_date(iso: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    now = datetime.now()
    if stamp.date() == now.date():
        return stamp.strftime("%H:%M")
    if stamp.year == now.year:
        return stamp.strftime("%b %d").replace(" 0", " ")
    return stamp.strftime("%Y-%m-%d")


def tool_button(text: str, tooltip: str, parent=None) -> QToolButton:
    button = QToolButton(parent)
    button.setText(text)
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoRaise(True)
    return button


def styled_panel(widget: QWidget, name: str) -> None:
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)



# ----- explorer (folders + notes in one tree) --------------------------------

KIND_ROLE = Qt.ItemDataRole.UserRole          # "folder" | "note"
ID_ROLE = Qt.ItemDataRole.UserRole + 1
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 2    # folder: note count, note: modified date
COLOR_ROLE = Qt.ItemDataRole.UserRole + 3     # folder color tag
PINNED_ROLE = Qt.ItemDataRole.UserRole + 4

ROW_HEIGHT = 30
ACTION_WIDTH = 24
ROOT_DROP = "root"


class _ExplorerDelegate(QStyledItemDelegate):
    """Paints rows Notion-style: arrow + icon + name, detail on the right,
    and a hover action (+ on folders, × on notes) in place of the detail."""

    def __init__(self, tree: "_ExplorerTree"):
        super().__init__(tree)
        self.tree = tree
        self.theme: Optional[Theme] = None

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        t = self.theme
        if t is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(0, 1, -2, -1)
        kind = index.data(KIND_ROLE)
        item_id = index.data(ID_ROLE)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        drop_target = kind == "folder" and self.tree.drop_target == item_id

        if selected or drop_target:
            background = t.selected
        elif hovered:
            background = t.hover
        else:
            background = None
        if background:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(rect, 4, 4)

        font = QFont(option.font)
        muted = QColor(t.muted)
        x = rect.left() + 4
        cy = rect.center().y()
        if kind == "folder":
            chevron = "chevron-down" if self.tree.isExpanded(index) else "chevron-right"
            paint_icon(painter, chevron, QRectF(x, cy - 7, 14, 14), t.muted, stroke=1.5)
            # A color-tagged folder draws its icon in that color.
            paint_icon(painter, "folder", QRectF(x + 18, cy - 8, 16, 16), index.data(COLOR_ROLE) or t.muted)
        else:
            pinned = index.data(PINNED_ROLE)
            paint_icon(painter, "pin" if pinned else "note", QRectF(x + 18, cy - 8, 16, 16),
                       t.accent if pinned else t.muted)
        x += 18 + 16 + 8

        # Right side: hover action, otherwise the detail text.
        action_rect = self.tree.action_rect(option.rect)
        if hovered:
            over = action_rect.contains(self.tree.viewport().mapFromGlobal(QCursor.pos()))
            center = QRectF(action_rect).center()
            if kind == "note":
                paint_icon(painter, "close", QRectF(center.x() - 7, center.y() - 7, 14, 14),
                           t.danger if over else t.muted, stroke=1.5)
            else:
                paint_icon(painter, "plus", QRectF(center.x() - 7, center.y() - 7, 14, 14),
                           t.text if over else t.muted)
            detail_width = 0
        else:
            detail = str(index.data(DETAIL_ROLE) or "")
            small = QFont(font)
            small.setPixelSize(11)
            painter.setFont(small)
            painter.setPen(muted)
            detail_width = painter.fontMetrics().horizontalAdvance(detail) + 8
            painter.drawText(QRectF(rect.right() - detail_width - 4, rect.top(), detail_width, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, detail)
            painter.setFont(font)

        right = (action_rect.left() if hovered else rect.right() - detail_width) - 6
        name_rect = QRectF(x, rect.top(), max(0.0, right - x), rect.height())
        if kind == "folder" or selected:
            font.setWeight(QFont.Weight.DemiBold if kind == "folder" else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(t.text))
        name = painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                                Qt.TextElideMode.ElideRight, int(name_rect.width()))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)
        painter.restore()


class _ExplorerTree(QTreeWidget):
    # note ids, folder id (None = standalone), anchor ("top" | "end" | "before" | "after"), anchor note id
    notes_dropped = pyqtSignal(list, object, str, object)
    folder_dropped = pyqtSignal(int, str, int)  # folder id, "before" | "after", anchor folder id
    action_clicked = pyqtSignal(str, int)     # ("folder", id) -> new note, ("note", id) -> delete

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)  # the delegate draws its own arrows
        self.setIndentation(16)
        self.setAnimated(True)
        self.setMouseTracking(True)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        # Copy, not Move: a Move would make Qt delete the dragged rows itself.
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(False)
        self.dragging = False  # while dragging a note, an extra row of drop space at the bottom
        self.itemExpanded.connect(lambda _item: self.updateGeometry())
        self.itemCollapsed.connect(lambda _item: self.updateGeometry())
        self.drop_target: Optional[object] = None  # folder id, ROOT_DROP or None
        self.drop_line: Optional[QRectF] = None    # insertion line between notes while dragging
        self.delegate = _ExplorerDelegate(self)
        self.setItemDelegate(self.delegate)

    # size to content ---------------------------------------------------------

    def visible_rows(self) -> int:
        rows = 0
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            rows += 1 + (item.childCount() if item.isExpanded() else 0)
        return rows

    def sizeHint(self) -> QSize:
        """Exactly tall enough for every visible row, so the sidebar card has no empty space."""
        rows = self.visible_rows() + (1 if self.dragging else 0)
        return QSize(super().sizeHint().width(), rows * ROW_HEIGHT + 2 * self.frameWidth() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, ROW_HEIGHT)  # beyond this the tree scrolls instead of growing the card

    def startDrag(self, actions) -> None:
        # The card has no empty space to drop on, so grow it by a row while dragging a
        # note: dropping there makes the note standalone.
        notes = [i for i in self.selectedItems() if i.data(0, KIND_ROLE) == "note"]
        self.dragging = bool(notes)
        self.updateGeometry()
        try:
            super().startDrag(actions)  # blocks until the drop
        finally:
            self.dragging = False
            self.updateGeometry()

    def action_rect(self, row_rect) -> QRect:
        return QRect(row_rect.right() - ACTION_WIDTH - 4, row_rect.top() + 3, ACTION_WIDTH, row_rect.height() - 6)

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            if self.action_rect(self.visualRect(index)).contains(event.position().toPoint()):
                self.action_clicked.emit(index.data(KIND_ROLE), index.data(ID_ROLE))
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self.viewport().update()  # repaint hover/× colors

    # drag & drop ---------------------------------------------------------

    def mimeTypes(self):
        return [NOTE_MIME, FOLDER_MIME]

    def mimeData(self, items):
        data = QMimeData()
        folders = [i for i in items if i.data(0, KIND_ROLE) == "folder"]
        if folders:
            data.setData(FOLDER_MIME, str(folders[0].data(0, ID_ROLE)).encode())
        else:
            ids = ",".join(str(i.data(0, ID_ROLE)) for i in items if i.data(0, KIND_ROLE) == "note")
            data.setData(NOTE_MIME, ids.encode())
        return data

    def _folder_drop_spot(self, pos):
        """For a dragged folder: (anchor, anchor folder id, line). Folders only move among
        folders: the upper half of a folder row drops before it; anywhere else in that
        folder's block (its row's lower half or its notes) drops after it."""
        folders = [self.topLevelItem(i) for i in range(self.topLevelItemCount())
                   if self.topLevelItem(i).data(0, KIND_ROLE) == "folder"]
        if not folders:
            return None
        for folder in folders:
            row = QRectF(self.visualItemRect(folder))
            bottom = row.bottom()
            if folder.isExpanded() and folder.childCount():
                bottom = self.visualItemRect(folder.child(folder.childCount() - 1)).bottom()
            if pos.y() <= bottom:
                before = pos.y() < row.center().y()
                y = row.top() if before else bottom + 1
                line = QRectF(row.left() + 4, y - 1, row.width() - 10, 2)
                return "before" if before else "after", folder.data(0, ID_ROLE), line
        last = folders[-1]
        row = QRectF(self.visualItemRect(last))
        bottom = row.bottom()
        if last.isExpanded() and last.childCount():
            bottom = self.visualItemRect(last.child(last.childCount() - 1)).bottom()
        return "after", last.data(0, ID_ROLE), QRectF(row.left() + 4, bottom, row.width() - 10, 2)

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def _drop_spot(self, pos):
        """Where a drop at pos lands: (folder id, anchor, anchor note id, highlight, line).

        On a note: before/after it (top or bottom half), in that note's folder.
        On a folder row: the top of that folder. On empty space: the end of the
        standalone notes."""
        item = self.itemAt(pos)
        if item is None:
            return None, "end", None, ROOT_DROP, None
        if item.data(0, KIND_ROLE) == "folder":
            folder_id = item.data(0, ID_ROLE)
            return folder_id, "top", None, folder_id, None
        rect = QRectF(self.visualItemRect(item))
        before = pos.y() < rect.center().y()
        parent = item.parent()
        folder_id = parent.data(0, ID_ROLE) if parent is not None else None
        y = rect.top() if before else rect.bottom() + 1
        line = QRectF(rect.left() + 4, y - 1, rect.width() - 10, 2)
        return folder_id, "before" if before else "after", item.data(0, ID_ROLE), None, line

    def _set_drop_feedback(self, highlight, line) -> None:
        if highlight != self.drop_target or line != self.drop_line:
            self.drop_target, self.drop_line = highlight, line
            self.viewport().update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(NOTE_MIME) or event.mimeData().hasFormat(FOLDER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(FOLDER_MIME):
            spot = self._folder_drop_spot(event.position().toPoint())
            self._set_drop_feedback(None, spot[2] if spot else None)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        if not event.mimeData().hasFormat(NOTE_MIME):
            event.ignore()
            return
        _, _, _, highlight, line = self._drop_spot(event.position().toPoint())
        self._set_drop_feedback(highlight, line)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._set_drop_feedback(None, None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(FOLDER_MIME):
            self._set_drop_feedback(None, None)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            raw = bytes(event.mimeData().data(FOLDER_MIME)).decode()
            spot = self._folder_drop_spot(event.position().toPoint())
            if raw.isdigit() and spot is not None and spot[1] != int(raw):
                self.folder_dropped.emit(int(raw), spot[0], spot[1])
            return
        folder_id, anchor, anchor_id, _, _ = self._drop_spot(event.position().toPoint())
        self._set_drop_feedback(None, None)
        if not event.mimeData().hasFormat(NOTE_MIME):
            event.ignore()
            return
        raw = bytes(event.mimeData().data(NOTE_MIME)).decode()
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if ids and anchor_id not in ids:  # dropping a note onto itself does nothing
            self.notes_dropped.emit(ids, folder_id, anchor, anchor_id)

    def paintEvent(self, event):
        super().paintEvent(event)
        theme = self.delegate.theme
        if theme is None or (self.drop_target != ROOT_DROP and self.drop_line is None):
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(theme.accent)
        if self.drop_target == ROOT_DROP:
            # Empty space: the note becomes standalone, at the end. Outline the whole list.
            painter.setPen(accent)
            painter.drawRoundedRect(QRectF(self.viewport().rect()).adjusted(1, 1, -1, -1), 4, 4)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(self.drop_line, 1, 1)
            painter.drawEllipse(QRectF(self.drop_line.left() - 3, self.drop_line.center().y() - 3, 6, 6))
        painter.end()


class NoteExplorer(QWidget):
    """Folders and notes in one tree. Standalone notes sit at the top level."""

    note_selected = pyqtSignal(int)
    new_note_requested = pyqtSignal(object)      # folder id or None
    new_folder_requested = pyqtSignal()
    edit_folder_requested = pyqtSignal(int)
    delete_folder_requested = pyqtSignal(int)
    delete_note_requested = pyqtSignal(int)
    move_note_requested = pyqtSignal(int, object)
    pin_toggled = pyqtSignal(int)
    notes_dropped = pyqtSignal(list, object, str, object)  # ids, folder id, anchor, anchor note id
    folder_dropped = pyqtSignal(int, str, int)             # folder id, "before" | "after", anchor folder id
    search_changed = pyqtSignal(str)
    collapse_requested = pyqtSignal()
    expanded_changed = pyqtSignal(list)          # expanded folder ids

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarContent")  # the rounded card behind it is FloatingPanel.card
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(4)
        # The hide/show chevron comes first: when the sidebar is minimized, the card
        # shrinks to a tile around exactly this button, so it never moves.
        self.toggle_button = tool_button("", "Hide sidebar (Ctrl+1)")
        self.toggle_button.clicked.connect(self.collapse_requested)
        header.addWidget(self.toggle_button)
        header.addSpacing(12)  # keeps the title out of the minimized tile
        title = QLabel("Notes")
        title.setObjectName("HeaderTitle")
        header.addWidget(title)
        header.addStretch(1)
        new_folder = tool_button("", "New folder (Ctrl+Shift+N)")
        new_folder.clicked.connect(self.new_folder_requested)
        header.addWidget(new_folder)
        new_note = tool_button("", "New note (Ctrl+N)")
        new_note.clicked.connect(lambda: self.new_note_requested.emit(None))
        header.addWidget(new_note)
        self._header_buttons = ((new_folder, "folder-plus"), (new_note, "plus"))
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search notes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.search_changed)
        layout.addWidget(self.search)

        self.tree = _ExplorerTree()
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemExpanded.connect(self._on_expansion)
        self.tree.itemCollapsed.connect(self._on_expansion)
        self.tree.notes_dropped.connect(self.notes_dropped)
        self.tree.folder_dropped.connect(self.folder_dropped)
        self.tree.action_clicked.connect(self._on_action)
        layout.addWidget(self.tree)

        self.empty_label = QLabel("No notes yet")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        self._notes: Dict[int, Note] = {}
        self._folders: List[Folder] = []
        self._expanded: set = set()
        self._refreshing = False
        self._minimized = False
        self._theme: Optional[Theme] = None
        # Never taller than the content (FloatingPanel puts a stretch underneath).
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    # state ------------------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        for button, icon in self._header_buttons:
            set_button_icon(button, icon, theme.muted, theme.text)
        self.set_minimized(self._minimized)
        self.tree.delegate.theme = theme
        self.tree.viewport().update()

    def set_minimized(self, minimized: bool) -> None:
        """Flip the toggle chevron; nothing else here changes (the card does the hiding)."""
        self._minimized = minimized
        self.toggle_button.setToolTip(("Show" if minimized else "Hide") + " sidebar (Ctrl+1)")
        self.search.setEnabled(not minimized)  # keep Tab focus out of the hidden part
        if self._theme is not None:
            set_button_icon(self.toggle_button, "chevron-right" if minimized else "chevron-left",
                            self._theme.muted, self._theme.text)

    def set_expanded(self, folder_ids) -> None:
        self._expanded = set(folder_ids)

    def expand_folder(self, folder_id: int) -> None:
        self._expanded.add(folder_id)
        item = self._folder_item(folder_id)
        if item is not None:
            item.setExpanded(True)
        self.expanded_changed.emit(sorted(self._expanded))

    def _folder_item(self, folder_id: int) -> Optional[QTreeWidgetItem]:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, KIND_ROLE) == "folder" and item.data(0, ID_ROLE) == folder_id:
                return item
        return None

    def refresh(self, folders: List[Folder], notes: List[Note], current_id: Optional[int], search: str = "") -> None:
        """Rebuild the tree; keeps expansion, selection and scroll position."""
        searching = bool(search.strip())
        scroll = self.tree.verticalScrollBar().value()
        self._refreshing = True
        self.tree.clear()
        self._notes = {n.id: n for n in notes}
        self._folders = folders
        known = {f.id for f in folders}
        by_folder: Dict[Optional[int], List[Note]] = {}
        for note in notes:
            by_folder.setdefault(note.folder_id if note.folder_id in known else None, []).append(note)

        current_item = None

        def note_item(note: Note) -> QTreeWidgetItem:
            nonlocal current_item
            item = QTreeWidgetItem([note.title])
            item.setData(0, KIND_ROLE, "note")
            item.setData(0, ID_ROLE, note.id)
            item.setData(0, DETAIL_ROLE, format_date(note.modified_date))
            item.setData(0, PINNED_ROLE, note.is_pinned)
            item.setToolTip(0, note.title)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
            if note.id == current_id:
                current_item = item
            return item

        for folder in folders:
            children = by_folder.get(folder.id, [])
            if searching and not children:
                continue
            item = QTreeWidgetItem([folder.name])
            item.setData(0, KIND_ROLE, "folder")
            item.setData(0, ID_ROLE, folder.id)
            item.setData(0, DETAIL_ROLE, len(children))
            item.setData(0, COLOR_ROLE, folder.color_tag)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsDragEnabled)
            self.tree.addTopLevelItem(item)
            for note in children:
                item.addChild(note_item(note))
            item.setExpanded(searching or folder.id in self._expanded)
        for note in by_folder.get(None, []):
            self.tree.addTopLevelItem(note_item(note))

        if current_item is not None:
            self.tree.setCurrentItem(current_item)
        self.tree.verticalScrollBar().setValue(scroll)
        self._refreshing = False
        empty = self.tree.topLevelItemCount() == 0
        self.empty_label.setText("No matching notes" if searching else "No notes yet — press + to start")
        self.empty_label.setVisible(empty)
        self.tree.setVisible(not empty)
        self.tree.updateGeometry()

    def select_note(self, note_id: Optional[int]) -> None:
        self._refreshing = True
        self.tree.clearSelection()
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, KIND_ROLE) == "note" and item.data(0, ID_ROLE) == note_id:
                if item.parent() is not None:
                    item.parent().setExpanded(True)
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                break
            iterator += 1
        self._refreshing = False

    # events -------------------------------------------------------------------

    def _on_current_changed(self, current, _previous):
        if not self._refreshing and current is not None and current.data(0, KIND_ROLE) == "note":
            self.note_selected.emit(current.data(0, ID_ROLE))

    def _on_item_clicked(self, item, _column):
        if item.data(0, KIND_ROLE) == "folder":
            item.setExpanded(not item.isExpanded())

    def _on_expansion(self, item):
        if self._refreshing or self.search.text().strip():
            return  # search auto-expands; don't remember that
        folder_id = item.data(0, ID_ROLE)
        if item.isExpanded():
            self._expanded.add(folder_id)
        else:
            self._expanded.discard(folder_id)
        self.expanded_changed.emit(sorted(self._expanded))

    def _on_action(self, kind: str, item_id: int):
        if kind == "folder":
            self.new_note_requested.emit(item_id)
        else:
            self.delete_note_requested.emit(item_id)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is not None and item.data(0, KIND_ROLE) == "folder":
            folder_id = item.data(0, ID_ROLE)
            menu.addAction("New Note in Folder", lambda: self.new_note_requested.emit(folder_id))
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self.edit_folder_requested.emit(folder_id))
            menu.addAction("Delete…", lambda: self.delete_folder_requested.emit(folder_id))
        elif item is not None:
            note = self._notes.get(item.data(0, ID_ROLE))
            if note is None:
                return
            menu.addAction("Unpin" if note.is_pinned else "Pin to Top", lambda: self.pin_toggled.emit(note.id))
            move = menu.addMenu("Move to Folder")
            standalone = move.addAction("No folder (standalone)",
                                        lambda: self.move_note_requested.emit(note.id, None))
            standalone.setEnabled(note.folder_id is not None)
            if self._folders:
                move.addSeparator()
            for folder in self._folders:
                action = move.addAction(color_dot(folder.color_tag), folder.name,
                                        lambda fid=folder.id: self.move_note_requested.emit(note.id, fid))
                action.setEnabled(folder.id != note.folder_id)
            menu.addSeparator()
            menu.addAction("Delete…", lambda: self.delete_note_requested.emit(note.id))
        else:
            menu.addAction("New Note", lambda: self.new_note_requested.emit(None))
            menu.addAction("New Folder…", self.new_folder_requested.emit)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


# ----- floating card -----------------------------------------------------------

class FloatingPanel(QWidget):
    """The sidebar column: a rounded card floating inside the window.

    The card can be minimized to a small tile around the content's toggle button,
    which stays in the same place in both states. `progress` runs from 0 (tile) to 1
    (open). While it animates, the content keeps its full, final size and the card
    clips it, so the content isn't re-laid out on every frame.

    The shadow is painted here rather than with a QGraphicsDropShadowEffect, which
    would re-render the whole content into an offscreen pixmap on every repaint."""

    RADIUS = 10
    SHADOW = 6  # px of shadow around the card
    GAP = 10    # space between the card and the window's left/bottom edges
    RIGHT = 4   # card to column edge; the splitter handle adds the rest of the gap

    def __init__(self, content: QWidget, top: int = GAP, parent=None):
        super().__init__(parent)
        styled_panel(self, "SidebarFrame")
        self.top = top
        self.card = QFrame(self)
        styled_panel(self.card, "Sidebar")
        self.content = content
        content.setParent(self.card)
        self.dark = False
        self.progress = 1.0
        self._content_width = 260
        self._animating = False
        content.installEventFilter(self)  # re-fit when the content's size hint changes

    # geometry ----------------------------------------------------------------

    def tile_size(self) -> QSize:
        """The minimized card: the toggle button plus equal padding around it."""
        if self.content.width() < 120:  # not laid out yet: give the header room first
            self.content.resize(max(260, self._content_width), self.content.sizeHint().height())
        self.content.layout().activate()
        button = self.content.toggle_button.geometry()
        return QSize(button.right() + 1 + button.left(), button.bottom() + 1 + button.top())

    def column_width(self, card_width: int) -> int:
        return self.GAP + card_width + self.RIGHT

    def minimized_column_width(self) -> int:
        return self.column_width(self.tile_size().width())

    def _open_height(self) -> int:
        available = max(self.tile_size().height(), self.height() - self.top - self.GAP)
        return min(self.content.sizeHint().height(), available)

    def set_progress(self, progress: float) -> None:
        """Place the card between tile (0) and fully open (1)."""
        self.progress = progress
        tile = self.tile_size()
        if not self._animating:
            # At rest the open card fills the column; minimized, the content keeps its
            # last open width so the tile shows exactly the toggle button.
            if progress >= 1:
                self._content_width = max(tile.width(), self.width() - self.GAP - self.RIGHT)
            self.content.setGeometry(0, 0, self._content_width, self._open_height())
        width = round(tile.width() + (self._content_width - tile.width()) * progress)
        height = round(tile.height() + (self._open_height() - tile.height()) * progress)
        self.card.setGeometry(self.GAP, self.top, width, height)
        self.update()

    def begin_animation(self, open_column_width: int) -> None:
        """Freeze the content at its open size for the duration of an animation."""
        self._animating = True
        self._content_width = open_column_width - self.GAP - self.RIGHT
        self.content.setGeometry(0, 0, self._content_width, self._open_height())

    def end_animation(self) -> None:
        self._animating = False
        self.set_progress(self.progress)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._animating:
            self.set_progress(self.progress)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.content and event.type() == QEvent.Type.LayoutRequest and not self._animating:
            QTimer.singleShot(0, lambda: self.set_progress(self.progress))
        return False

    # painting -----------------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self.dark = theme.name == "dark"
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # frame background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.card.geometry())
        strength = 30 if self.dark else 11
        for i in range(self.SHADOW, 0, -1):  # outermost (faintest) first
            alpha = int(strength * (1 - i / (self.SHADOW + 1)) ** 2)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(card.adjusted(-i, -i + 2, i, i + 2), self.RADIUS + i, self.RADIUS + i)
        painter.end()


# ----- storage location -------------------------------------------------------

class PathLabel(QLabel):
    """A file path in small muted text, shortened in the middle when space is
    tight ("~/Library/…/notes.db"). Clicking it reveals the file."""

    clicked = pyqtSignal()

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setObjectName("PathLabel")
        self.path = path
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)
        self.setText(path)  # for sizeHint/accessibility; painting is elided below

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.path, Qt.TextElideMode.ElideMiddle, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ----- theme switch ----------------------------------------------------------

class ThemeSwitch(QAbstractButton):
    """A small pill switch: sun (light) on the left, moon (dark) on the right."""

    TRACK_W, TRACK_H, MARGIN = 40, 22, 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Dark mode (Ctrl+Shift+D)")
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self._theme: Optional[Theme] = None
        self._position = 0.0  # 0 = light, 1 = dark
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_position)
        self.toggled.connect(self._animate)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def setChecked(self, checked: bool) -> None:  # programmatic changes jump without animating
        super().setChecked(checked)
        self._animation.stop()
        self._set_position(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _set_position(self, value) -> None:
        self._position = float(value)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.TRACK_W, self.TRACK_H)

    def paintEvent(self, _event) -> None:
        t = self._theme
        if t is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()

        track = QRectF(0.5, 0.5, self.TRACK_W - 1, self.TRACK_H - 1)
        painter.setPen(QColor(t.border))
        painter.setBrush(QColor(t.hover if hovered else t.panel))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        knob_d = self.TRACK_H - 2 * self.MARGIN
        travel = self.TRACK_W - 2 * self.MARGIN - knob_d
        knob = QRectF(self.MARGIN + travel * self._position, self.MARGIN, knob_d, knob_d)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.accent))
        painter.drawEllipse(knob)

        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(knob, Qt.AlignmentFlag.AlignCenter, "\u263E" if self._position > 0.5 else "\u2600")
        painter.end()


# ----- dialogs ---------------------------------------------------------------

class FolderDialog(QDialog):
    """Create or edit a folder: name plus optional color tag."""

    def __init__(self, parent=None, title: str = "New Folder", name: str = "",
                 color: Optional[str] = None, existing_names: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self._existing = {n.lower() for n in (existing_names or [])}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(name)
        self.name_edit.setMaxLength(MAX_FOLDER_NAME_LENGTH)
        self.name_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"^[A-Za-z0-9 _-]*$")))
        self.name_edit.setPlaceholderText("Letters, numbers, spaces, - and _")
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Color tag"))
        self.color_combo = QComboBox()
        for label, value in FOLDER_COLORS.items():
            self.color_combo.addItem(color_dot(value, 12), label, value)
        index = self.color_combo.findData(color)
        self.color_combo.setCurrentIndex(max(0, index))
        layout.addWidget(self.color_combo)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Muted")
        self.error_label.setStyleSheet("color: #DC2626;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _validate(self):
        name = self.folder_name()
        if not is_valid_folder_name(name):
            self.error_label.setText("Use letters, numbers, spaces, - and _ only.")
        elif name.lower() in self._existing:
            self.error_label.setText("A folder with that name already exists.")
        else:
            self.accept()
            return
        self.error_label.show()

    def folder_name(self) -> str:
        return " ".join(self.name_edit.text().split())

    def color_tag(self) -> Optional[str]:
        return self.color_combo.currentData()

