"""Sidebar (folders), note list, sheet dialogs, menus and small shared widgets."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QMimeData, QObject, QPointF, QRect, QRectF, QRegularExpression, QSize, Qt,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QAbstractButton, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QSizePolicy, QSplitter, QSplitterHandle, QStyle,
    QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from .icons import paint_icon, set_button_icon
from .models import FOLDER_COLORS, MAX_FOLDER_NAME_LENGTH, Folder, Note, is_valid_folder_name, preview_snippet
from .styles import Theme, current_theme

NOTE_MIME = "application/x-simplenotes-note-ids"
FOLDER_MIME = "application/x-simplenotes-folder-id"

# On macOS the traffic lights are drawn over our content (see MainWindow), moved to
# be centered this far below the window's top edge with the close button this far
# from its left (mac_chrome.place_traffic_lights). TRAFFIC_LIGHTS_WIDTH is the
# space headers keep clear for them.
TITLEBAR_CENTER = 26
TRAFFIC_LIGHTS_LEFT = 20
TRAFFIC_LIGHTS_WIDTH = 96

Scope = Tuple[str, Optional[int]]


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
    """Short list date, like Notes: a time today, "Yesterday", a weekday this week, else d/m/y."""
    try:
        stamp = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    today = datetime.now().date()
    if stamp.date() == today:
        return stamp.strftime("%H:%M")
    if stamp.date() == today - timedelta(days=1):
        return "Yesterday"
    if today - stamp.date() < timedelta(days=7):
        return stamp.strftime("%A")
    return stamp.strftime("%d/%m/%y")


def format_long_date(iso: str) -> str:
    """The editor's date line: "26 October 2026 at 22:05"."""
    try:
        stamp = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{stamp.day} {stamp.strftime('%B %Y')} at {stamp.strftime('%H:%M')}"


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


def soft_shadow(widget: QWidget, dark: bool) -> None:
    """The faint shadow under toolbar pills. Only used on small widgets: the effect
    re-renders the widget offscreen on every repaint."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(14)
    effect.setOffset(0, 1)
    effect.setColor(QColor(0, 0, 0, 90 if dark else 28))
    widget.setGraphicsEffect(effect)


def _section_font(base: QFont, px: int, weight=QFont.Weight.Normal) -> QFont:
    font = QFont(base)
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


# ----- window chrome ------------------------------------------------------------

class WindowDragFilter(QObject):
    """Lets empty parts of the header bars move the window (the system title bar is
    hidden under them on macOS); a double click zooms it, as a title bar would."""

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        child = obj.childAt(event.position().toPoint())
        if child is not None and not isinstance(child, QLabel):
            return False
        window = obj.window()
        if kind == QEvent.Type.MouseButtonDblClick:
            window.showNormal() if window.isMaximized() else window.showMaximized()
        elif window.windowHandle() is not None:
            window.windowHandle().startSystemMove()
        return True


class MenuStyler(QObject):
    """App-wide event filter that gives every QMenu (ours and Qt's own context menus)
    a frameless, translucent window so the stylesheet's rounded corners show."""

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QMenu) and event.type() == QEvent.Type.Polish and not obj.property("_rounded"):
            obj.setProperty("_rounded", True)
            obj.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return False


