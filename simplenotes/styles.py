"""Design tokens (light/dark themes) and the Qt stylesheet built from them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    panel: str            # the sidebar card, pill fields
    text: str
    muted: str
    accent: str           # the app icon's blue: selected folder, OK button, checked to-dos
    accent_text: str      # accent dark enough to read as text (links)
    on_accent: str        # text drawn on the accent
    border: str
    hover: str
    selected: str         # the selected note in the list (neutral gray)
    selection: str        # selected text
    focus_ring: str
    button: str           # secondary push buttons (Cancel)
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
    panel="#F5F5F4",
    text="#1D1D1F",
    muted="#86868B",
    accent="#2578D4",
    accent_text="#1D64B5",
    on_accent="#FFFFFF",
    border="#E3E3E3",
    hover="#EBEBEA",
    selected="#DADADA",
    selection="#CCE0F8",
    focus_ring="#9CC2EE",
    button="#EBEBEB",
    preview_bg="#FAFAFA",
    code_bg="#F2F2F2",
    quote_bg="#F4F4F4",
    table_head="#F4F4F4",
    stripe="#F7F7F7",
    heading_sub="#3A3A3C",
    highlight="#FBE29A",
    highlight_text="#1D1D1F",
    danger="#E5484D",
    line_number="#C7C7CC",
    pygments_style="default",
)

DARK = Theme(
    name="dark",
    bg="#1E1E1E",
    panel="#2A2A2B",
    text="#ECECEC",
    muted="#98989D",
    accent="#2F82DD",
    accent_text="#6AAEF2",
    on_accent="#FFFFFF",
    border="#38383A",
    hover="#353537",
    selected="#3D3D3F",
    selection="#1F4673",
    focus_ring="#2D5E96",
    button="#3A3A3C",
    preview_bg="#242425",
    code_bg="#2C2C2E",
    quote_bg="#28282A",
    table_head="#2C2C2E",
    stripe="#262628",
    heading_sub="#C9C9CE",
    highlight="#7A5A12",
    highlight_text="#FFFFFF",
    danger="#FF6961",
    line_number="#4B4B50",
    pygments_style="monokai",
)

_current = LIGHT


def current_theme() -> Theme:
    """The active theme, for windows that aren't rebuilt on a theme switch (dialogs)."""
    return _current


def set_current_theme(theme: Theme) -> None:
    global _current
    _current = theme


@dataclass(frozen=True)
class Fonts:
    ui: str
    mono: str


def build_qss(t: Theme) -> str:
    """Application stylesheet. Fonts are set on widgets directly, not here,
    because QSS font rules would override the editor/preview fonts."""
    return f"""
QMainWindow, #EditorPanel, #EmptyState, #NotesPanel, #SidebarColumn {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; }}
#Sidebar {{ background: transparent; }}
QSplitter::handle {{ background: {t.border}; }}
QSplitterHandle#SidebarHandle {{ background: {t.bg}; }}

QLabel#ListTitle {{ font-size: 14px; font-weight: 700; }}
QLabel#ListSubtitle {{ color: {t.muted}; font-size: 12px; }}
QLabel#Muted, QLabel#SavedLabel, QLabel#EmptyHint, QLabel#DateLabel {{ color: {t.muted}; }}
QLabel#SavedLabel {{ font-size: 12px; }}
QLabel#Muted {{ font-size: 12px; }}
QLabel#DateLabel {{ font-size: 12px; }}
QLabel#PathLabel {{ color: {t.muted}; font-size: 11px; font-weight: 600; }}
QLabel#PathLabel:hover {{ color: {t.text}; }}
QLabel#EmptyHint {{ font-size: 14px; }}
QLabel#SheetTitle {{ font-size: 14px; font-weight: 700; }}
QLabel#SheetText {{ font-size: 13px; }}
QLabel#SheetInfo {{ color: {t.muted}; font-size: 13px; }}
QLabel#ErrorLabel {{ color: {t.danger}; font-size: 12px; }}
QFrame#SheetRule {{ background: {t.border}; border: none; }}

QListWidget {{
    background: transparent; border: none; outline: 0;
    selection-background-color: transparent;
}}
QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{
    background: transparent; border: none;
}}

QLineEdit {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 7px; padding: 5px 7px;
    selection-background-color: {t.selection}; selection-color: {t.text};
}}
QLineEdit:focus {{ border: 3px solid {t.focus_ring}; padding: 3px 5px; }}
QLineEdit#TitleEdit {{
    border: none; background: transparent; font-size: 28px; font-weight: 700; padding: 0;
}}
QLineEdit#SearchField {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 17px; padding: 4px 12px 4px 8px;
    font-size: 13px;
}}
QLineEdit#SearchField:focus {{ border: 3px solid {t.focus_ring}; padding: 2px 10px 2px 6px; }}

QTextEdit#Editor {{
    background: {t.bg}; border: none;
    selection-background-color: {t.selection}; selection-color: {t.text};
}}

QToolButton {{
    background: transparent; border: none; border-radius: 6px;
    padding: 2px 6px; color: {t.muted}; font-size: 15px;
}}
QToolButton:hover {{ background: {t.hover}; color: {t.text}; }}
QToolButton:checked {{ color: {t.accent}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
#Pill {{ background: {t.bg}; border: 1px solid {t.border}; border-radius: 17px; }}
#Pill QToolButton {{ border-radius: 14px; padding: 4px 10px; }}
#Pill QToolButton:checked {{ background: {t.selection}; }}
#Sidebar QToolButton {{ padding: 4px 6px; }}

QPushButton {{
    background: {t.button}; color: {t.text}; border: none; border-radius: 7px;
    padding: 6px 18px; min-width: 64px; font-size: 13px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.selected}; }}
QPushButton#Primary {{ background: {t.accent}; color: {t.on_accent}; }}
QPushButton#Primary:hover {{ background: {t.accent_text}; }}
QPushButton#Destructive {{ color: {t.danger}; }}
QPushButton:focus {{ outline: none; }}

QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 5px 22px 5px 14px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 10px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::right-arrow {{ width: 8px; height: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 3px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {t.bg}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px 6px; }}
"""
