"""The "/" command menu: commands, type-to-filter matching, and the popup list.

The popup is a child widget of the editor, not a separate window, so keyboard
focus never leaves the editor: you keep typing the query into the note, and
the editor forwards only Up/Down/Enter/Tab/Esc to the popup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout

from .styles import Theme


@dataclass(frozen=True)
class SlashCommand:
    label: str
    insert: str
    hint: str                     # the markdown it produces, shown on the right
    keywords: Tuple[str, ...] = ()


SLASH_COMMANDS: List[SlashCommand] = [
    SlashCommand("Text", "", "", ("plain", "paragraph")),
    SlashCommand("Heading 1", "# ", "#", ("h1", "title")),
    SlashCommand("Heading 2", "## ", "##", ("h2", "subtitle")),
    SlashCommand("Heading 3", "### ", "###", ("h3",)),
    SlashCommand("Bulleted list", "- ", "-", ("ul", "bullet", "unordered")),
    SlashCommand("Numbered list", "1. ", "1.", ("ol", "ordered", "number")),
    SlashCommand("To-do list", "- [ ] ", "[ ]", ("todo", "task", "checkbox", "check")),
    SlashCommand("Quote", "> ", ">", ("blockquote", "citation")),
    SlashCommand("Code block", "```\n\n```", "```", ("snippet", "pre", "monospace")),
    SlashCommand("Divider", "---\n", "---", ("hr", "line", "rule", "separator")),
    SlashCommand("Table", "| Column 1 | Column 2 |\n| --- | --- |\n|  |  |", "|", ("grid",)),
]


def _compact(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def filter_commands(query: str) -> List[SlashCommand]:
    """Commands matching query, best first.

    Ranking: label starts with the query, then a word of the label (or a keyword)
    starts with it, then the query appears anywhere. Matching ignores case, spaces
    and punctuation, so "/todo", "/to-do" and "/h2" all work.
    """
    q = _compact(query)
    if not q:
        return list(SLASH_COMMANDS)
    ranked = []
    for index, command in enumerate(SLASH_COMMANDS):
        label = _compact(command.label)
        words = [_compact(w) for w in command.label.replace("-", " ").split()] + list(command.keywords)
        query_words = [_compact(w) for w in query.replace("-", " ").split() if _compact(w)]
        if label.startswith(q):
            rank = 0
        elif any(w.startswith(q) for w in words) or (
                len(query_words) > 1 and all(any(w.startswith(qw) for w in words) for qw in query_words)):
            rank = 1  # e.g. "head 2" -> Heading 2
        elif q in label or any(q in w for w in words):
            rank = 2
        else:
            continue
        ranked.append((rank, index, command))
    return [command for _, _, command in sorted(ranked, key=lambda r: (r[0], r[1]))]


class _SlashDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 30

    def __init__(self, popup: "SlashPopup"):
        super().__init__(popup)
        self.popup = popup

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        t = self.popup.theme
        command: Optional[SlashCommand] = index.data(Qt.ItemDataRole.UserRole)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(4, 1, -4, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and command is not None
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(rect, 6, 6)
        font = QFont(option.font)
        font.setPixelSize(13)
        painter.setFont(font)
        if command is None:  # the "No results" row
            painter.setPen(QColor(t.muted))
            painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "No results")
        else:
            painter.setPen(QColor(t.on_accent if selected else t.text))
            painter.drawText(rect.adjusted(10, 0, -60, 0), Qt.AlignmentFlag.AlignVCenter, command.label)
            if command.hint:
                mono = QFont(self.popup.mono_family)
                mono.setPixelSize(12)
                painter.setFont(mono)
                painter.setPen(QColor(t.on_accent if selected else t.muted))
                painter.drawText(rect.adjusted(0, 0, -10, 0),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, command.hint)
        painter.restore()


class SlashPopup(QFrame):
    """Filterable command list shown under the cursor. Emits chosen(command)."""

    chosen = pyqtSignal(object)
    MAX_ROWS = 8

    def __init__(self, parent, theme: Theme, mono_family: str):
        super().__init__(parent)
        self.setObjectName("SlashMenu")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.theme = theme
        self.mono_family = mono_family
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget(self)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(_SlashDelegate(self))
        self.list.setMouseTracking(True)
        self.list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.list)
        self.commands: List[SlashCommand] = []
        self.hide()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.setStyleSheet(
            f"#SlashMenu {{ background: {theme.bg}; border: 1px solid {theme.border}; border-radius: 10px; }}"
        )
        self.list.viewport().update()

    def set_query(self, query: str) -> None:
        self.commands = filter_commands(query)
        self.list.clear()
        if not self.commands:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        for command in self.commands:
            item = QListWidgetItem(command.label)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
        if self.commands:
            self.list.setCurrentRow(0)
        rows = max(1, min(self.MAX_ROWS, len(self.commands)))
        self.setFixedSize(250, rows * _SlashDelegate.ROW_HEIGHT + 10)

    def move_selection(self, step: int) -> None:
        if self.commands:
            self.list.setCurrentRow((self.list.currentRow() + step) % len(self.commands))

    def current(self) -> Optional[SlashCommand]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_clicked(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole)
        if command is not None:
            self.chosen.emit(command)
