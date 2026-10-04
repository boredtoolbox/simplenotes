"""Design tokens (light/dark themes) and the Qt stylesheet built from them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    panel: str
    text: str
    muted: str
    accent: str
    border: str
    hover: str
    selected: str
    preview_bg: str
    code_bg: str
    quote_bg: str
    table_head: str
    stripe: str
    heading_sub: str
    highlight: str
    highlight_text: str
    danger: str
    line_number: str
    pygments_style: str


LIGHT = Theme(
    name="light",
    bg="#FFFFFF",
    panel="#FAFAFA",
    text="#1A1A1A",
    muted="#6B7280",
    accent="#2563EB",
    border="#E5E7EB",
    hover="#F3F4F6",
    selected="#E8EEFC",
    preview_bg="#FAFAFA",
    code_bg="#F1F2F4",
    quote_bg="#F3F4F6",
    table_head="#F3F4F6",
    stripe="#F5F6F8",
    heading_sub="#374151",
    highlight="#FDE68A",
    highlight_text="#1A1A1A",
    danger="#DC2626",
    line_number="#C4C7CE",
    pygments_style="default",
)

DARK = Theme(
    name="dark",
    bg="#1B1B1E",
    panel="#202024",
    text="#E6E6E9",
    muted="#9CA3AF",
    accent="#6EA8FE",
    border="#2F2F35",
    hover="#2A2A30",
    selected="#283449",
    preview_bg="#202024",
    code_bg="#2A2A30",
    quote_bg="#26262B",
    table_head="#2A2A30",
    stripe="#25252A",
    heading_sub="#C9CBD1",
    highlight="#7A5A12",
    highlight_text="#FFFFFF",
    danger="#F87171",
    line_number="#4B4B53",
    pygments_style="monokai",
)


@dataclass(frozen=True)
class Fonts:
    ui: str
    mono: str


def build_qss(t: Theme) -> str:
    """Application stylesheet. Fonts are set on widgets directly, not here,
    because QSS font rules would override the editor/preview fonts."""
    return f"""
QMainWindow, #EditorPanel, #EmptyState {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; }}
#SidebarFrame {{ background: {t.bg}; }}
#Sidebar {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 10px; }}
QSplitter::handle {{ background: {t.border}; }}
#MainSplitter::handle {{ background: {t.bg}; }}

QLabel#HeaderTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#Muted, QLabel#SavedLabel, QLabel#EmptyHint {{ color: {t.muted}; }}
QLabel#SavedLabel {{ font-size: 11px; }}
QLabel#Muted {{ font-size: 12px; }}
QLabel#PathLabel {{ color: {t.muted}; font-size: 12px; }}
QLabel#PathLabel:hover {{ color: {t.text}; }}
QLabel#NoteTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#NotePreview, QLabel#NoteDate {{ color: {t.muted}; font-size: 12px; }}
QLabel#EmptyHint {{ font-size: 14px; }}

QTreeWidget {{
    background: transparent; border: none; outline: 0;
    show-decoration-selected: 0; selection-background-color: transparent;
}}
QTreeWidget::item, QTreeWidget::branch,
QTreeWidget::item:selected, QTreeWidget::branch:selected,
QTreeWidget::item:hover, QTreeWidget::branch:hover {{ background: transparent; border: none; }}
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border: none; padding: 6px 8px; }}
QListWidget::item:hover {{ background: {t.hover}; }}
QListWidget::item:selected {{ background: {t.selected}; color: {t.text}; }}

QLineEdit {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 4px;
    padding: 6px 8px; selection-background-color: {t.accent}; selection-color: #FFFFFF;
}}
QLineEdit:focus {{ border-color: {t.accent}; }}
QLineEdit#TitleEdit {{
    border: none; background: transparent; font-size: 32px; font-weight: 700; padding: 0;
}}

QTextEdit#Editor {{
    background: {t.bg}; border: none;
    selection-background-color: {t.accent}; selection-color: #FFFFFF;
}}

#Footer {{ border-top: 1px solid {t.border}; }}

QToolButton {{
    background: transparent; border: none; border-radius: 4px;
    padding: 2px 6px; color: {t.muted}; font-size: 15px;
}}
QToolButton:hover {{ background: {t.hover}; color: {t.text}; }}
QToolButton:checked {{ color: {t.accent}; }}
QToolButton#DeleteNoteButton {{ font-size: 14px; padding: 0 4px; }}
QToolButton#DeleteNoteButton:hover {{ color: {t.danger}; background: transparent; }}

QPushButton {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 4px; padding: 6px 14px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:default {{ border-color: {t.accent}; }}
QComboBox {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 4px; padding: 5px 8px;
}}
QComboBox QAbstractItemView {{ background: {t.bg}; border: 1px solid {t.border}; }}
QDialog, QMessageBox {{ background: {t.bg}; }}

QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.bg}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.hover}; color: {t.text}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {t.bg}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
"""