class _HairlineHandle(QSplitterHandle):
    def paintEvent(self, _event) -> None:
        t = self.splitter().theme
        if t is None:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(t.bg))
        painter.fillRect(QRect(self.width() // 2, 0, 1, self.height()), QColor(t.border))
        painter.end()


class HairlineSplitter(QSplitter):
    """A splitter whose handle is a 1px line, inside a handle wide enough to grab."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.theme: Optional[Theme] = None
        self.setHandleWidth(5)

    def createHandle(self) -> QSplitterHandle:
        return _HairlineHandle(self.orientation(), self)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        for i in range(self.count()):
            self.handle(i).update()


# ----- sidebar: scopes and folders -------------------------------------------------

KIND_ROLE = Qt.ItemDataRole.UserRole          # "all" | "standalone" | "section" | "folder"
ID_ROLE = Qt.ItemDataRole.UserRole + 1
COUNT_ROLE = Qt.ItemDataRole.UserRole + 2
COLOR_ROLE = Qt.ItemDataRole.UserRole + 3

SIDEBAR_ROW = 32
SECTION_ROW = 34


class _SidebarDelegate(QStyledItemDelegate):
    """Folder rows like Notes: icon, name, count on the right; the selection is an accent pill."""

    def __init__(self, view: "_SidebarList"):
        super().__init__(view)
        self.view = view
        self.theme: Optional[Theme] = None

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, SECTION_ROW if index.data(KIND_ROLE) == "section" else SIDEBAR_ROW)

    def paint(self, painter: QPainter, option, index) -> None:
        t = self.theme
        if t is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        kind = index.data(KIND_ROLE)
        rect = QRectF(option.rect).adjusted(6, 1, -6, -1)
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if kind == "section":
            painter.setFont(_section_font(option.font, 12, QFont.Weight.DemiBold))
            painter.setPen(QColor(t.muted))
            painter.drawText(rect.adjusted(8, 0, 0, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                             name)
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        drop_target = self.view.drop_target == (kind, index.data(ID_ROLE))
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(rect, 8, 8)
        elif drop_target:
            painter.setPen(QPen(QColor(t.accent), 2))
            painter.setBrush(QColor(t.hover))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)
        fg = QColor(t.on_accent if selected else t.text)
        icon_color = t.on_accent if selected else (index.data(COLOR_ROLE) or t.text)
        cy = rect.center().y()
        paint_icon(painter, "folder", QRectF(rect.left() + 8, cy - 9, 18, 18), icon_color, stroke=1.25)

        count = str(index.data(COUNT_ROLE) if index.data(COUNT_ROLE) is not None else "")
        font = _section_font(option.font, 14)
        painter.setFont(font)
        painter.setPen(QColor(t.on_accent) if selected else QColor(t.muted))
        count_width = painter.fontMetrics().horizontalAdvance(count) + 4
        painter.drawText(QRectF(rect.right() - count_width - 10, rect.top(), count_width, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count)
        name_rect = QRectF(rect.left() + 34, rect.top(), rect.width() - 34 - count_width - 16, rect.height())
        painter.setPen(fg)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(name_rect.width())))
        painter.restore()


class _SidebarList(QListWidget):
    notes_moved = pyqtSignal(list, object)       # note ids, folder id (None = standalone)
    folder_dropped = pyqtSignal(int, str, int)   # folder id, "before" | "after", anchor folder id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        # Copy, not Move: a Move would make Qt delete the dragged rows itself.
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(False)
        self.drop_target: Optional[Tuple[str, Optional[int]]] = None
        self.drop_line: Optional[QRectF] = None
        self.delegate = _SidebarDelegate(self)
        self.setItemDelegate(self.delegate)

    def mimeTypes(self):
        return [NOTE_MIME, FOLDER_MIME]

    def mimeData(self, items):
        data = QMimeData()
        folders = [i for i in items if i.data(KIND_ROLE) == "folder"]
        if folders:
            data.setData(FOLDER_MIME, str(folders[0].data(ID_ROLE)).encode())
        return data

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def _folder_items(self) -> List[QListWidgetItem]:
        return [self.item(i) for i in range(self.count()) if self.item(i).data(KIND_ROLE) == "folder"]

    def _folder_drop_spot(self, pos):
        """For a dragged folder: (anchor, anchor folder id, line). Upper half of a row
        drops before it, lower half after; below the last folder drops after it."""
        folders = self._folder_items()
        if not folders:
            return None
        for item in folders:
            row = QRectF(self.visualItemRect(item))
            if pos.y() <= row.bottom():
                before = pos.y() < row.center().y()
                y = row.top() if before else row.bottom() + 1
                return ("before" if before else "after"), item.data(ID_ROLE), QRectF(row.left() + 12, y - 1,
                                                                                    row.width() - 24, 2)
        row = QRectF(self.visualItemRect(folders[-1]))
        return "after", folders[-1].data(ID_ROLE), QRectF(row.left() + 12, row.bottom(), row.width() - 24, 2)

    def _note_drop_target(self, pos):
        item = self.itemAt(pos)
        if item is None or item.data(KIND_ROLE) not in ("folder", "standalone"):
            return None
        return item.data(KIND_ROLE), item.data(ID_ROLE)

    def _set_feedback(self, target, line) -> None:
        if target != self.drop_target or line != self.drop_line:
            self.drop_target, self.drop_line = target, line
            self.viewport().update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(NOTE_MIME) or event.mimeData().hasFormat(FOLDER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()
        if event.mimeData().hasFormat(FOLDER_MIME):
            spot = self._folder_drop_spot(pos)
            self._set_feedback(None, spot[2] if spot else None)
        elif event.mimeData().hasFormat(NOTE_MIME):
            target = self._note_drop_target(pos)
            self._set_feedback(target, None)
            if target is None:
                event.ignore()
                return
        else:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._set_feedback(None, None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        pos = event.position().toPoint()
        self._set_feedback(None, None)
        data = event.mimeData()
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if data.hasFormat(FOLDER_MIME):
            raw = bytes(data.data(FOLDER_MIME)).decode()
            spot = self._folder_drop_spot(pos)
            if raw.isdigit() and spot is not None and spot[1] != int(raw):
                self.folder_dropped.emit(int(raw), spot[0], spot[1])
        elif data.hasFormat(NOTE_MIME):
            target = self._note_drop_target(pos)
            ids = [int(x) for x in bytes(data.data(NOTE_MIME)).decode().split(",") if x.strip().isdigit()]
            if target is not None and ids:
                self.notes_moved.emit(ids, target[1] if target[0] == "folder" else None)

    def paintEvent(self, event):
        super().paintEvent(event)
        theme = self.delegate.theme
        if theme is None or self.drop_line is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.accent))
        painter.drawRoundedRect(self.drop_line, 1, 1)
        painter.drawEllipse(QRectF(self.drop_line.left() - 3, self.drop_line.center().y() - 3, 6, 6))
        painter.end()


class Sidebar(QWidget):
    """The sidebar card's content: window controls row, "All Notes", "Notes"
    (standalone notes) and the folders, then the database location at the bottom."""

    scope_selected = pyqtSignal(object)           # Scope
    new_note_requested = pyqtSignal(object)       # folder id or None
    new_folder_requested = pyqtSignal()
    edit_folder_requested = pyqtSignal(int)
    delete_folder_requested = pyqtSignal(int)
    notes_moved = pyqtSignal(list, object)
    folder_dropped = pyqtSignal(int, str, int)
    collapse_requested = pyqtSignal()

    def __init__(self, path_label: QWidget, parent=None):
        super().__init__(parent)
        styled_panel(self, "Sidebar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 14)
        layout.setSpacing(8)

        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 8, 0)
        header.setSpacing(6)
        self.traffic_space = QWidget()  # the window's traffic lights sit here on macOS
        self.traffic_space.setFixedWidth(0)
        header.addWidget(self.traffic_space)
        header.addStretch(1)
        self.new_folder_button = tool_button("", "New folder (Ctrl+Shift+N)")
        self.new_folder_button.clicked.connect(self.new_folder_requested)
        header.addWidget(self.new_folder_button)
        self.toggle_button = tool_button("", "Hide sidebar (Ctrl+1)")
        self.toggle_button.clicked.connect(self.collapse_requested)
        header.addWidget(self.toggle_button)
        layout.addWidget(self.header)

        self.list = _SidebarList()
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.notes_moved.connect(self.notes_moved)
        self.list.folder_dropped.connect(self.folder_dropped)
        layout.addWidget(self.list, 1)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(14, 0, 10, 0)
        path_row.addWidget(path_label)
        layout.addLayout(path_row)

        self._refreshing = False
        self._folders: List[Folder] = []

    def set_header_height(self, height: int, traffic_width: int) -> None:
        self.header.setFixedHeight(height)
        self.traffic_space.setFixedWidth(traffic_width)

    def set_theme(self, theme: Theme) -> None:
        set_button_icon(self.new_folder_button, "folder-plus", theme.muted, theme.text, 18)
        set_button_icon(self.toggle_button, "sidebar", theme.muted, theme.text, 18)
        self.list.delegate.theme = theme
        self.list.viewport().update()

    def refresh(self, folders: List[Folder], per_folder: Dict[int, int], standalone: int, total: int,
                scope: Scope) -> None:
        self._refreshing = True
        self._folders = folders
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()

        def add(kind: str, name: str, item_id=None, count=None, color=None) -> QListWidgetItem:
            item = QListWidgetItem(name)
            item.setData(KIND_ROLE, kind)
            item.setData(ID_ROLE, item_id)
            item.setData(COUNT_ROLE, count)
            item.setData(COLOR_ROLE, color)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if kind == "section":
                flags = Qt.ItemFlag.NoItemFlags
            elif kind == "folder":
                flags |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
            elif kind == "standalone":
                flags |= Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            self.list.addItem(item)
            if (kind, item_id) == tuple(scope):
                self.list.setCurrentItem(item)
            return item

        add("all", "All Notes", None, total)
        add("standalone", "Notes", None, standalone)
        if folders:
            add("section", "Folders")
        for folder in folders:
            add("folder", folder.name, folder.id, per_folder.get(folder.id, 0), folder.color_tag)
        self.list.verticalScrollBar().setValue(scroll)
        self._refreshing = False

    def _on_current_changed(self, current, _previous) -> None:
        if not self._refreshing and current is not None and current.data(KIND_ROLE) != "section":
            self.scope_selected.emit((current.data(KIND_ROLE), current.data(ID_ROLE)))

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        if item is not None and item.data(KIND_ROLE) == "folder":
            folder_id = item.data(ID_ROLE)
            menu.addAction("New Note", lambda: self.new_note_requested.emit(folder_id))
            menu.addSeparator()
            menu.addAction("Rename Folder…", lambda: self.edit_folder_requested.emit(folder_id))
            menu.addAction("Delete Folder…", lambda: self.delete_folder_requested.emit(folder_id))
        else:
            if item is not None and item.data(KIND_ROLE) == "standalone":
                menu.addAction("New Note", lambda: self.new_note_requested.emit(None))
            menu.addAction("New Folder…", self.new_folder_requested.emit)
        menu.exec(self.list.viewport().mapToGlobal(pos))


class SidebarCard(QWidget):
    """The sidebar column: the Sidebar content on a rounded card with a soft shadow.

    While the column animates (see MainWindow.toggle_sidebar) the card keeps its open
    width and slides out to the left instead of being squeezed, so its content isn't
    re-laid out on every frame. The shadow is painted here rather than with a
    QGraphicsDropShadowEffect, which would re-render the whole list on every repaint."""

    GAP = 6
    RADIUS = 12
    SHADOW = 7
    MIN_WIDTH, MAX_WIDTH = 190, 420

    resized = pyqtSignal(int)  # the user dragged the column to this width

    def __init__(self, content: QWidget, parent=None):
        super().__init__(parent)
        styled_panel(self, "SidebarColumn")
        self.content = content
        content.setParent(self)
        self.frozen_width: Optional[int] = None  # the open column width while animating
        self.theme: Optional[Theme] = None
        self._resize_from: Optional[Tuple[float, int]] = None  # (press x, width) while resizing
        self.setMouseTracking(True)

    def card_rect(self) -> QRect:
        full = self.frozen_width or self.width()
        x = self.GAP - (full - self.width())  # slides left as the column narrows
        return QRect(x, self.GAP, max(0, full - 2 * self.GAP), max(0, self.height() - 2 * self.GAP))

    def freeze(self, width: Optional[int]) -> None:
        self.frozen_width = width
        self.content.setGeometry(self.card_rect())
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.content.setGeometry(self.card_rect())

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    # The strip right of the card resizes the column.
    def _on_grip(self, x: float) -> bool:
        return x >= self.width() - self.GAP - 1 and self.frozen_width is None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_grip(event.position().x()):
            self._resize_from = (event.globalPosition().x(), self.width())
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_from is not None:
            start_x, start_width = self._resize_from
            width = round(start_width + event.globalPosition().x() - start_x)
            self.resized.emit(max(self.MIN_WIDTH, min(self.MAX_WIDTH, width)))
            return
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._on_grip(event.position().x())
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._resize_from = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        t = self.theme
        if t is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.card_rect())
        dark = t.name == "dark"
        strength = 34 if dark else 13
        for i in range(self.SHADOW, 0, -1):  # outermost (faintest) first
            alpha = int(strength * (1 - i / (self.SHADOW + 1)) ** 2)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(card.adjusted(-i, -i + 2, i, i + 2), self.RADIUS + i, self.RADIUS + i)
        painter.setBrush(QColor(t.panel))
        painter.setPen(QPen(QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 14), 1))
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        painter.end()


# ----- note list ------------------------------------------------------------------

NOTE_ID_ROLE = Qt.ItemDataRole.UserRole
NOTE_DATE_ROLE = Qt.ItemDataRole.UserRole + 1
NOTE_PREVIEW_ROLE = Qt.ItemDataRole.UserRole + 2
NOTE_PINNED_ROLE = Qt.ItemDataRole.UserRole + 3

NOTE_ROW = 66


class _NoteDelegate(QStyledItemDelegate):
    """Title in bold over "date  preview", hairline separators, a gray rounded selection."""

    def __init__(self, view: "NoteList"):
        super().__init__(view)
        self.view = view
        self.theme: Optional[Theme] = None

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, NOTE_ROW)

    def paint(self, painter: QPainter, option, index) -> None:
        t = self.theme
        if t is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(8, 1, -8, -1)
        row = index.row()
        current = self.view.currentRow()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.selected))
            painter.drawRoundedRect(rect, 9, 9)
        elif row + 1 != current and row + 1 < self.view.count():
            painter.setPen(QPen(QColor(t.border), 1))
            y = option.rect.bottom() + 0.5
            painter.drawLine(QPointF(rect.left() + 26, y), QPointF(rect.right() - 8, y))

        x = rect.left() + 26
        right = rect.right() - 12
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        title_font = _section_font(option.font, 14, QFont.Weight.Bold)
        painter.setFont(title_font)
        title_rect = QRectF(x, rect.top() + 10, right - x, 20)
        if index.data(NOTE_PINNED_ROLE):
            paint_icon(painter, "pin", QRectF(rect.left() + 7, title_rect.center().y() - 7, 14, 14), t.accent)
        painter.setPen(QColor(t.text))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(title_rect.width())))

        detail_rect = QRectF(x, title_rect.bottom() + 1, right - x, 19)
        date = index.data(NOTE_DATE_ROLE) or ""
        painter.setFont(_section_font(option.font, 13))
        painter.setPen(QColor(t.text))
        painter.drawText(detail_rect, Qt.AlignmentFlag.AlignVCenter, date)
        offset = painter.fontMetrics().horizontalAdvance(date) + (10 if date else 0)
        painter.setFont(_section_font(option.font, 13))
        painter.setPen(QColor(t.muted))
        preview_rect = detail_rect.adjusted(offset, 0, 0, 0)
        preview = index.data(NOTE_PREVIEW_ROLE) or "No additional text"
        painter.drawText(preview_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(preview, Qt.TextElideMode.ElideRight,
                                                          int(max(0.0, preview_rect.width()))))
        painter.restore()


class NoteList(QListWidget):
    """The middle column. Notes drag out to sidebar folders, and within a folder (or
    the standalone notes) they can be dragged into a new order."""

    notes_dropped = pyqtSignal(list, str, object)  # note ids, "before" | "after" | "end", anchor note id
    delete_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)  # Move would delete the source rows
        self.setDropIndicatorShown(False)
        self.reorderable = False
        self.drop_line: Optional[QRectF] = None
        self.delegate = _NoteDelegate(self)
        self.setItemDelegate(self.delegate)
        self.currentRowChanged.connect(lambda _row: self.viewport().update())  # separators next to it

    def mimeTypes(self):
        return [NOTE_MIME]

    def mimeData(self, items):
        data = QMimeData()
        data.setData(NOTE_MIME, ",".join(str(i.data(NOTE_ID_ROLE)) for i in items).encode())
        return data

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.currentItem() is not None:
            self.delete_requested.emit(self.currentItem().data(NOTE_ID_ROLE))
            return
        super().keyPressEvent(event)

    def _drop_spot(self, pos):
        """(anchor, anchor note id, line) for a reorder drop at pos."""
        item = self.itemAt(pos)
        if item is None:
            if not self.count():
                return "end", None, None
            row = QRectF(self.visualItemRect(self.item(self.count() - 1)))
            return "end", None, QRectF(row.left() + 16, row.bottom(), row.width() - 32, 2)
        row = QRectF(self.visualItemRect(item))
        before = pos.y() < row.center().y()
        y = row.top() if before else row.bottom() + 1
        return ("before" if before else "after"), item.data(NOTE_ID_ROLE), QRectF(row.left() + 16, y - 1,
                                                                                  row.width() - 32, 2)

    def _set_line(self, line) -> None:
        if line != self.drop_line:
            self.drop_line = line
            self.viewport().update()

    def dragEnterEvent(self, event):
        if self.reorderable and event.source() is self and event.mimeData().hasFormat(NOTE_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not (self.reorderable and event.source() is self):
            event.ignore()
            return
        self._set_line(self._drop_spot(event.position().toPoint())[2])
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._set_line(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_line(None)
        if not (self.reorderable and event.source() is self):
            event.ignore()
            return
        anchor, anchor_id, _ = self._drop_spot(event.position().toPoint())
        ids = [int(x) for x in bytes(event.mimeData().data(NOTE_MIME)).decode().split(",") if x.strip().isdigit()]
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if ids and anchor_id not in ids:  # dropping a note onto itself does nothing
            self.notes_dropped.emit(ids, anchor, anchor_id)

    def paintEvent(self, event):
        super().paintEvent(event)
        theme = self.delegate.theme
        if theme is None or self.drop_line is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.accent))
        painter.drawRoundedRect(self.drop_line, 1, 1)
        painter.drawEllipse(QRectF(self.drop_line.left() - 3, self.drop_line.center().y() - 3, 6, 6))
        painter.end()


class NotesPanel(QWidget):
    """Middle column: "<folder>  N notes" header with a ⋯ menu, then the note list."""

    note_selected = pyqtSignal(int)
    new_note_requested = pyqtSignal()
    new_folder_requested = pyqtSignal()
    edit_folder_requested = pyqtSignal(int)
    delete_folder_requested = pyqtSignal(int)
    delete_note_requested = pyqtSignal(int)
    move_note_requested = pyqtSignal(int, object)
    pin_toggled = pyqtSignal(int)
    notes_dropped = pyqtSignal(list, str, object)
    expand_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        styled_panel(self, "NotesPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(18, TITLEBAR_CENTER - 17, 16, 0)  # 34px controls on the traffic-light line
        header.setSpacing(6)
        header.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Shown while the sidebar is hidden: room for the traffic lights plus a
        # button to bring the sidebar back. Its width animates with the sidebar.
        self.lead = QWidget()
        lead = QHBoxLayout(self.lead)
        lead.setContentsMargins(0, 0, 8, 0)
        lead.addStretch(1)
        self.expand_button = tool_button("", "Show sidebar (Ctrl+1)")
        self.expand_button.clicked.connect(self.expand_requested)
        self.expand_button.setFixedHeight(34)
        lead.addWidget(self.expand_button)
        self.lead.setFixedWidth(0)
        self.lead.setFixedHeight(34)
        header.addWidget(self.lead)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.setContentsMargins(0, 1, 0, 0)
        self.title = QLabel("All Notes")
        self.title.setObjectName("ListTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("ListSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.more_button = tool_button("", "More")
        self.more_button.setFixedSize(34, 34)
        self.more_button.clicked.connect(self._more_menu)
        self.more_pill = QFrame()
        self.more_pill.setObjectName("Pill")
        pill = QHBoxLayout(self.more_pill)
        pill.setContentsMargins(0, 0, 0, 0)
        pill.addWidget(self.more_button)
        self.more_pill.setFixedSize(34, 34)
        header.addWidget(self.more_pill)
        layout.addWidget(self.header)

        self.list = NoteList()
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.notes_dropped.connect(self.notes_dropped)
        self.list.delete_requested.connect(self.delete_note_requested)
        layout.addWidget(self.list, 1)

        self.empty_label = QLabel("No Notes")
        self.empty_label.setObjectName("EmptyHint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label, 1)

        self._notes: Dict[int, Note] = {}
        self._folders: List[Folder] = []
        self._scope: Scope = ("all", None)
        self._refreshing = False
        self._theme: Optional[Theme] = None
        self._lead_width = 0

    def set_header_height(self, height: int, lead_width: int) -> None:
        self.header.setFixedHeight(height)
        self._lead_width = lead_width

    def set_lead_progress(self, hidden: float) -> None:
        """0 with the sidebar open, 1 with it hidden: how much of the lead area shows."""
        self.lead.setFixedWidth(round(self._lead_width * hidden))
        self.lead.setVisible(hidden > 0)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        set_button_icon(self.expand_button, "sidebar", theme.muted, theme.text, 18)
        set_button_icon(self.more_button, "more", theme.text, theme.text, 18)
        soft_shadow(self.more_pill, theme.name == "dark")
        self.list.delegate.theme = theme
        self.list.viewport().update()

    def refresh(self, notes: List[Note], folders: List[Folder], scope: Scope, title: str, subtitle: str,
                current_id: Optional[int], reorderable: bool) -> None:
        self._refreshing = True
        self._notes = {n.id: n for n in notes}
        self._folders = folders
        self._scope = scope
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        self.list.reorderable = reorderable
        for note in notes:
            item = QListWidgetItem(note.title)
            item.setData(NOTE_ID_ROLE, note.id)
            item.setData(NOTE_DATE_ROLE, format_date(note.modified_date))
            item.setData(NOTE_PREVIEW_ROLE, preview_snippet(note.markdown_content, 160))
            item.setData(NOTE_PINNED_ROLE, note.is_pinned)
            item.setToolTip(note.title)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
            self.list.addItem(item)
            if note.id == current_id:
                self.list.setCurrentItem(item)
        self.list.verticalScrollBar().setValue(scroll)
        self.empty_label.setVisible(not notes)
        self.list.setVisible(bool(notes))
        self._refreshing = False

    def note_ids(self) -> List[int]:
        return [self.list.item(i).data(NOTE_ID_ROLE) for i in range(self.list.count())]

    def select_note(self, note_id: Optional[int]) -> None:
        self._refreshing = True
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(NOTE_ID_ROLE) == note_id:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                break
        self._refreshing = False

    def _on_current_changed(self, current, _previous) -> None:
        if not self._refreshing and current is not None:
            self.note_selected.emit(current.data(NOTE_ID_ROLE))

    def _more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("New Note", self.new_note_requested.emit)
        menu.addAction("New Folder…", self.new_folder_requested.emit)
        if self._scope[0] == "folder":
            folder_id = self._scope[1]
            menu.addSeparator()
            menu.addAction("Rename Folder…", lambda: self.edit_folder_requested.emit(folder_id))
            menu.addAction("Delete Folder…", lambda: self.delete_folder_requested.emit(folder_id))
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def note_menu(self, note: Note, menu: QMenu) -> None:
        """Pin / Move to Folder / Delete actions for note, added to menu (shared with the editor's ⋯)."""
        menu.addAction("Unpin Note" if note.is_pinned else "Pin Note", lambda: self.pin_toggled.emit(note.id))
        move = menu.addMenu("Move to")
        notes_action = move.addAction(color_dot(None), "Notes", lambda: self.move_note_requested.emit(note.id, None))
        notes_action.setEnabled(note.folder_id is not None)
        if self._folders:
            move.addSeparator()
        for folder in self._folders:
            action = move.addAction(color_dot(folder.color_tag), folder.name,
                                    lambda fid=folder.id: self.move_note_requested.emit(note.id, fid))
            action.setEnabled(folder.id != note.folder_id)
        menu.addSeparator()
        menu.addAction("Delete Note…", lambda: self.delete_note_requested.emit(note.id))

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        note = self._notes.get(item.data(NOTE_ID_ROLE)) if item is not None else None
        if note is not None:
            self.note_menu(note, menu)
        else:
            menu.addAction("New Note", self.new_note_requested.emit)
        menu.exec(self.list.viewport().mapToGlobal(pos))


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

    TRACK_W, TRACK_H, MARGIN = 44, 26, 3

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
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(knob, Qt.AlignmentFlag.AlignCenter, "☾" if self._position > 0.5 else "☀")
        painter.end()


# ----- sheet dialogs -----------------------------------------------------------

class SheetDialog(QDialog):
    """A frameless, rounded dialog card in the style of a macOS sheet: bold title,
    content, a hairline, then the buttons on the right. Drag anywhere to move it."""

    RADIUS = 16

    def __init__(self, parent=None, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.theme = current_theme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("SheetTitle")
        outer.addWidget(heading)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)
        rule = QFrame()
        rule.setObjectName("SheetRule")
        rule.setFixedHeight(1)
        outer.addSpacing(4)
        outer.addWidget(rule)
        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(12)
        self.button_row.addStretch(1)
        outer.addLayout(self.button_row)

    def add_button(self, text: str, role: str = "", default: bool = False) -> QPushButton:
        """role: "primary" (accent), "destructive" (red text) or "" (plain)."""
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(110)
        if role == "primary":
            button.setObjectName("Primary")
        elif role == "destructive":
            button.setObjectName("Destructive")
        button.setAutoDefault(False)
        button.setDefault(default)
        self.button_row.addWidget(button)
        return button

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:  # centered over the window, a little high, like a sheet
            frame = parent.window().frameGeometry()
            self.move(frame.center().x() - self.width() // 2,
                      frame.top() + max(40, (frame.height() - self.height()) // 3))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle() is not None:
            self.windowHandle().startSystemMove()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        t = self.theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = t.name == "dark"
        painter.setPen(QPen(QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 40), 1))
        painter.setBrush(QColor(t.panel if dark else t.bg))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        painter.end()


class MessageDialog(SheetDialog):
    """Confirmation / message sheet. ask() returns the key of the clicked button, or None."""

    def __init__(self, parent, title: str, text: str, info: str = ""):
        super().__init__(parent, title)
        self.setMinimumWidth(420)
        self.result_key: Optional[str] = None
        message = QLabel(text)
        message.setObjectName("SheetText")
        message.setWordWrap(True)
        self.body.addWidget(message)
        if info:
            detail = QLabel(info)
            detail.setObjectName("SheetInfo")
            detail.setWordWrap(True)
            self.body.addWidget(detail)

    @classmethod
    def ask(cls, parent, title: str, text: str, info: str,
            buttons: Sequence[Tuple[str, str, str]], default: Optional[str] = None) -> Optional[str]:
        """buttons: (key, label, role) left to right; a "cancel" key also closes on Esc."""
        dialog = cls(parent, title, text, info)
        for key, label, role in buttons:
            button = dialog.add_button(label, role, default=key == default)
            button.clicked.connect(lambda _=False, k=key: dialog._choose(k))
        dialog.exec()
        return dialog.result_key

    def _choose(self, key: str) -> None:
        self.result_key = key
        self.accept()


class _ColorSwatch(QAbstractButton):
    """One color-tag choice: a filled circle (or a slashed ring for "None")."""

    SIZE = 22

    def __init__(self, label: str, color: Optional[str], parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.SIZE + 6, self.SIZE + 6)

    def paintEvent(self, _event) -> None:
        t = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot = QRectF(3, 3, self.SIZE, self.SIZE)
        if self.isChecked():
            painter.setPen(QPen(QColor(t.focus_ring), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
        if self.color:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.color))
            painter.drawEllipse(dot.adjusted(2, 2, -2, -2))
        else:
            painter.setPen(QPen(QColor(t.muted), 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inner = dot.adjusted(2.5, 2.5, -2.5, -2.5)
            painter.drawEllipse(inner)
            painter.drawLine(QPointF(inner.left() + 3, inner.bottom() - 3), QPointF(inner.right() - 3, inner.top() + 3))
        painter.end()


class FolderDialog(SheetDialog):
    """Create or edit a folder: name plus optional color tag."""

    def __init__(self, parent=None, title: str = "New Folder", name: str = "",
                 color: Optional[str] = None, existing_names: Optional[List[str]] = None):
        super().__init__(parent, title)
        self.setMinimumWidth(460)
        self._existing = {n.lower() for n in (existing_names or [])}

        grid = QVBoxLayout()
        grid.setSpacing(14)
        name_row = QHBoxLayout()
        name_row.setSpacing(12)
        name_label = QLabel("Name:")
        name_label.setFixedWidth(52)
        name_row.addWidget(name_label)
        self.name_edit = QLineEdit(name)
        self.name_edit.setMaxLength(MAX_FOLDER_NAME_LENGTH)
        self.name_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"^[A-Za-z0-9 _-]*$")))
        self.name_edit.setPlaceholderText("Letters, numbers, spaces, - and _")
        self.name_edit.setMinimumHeight(34)
        name_row.addWidget(self.name_edit, 1)
        grid.addLayout(name_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        color_label = QLabel("Colour:")
        color_label.setFixedWidth(52)
        color_row.addWidget(color_label)
        color_row.addSpacing(6)
        self.swatches: List[_ColorSwatch] = []
        for label, value in FOLDER_COLORS.items():
            swatch = _ColorSwatch(label, value)
            swatch.setChecked(value == color)
            self.swatches.append(swatch)
            color_row.addWidget(swatch)
        if not any(s.isChecked() for s in self.swatches):
            self.swatches[0].setChecked(True)
        color_row.addStretch(1)
        grid.addLayout(color_row)
        self.body.addLayout(grid)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.hide()
        self.body.addWidget(self.error_label)

        cancel = self.add_button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = self.add_button("OK", "primary", default=True)
        ok.clicked.connect(self._validate)
        self.name_edit.returnPressed.connect(self._validate)
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
        return next((s.color for s in self.swatches if s.isChecked()), None)
