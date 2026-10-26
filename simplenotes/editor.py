"""Notion-style live-preview markdown editor.

The document is always the note's plain markdown, which is exactly what gets
saved, so editing can never lose or reformat content. Everything else is
visual:

- LiveHighlighter formats headings, emphasis, code, links, quotes, and hides
  markdown syntax on every line except the one being edited.
- LiveEditor paints bullets, checkboxes, quote bars and dividers on top of the
  text, and shades code blocks with full-width extra selections.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QKeyEvent, QPainter, QPainterPath, QPen, QSyntaxHighlighter, QTextBlock,
    QTextBlockFormat, QTextBlockUserData, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
)
from PyQt6.QtWidgets import QTextEdit

from .models import count_words
from .slash import SlashCommand, SlashPopup
from .styles import Fonts, Theme

try:  # optional: colored code blocks
    from pygments.lexers import get_lexer_by_name
    from pygments.styles import get_style_by_name
    from pygments.util import ClassNotFound
    HAVE_PYGMENTS = True
except ImportError:  # pragma: no cover - depends on environment
    HAVE_PYGMENTS = False

INDENT = "    "
COLUMN_WIDTH = 760      # max width of the writing column (like a Notion page)
DOC_MARGIN = 28         # room left of the text for quote bars
CHECKBOX_SIZE = 18      # the painted to-do circle
TASK_BOX_WIDTH = 24     # the hidden "[ ]"/"[x]" is padded to this width so the circle fits inside it

FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)")
HEADING_RE = re.compile(r"^(#{1,6})(\s+)")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
QUOTE_RE = re.compile(r"^(\s*>\s?)")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\](?=\s|$)\s?)?")
TABLE_RE = re.compile(r"^\s*\|")
TABLE_RULE_RE = re.compile(r"^\s*\|?(\s*:?-+:?\s*\|)+\s*(:?-+:?\s*)?$")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
LINK_RE = re.compile(r"(!?)\[([^\]\n]+)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
STRIKE_RE = re.compile(r"(~~)(?=\S)(.+?)(?<=\S)~~")
ITALIC_RE = re.compile(r"(?<![*_\w])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![*_\w])")

# Editing helpers (operate on raw markdown lines).
_LIST_LINE_RE = re.compile(r"^(\s*)([-*+]|(\d+)([.)]))(\s+)(\[[ xX]\]\s+)?")
_TASK_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]")
_BULLET_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")
_QUOTE_LINE_RE = re.compile(r"^(\s*>\s?)")

HEADING_SIZES = {1: 30, 2: 24, 3: 20, 4: 17, 5: 16, 6: 15}


# ----- tables ----------------------------------------------------------------
#
# A table is a run of lines starting with "|" whose second line is a delimiter
# row ("| --- | :-: |"). Unless the cursor is inside it, its text is made
# invisible and LiveEditor paints a grid in its place.

# Inline markdown inside a cell. Group numbers are used by cell_runs().
_CELL_TOKEN_RE = re.compile(
    r"(`+)(.+?)\1"                                                  # 1-2 code span
    r"|(!?)\[([^\]]*)\]\([^)]*\)"                                 # 3-4 link / image
    r"|(\*\*|__)(?=\S)(.+?)(?<=\S)\5"                              # 5-6 bold
    r"|~~(?=\S)(.+?)(?<=\S)~~"                                        # 7 strikethrough
    r"|(?<![*_\w])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\8(?![*_\w])"  # 8-9 italic
)

CellRun = Tuple[str, frozenset]  # (text, styles: bold/italic/strike/code/link/image)
CellLine = List[Tuple[str, frozenset, float]]  # one wrapped line of a cell: (text, styles, width)
_WRAP_TOKEN_RE = re.compile(r"\s+|\S+")


def table_bounds(block: QTextBlock) -> Optional[Tuple[QTextBlock, QTextBlock]]:
    """(first, last) block of the table containing block, or None if it isn't one."""
    if not block.isValid() or not TABLE_RE.match(block.text()):
        return None
    first = block
    while first.previous().isValid() and TABLE_RE.match(first.previous().text()):
        first = first.previous()
    last = block
    while last.next().isValid() and TABLE_RE.match(last.next().text()):
        last = last.next()
    if first == last or not TABLE_RULE_RE.match(first.next().text()):
        return None
    return first, last


def split_cells(line: str) -> List[Tuple[str, int]]:
    """Cells of a table row as (content, index where the content starts in line)."""
    cells: List[Tuple[str, int]] = []
    i = 0
    while i < len(line) and line[i].isspace():
        i += 1
    if i < len(line) and line[i] == "|":
        i += 1
    start = i
    for k in range(i, len(line) + 1):
        if k == len(line) or (line[k] == "|" and line[k - 1] != "\\"):
            raw = line[start:k]
            content = raw.strip()
            if k == len(line) and not content and cells:
                break  # nothing after the closing pipe
            # Empty cell: aim just after the first space, so typing gives "| x |".
            offset = len(raw) - len(raw.lstrip()) if content else min(1, len(raw))
            cells.append((content, start + offset))
            start = k + 1
    return cells


def cell_alignment(delimiter: str) -> Qt.AlignmentFlag:
    if delimiter.startswith(":") and delimiter.endswith(":"):
        return Qt.AlignmentFlag.AlignHCenter
    if delimiter.endswith(":"):
        return Qt.AlignmentFlag.AlignRight
    return Qt.AlignmentFlag.AlignLeft


def cell_runs(text: str, styles: frozenset = frozenset()) -> List[CellRun]:
    """Split cell markdown into styled runs of plain text, e.g.
    "**Grid** [docs](u)" -> [("Grid", {bold}), (" ", {}), ("docs", {link})]."""
    runs: List[CellRun] = []

    def plain(chunk: str) -> None:
        if chunk:
            runs.append((chunk.replace("\\|", "|"), styles))

    pos = 0
    for m in _CELL_TOKEN_RE.finditer(text):
        plain(text[pos:m.start()])
        if m.group(1):
            runs.append((m.group(2).replace("\\|", "|"), styles | {"code"}))
        elif m.group(4) is not None and m.group(0).endswith(")"):
            if m.group(3):
                runs.append((m.group(4) or "image", styles | {"image"}))
            else:
                runs.extend(cell_runs(m.group(4), styles | {"link"}))
        elif m.group(5):
            runs.extend(cell_runs(m.group(6), styles | {"bold"}))
        elif m.group(7):
            runs.extend(cell_runs(m.group(7), styles | {"strike"}))
        else:
            runs.extend(cell_runs(m.group(9), styles | {"italic"}))
        pos = m.end()
    plain(text[pos:])
    return runs


def fit_columns(widths: List[float], available: float, floor: float = 48.0) -> List[float]:
    """Shrink only the widest columns until the table fits; narrow columns keep their width."""
    if sum(widths) <= available:
        return widths
    remaining, ordered = available, sorted(widths)
    cap = floor
    for i, width in enumerate(ordered):
        share = remaining / (len(ordered) - i)
        if width <= share:
            remaining -= width
        else:
            cap = max(floor, share)
            break
    return [min(width, cap) for width in widths]


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _utf16_mapper(text: str) -> Callable[[int], int]:
    """Python string index -> UTF-16 offset (what Qt positions use)."""
    if all(ord(ch) < 0x10000 for ch in text):
        return lambda i: i
    offsets = [0]
    for ch in text:
        offsets.append(offsets[-1] + (2 if ord(ch) >= 0x10000 else 1))
    return lambda i: offsets[i]


class BlockInfo(QTextBlockUserData):
    """What the highlighter learned about a line, for the editor's painting."""

    def __init__(self, kind: str = ""):
        super().__init__()
        self.kind = kind  # heading|quote|bullet|ordered|task|hr|code|fence|table|table_row|table_rule|""
        self.marker: Optional[Tuple[int, int]] = None   # bullet char (utf16 start, end)
        self.box: Optional[Tuple[int, int]] = None      # "[ ]" (utf16 start, end)
        self.checked = False
        self.words = 0  # this line's word count; LiveEditor.word_count() sums them
        self.row_px = 0  # table_row: pixel size of the character that sets the row height


def _char_format(color: Optional[str] = None, *, pixel_size: Optional[int] = None,
                 weight: Optional[QFont.Weight] = None, italic: bool = False, strike: bool = False,
                 underline: bool = False, family: Optional[str] = None,
                 background: Optional[str] = None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if any(v is not None for v in (pixel_size, weight, family)):
        font = QFont()
        if family:
            font.setFamily(family)
            font.setStyleHint(QFont.StyleHint.Monospace)
        if pixel_size:
            font.setPixelSize(pixel_size)
        if weight is not None:
            font.setWeight(weight)
        fmt.setFont(font, QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
    if color:
        fmt.setForeground(QColor(color))
    if italic:
        fmt.setFontItalic(True)
    if strike:
        fmt.setFontStrikeOut(True)
    if underline:
        fmt.setFontUnderline(True)
    if background:
        fmt.setBackground(QColor(background))
    return fmt


class LiveHighlighter(QSyntaxHighlighter):
    def __init__(self, editor: "LiveEditor"):
        # Owned by the editor, not a document: set_markdown swaps in a new document per
        # note, and a highlighter parented to the old one would be deleted with it.
        super().__init__(editor)
        self.setDocument(editor.document())
        self.editor = editor
        self._languages: List[str] = []      # block state n >= 1 means "in code, language n-1"
        self._lexers: Dict[str, object] = {}
        self._token_formats: Dict[object, Optional[QTextCharFormat]] = {}
        self._style = None
        self.f: Dict[str, QTextCharFormat] = {}
        self.f_heading: Dict[int, QTextCharFormat] = {}
        self._row_formats: Dict[int, QTextCharFormat] = {}

    def set_theme(self, t: Theme, fonts: Fonts) -> None:
        mono = fonts.mono
        transparent = "#00000000"
        self._box_pads: Dict[str, QTextCharFormat] = {}
        self.f = {
            "marker": _char_format(t.muted),
            "hidden": self._hidden_format(),
            "transparent": _char_format(transparent),
            "bold": _char_format(weight=QFont.Weight.Bold),
            "italic": _char_format(italic=True),
            "strike": _char_format(t.muted, strike=True),
            "done": _char_format(t.text),  # like Notes: checked items keep their text
            "code_span": _char_format(t.heading_sub, family=mono, pixel_size=13, background=t.code_bg),
            "link": _char_format(t.accent_text, underline=True),
            "image": _char_format(t.muted, italic=True),
            "quote": _char_format(t.heading_sub, italic=True),
            "code": _char_format(t.text, family=mono, pixel_size=13),
            "fence": _char_format(t.muted, family=mono, pixel_size=12),
            "table": _char_format(t.text, family=mono, pixel_size=13),
            "pipe": _char_format(t.muted),
            "rule": _char_format(t.muted),
            "bullet": _char_format(t.accent),
        }
        self.f_heading = {
            level: _char_format(t.text if level <= 2 else t.heading_sub, pixel_size=px, weight=QFont.Weight.Bold)
            for level, px in HEADING_SIZES.items()
        }
        self._token_formats.clear()
        self._style = None
        if HAVE_PYGMENTS:
            try:
                self._style = get_style_by_name(t.pygments_style)
            except ClassNotFound:
                self._style = None
        self.rehighlight()

    def _table_row_format(self, pixel_size: int) -> QTextCharFormat:
        """Invisible and (nearly) zero-width, but tall: one character in this format sets
        the height of a grid row, while the rest of the row is hidden so it never wraps."""
        if pixel_size not in self._row_formats:
            fmt = _char_format("#00000000", pixel_size=pixel_size)
            fmt.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
            fmt.setFontLetterSpacing(0)
            self._row_formats[pixel_size] = fmt
        return self._row_formats[pixel_size]

    @staticmethod
    def _hidden_format() -> QTextCharFormat:
        """Invisible and (nearly) zero-width, so hidden syntax leaves no gap."""
        fmt = _char_format("#00000000", pixel_size=1)
        fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        fmt.setFontLetterSpacing(-0.6)
        return fmt

    # ----- helpers -------------------------------------------------------

    def _box_pad(self, box: str) -> QTextCharFormat:
        """Format for a to-do box's closing "]": letter spacing that makes the whole box
        TASK_BOX_WIDTH wide, so the circle painted there never covers the revealed "- "
        before it or the text after it, and "[x]" lines up with the narrower "[ ]"."""
        fmt = self._box_pads.get(box)
        if fmt is None:
            natural = QFontMetricsF(self.editor.document().defaultFont()).horizontalAdvance(box)
            fmt = QTextCharFormat(self.f["transparent"])
            fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            fmt.setFontLetterSpacing(max(0.0, TASK_BOX_WIDTH - natural))
            self._box_pads[box] = fmt
        return fmt

    def _merge(self, start: int, length: int, fmt: QTextCharFormat) -> None:
        for i in range(start, start + length):
            merged = self.format(i)
            merged.merge(fmt)
            self.setFormat(i, 1, merged)

    def _language_state(self, language: str) -> int:
        if language not in self._languages:
            self._languages.append(language)
        return self._languages.index(language) + 1

    def _token_format(self, token_type) -> Optional[QTextCharFormat]:
        if token_type not in self._token_formats:
            fmt = None
            if self._style is not None:
                style = self._style.style_for_token(token_type)
                if style.get("color") or style.get("bold") or style.get("italic"):
                    fmt = QTextCharFormat()
                    if style.get("color"):
                        fmt.setForeground(QColor("#" + style["color"]))
                    if style.get("bold"):
                        fmt.setFontWeight(QFont.Weight.Bold)
                    if style.get("italic"):
                        fmt.setFontItalic(True)
            self._token_formats[token_type] = fmt
        return self._token_formats[token_type]

    def _highlight_code(self, text: str, u, language: str) -> None:
        if not HAVE_PYGMENTS or not language or self._style is None:
            return
        if language not in self._lexers:
            try:
                self._lexers[language] = get_lexer_by_name(language, stripnl=False, ensurenl=False)
            except ClassNotFound:
                self._lexers[language] = None
        lexer = self._lexers[language]
        if lexer is None:
            return
        pos = 0
        for token_type, value in lexer.get_tokens(text):
            end = min(len(text), pos + len(value))
            fmt = self._token_format(token_type)
            if fmt is not None and end > pos:
                self._merge(u(pos), u(end) - u(pos), fmt)
            pos = end
            if pos >= len(text):
                break

    # ----- main ----------------------------------------------------------

    def highlightBlock(self, text: str) -> None:
        editor = self.editor
        reveal = editor.reveals(self.currentBlock())
        u = _utf16_mapper(text)
        length = u(len(text))
        info = BlockInfo()
        info.words = count_words(text)  # only re-counted for lines that change
        previous = self.previousBlockState()
        fence = FENCE_RE.match(text)

        if previous >= 1 or fence:
            if fence:
                info.kind = "fence"
                state = 0 if previous >= 1 else self._language_state(fence.group(2).lower())
                self.setFormat(0, length, self.f["fence"])
                if not reveal:  # leave just the language label
                    start, end = fence.span(1)
                    self.setFormat(u(start), u(end) - u(start), self.f["hidden"])
            else:
                info.kind = "code"
                state = previous
                self.setFormat(0, length, self.f["code"])
                self._highlight_code(text, u, self._languages[previous - 1])
            self.setCurrentBlockState(state)
            self.setCurrentBlockUserData(info)
            editor.schedule_decorations()
            return
        self.setCurrentBlockState(0)

        if HR_RE.match(text):
            info.kind = "hr"
            self.setFormat(0, length, self.f["rule"] if reveal else self.f["transparent"])
            self.setCurrentBlockUserData(info)
            return
        bounds = table_bounds(self.currentBlock()) if TABLE_RE.match(text) else None
        if bounds is not None and not reveal:
            is_rule = self.currentBlock() == bounds[0].next()
            info.kind = "table_rule" if is_rule else "table_row"
            self.setFormat(0, length, self.f["hidden"])
            if not is_rule:
                info.row_px = editor.table_row_pixel_size(self.currentBlock(), bounds)
                self.setFormat(0, u(1), self._table_row_format(info.row_px))
            self.setCurrentBlockUserData(info)
            editor.schedule_decorations()
            return
        if TABLE_RE.match(text):
            info.kind = "table"
            self.setFormat(0, length, self.f["table"])
            for i, ch in enumerate(text):
                if ch == "|":
                    self._merge(u(i), u(i + 1) - u(i), self.f["pipe"])
            self.setCurrentBlockUserData(info)
            return

        hide: List[Tuple[int, int]] = []
        content_start = 0

        def marker(start: int, end: int) -> None:
            if reveal:
                self._merge(u(start), u(end) - u(start), self.f["marker"])
            else:
                hide.append((start, end))

        heading = HEADING_RE.match(text)
        quote = QUOTE_RE.match(text)
        listing = LIST_RE.match(text)
        if heading:
            info.kind = "heading"
            self.setFormat(0, length, self.f_heading[len(heading.group(1))])
            marker(0, heading.end())
            content_start = heading.end()
        elif quote:
            info.kind = "quote"
            self.setFormat(0, length, self.f["quote"])
            marker(0, quote.end())
            content_start = quote.end()
        elif listing:
            symbol_start, symbol_end = listing.span(2)
            box = listing.group(4)
            if box:
                info.kind = "task"
                info.checked = box[1] in "xX"
                box_start = listing.start(4)
                info.box = (u(box_start), u(box_start + 3))
                if not editor.reveal_all:
                    self.setFormat(u(box_start), u(box_start + 3) - u(box_start), self.f["transparent"])
                    self.setFormat(u(box_start + 2), 1, self._box_pad(text[box_start:box_start + 3]))
                marker(symbol_start, listing.end(3))
                if info.checked:
                    self._merge(u(listing.end()), length - u(listing.end()), self.f["done"])
            elif listing.group(2) in "-*+":
                info.kind = "bullet"
                info.marker = (u(symbol_start), u(symbol_end))
                if reveal:
                    self._merge(u(symbol_start), u(symbol_end) - u(symbol_start), self.f["bullet"])
                else:
                    self.setFormat(u(symbol_start), u(symbol_end) - u(symbol_start), self.f["transparent"])
            else:
                info.kind = "ordered"
                self._merge(u(symbol_start), u(symbol_end) - u(symbol_start), self.f["marker"])
            content_start = listing.end()

        self._inline(text, u, content_start, marker)
        for start, end in hide:
            self.setFormat(u(start), u(end) - u(start), self.f["hidden"])
        self.setCurrentBlockUserData(info)
        editor.schedule_decorations()  # a line may have stopped being code

    def _inline(self, text: str, u, start: int, marker) -> None:
        protected: List[Tuple[int, int]] = []

        def free(a: int, b: int) -> bool:
            return not any(x < b and a < y for x, y in protected)

        for m in CODE_SPAN_RE.finditer(text, start):
            ticks = len(m.group(1))
            inner_start, inner_end = m.start() + ticks, m.end() - ticks
            self._merge(u(inner_start), u(inner_end) - u(inner_start), self.f["code_span"])
            marker(m.start(), inner_start)
            marker(inner_end, m.end())
            protected.append(m.span())

        for m in LINK_RE.finditer(text, start):
            if not free(*m.span()):
                continue
            label_start, label_end = m.span(2)
            fmt = self.f["image"] if m.group(1) else self.f["link"]
            self._merge(u(label_start), u(label_end) - u(label_start), fmt)
            marker(m.start(), label_start)
            marker(label_end, m.end())
            protected.append((label_end, m.end()))  # URLs often contain _ and *

        for regex, key in ((BOLD_RE, "bold"), (STRIKE_RE, "strike"), (ITALIC_RE, "italic")):
            for m in regex.finditer(text, start):
                if not free(*m.span()):
                    continue
                size = len(m.group(1))
                inner_start, inner_end = m.start() + size, m.end() - size
                self._merge(u(inner_start), u(inner_end) - u(inner_start), self.f[key])
                marker(m.start(), inner_start)
                marker(inner_end, m.end())


class LiveEditor(QTextEdit):
    column_margin_changed = pyqtSignal(int)  # left edge of the text, for aligning the title

    def __init__(self, theme: Theme, fonts: Fonts, parent=None):
        super().__init__(parent)
        self.setObjectName("Editor")
        self.setAcceptRichText(False)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.theme = theme
        self.fonts = fonts
        self.reveal_all = False
        self.search_term = ""
        self._active_block = -1
        self._active_table: Optional[Tuple[int, int]] = None  # block numbers of the table being edited
        self._table_hits: List[tuple] = []  # painted grids, for mapping clicks to cells
        self._table_cache: Dict[tuple, tuple] = {}  # see _table_layout
        self._row_px_cache: Dict[int, int] = {}
        self._metrics_cache: Dict[frozenset, QFontMetricsF] = {}
        self._loading = False
        self._side_margin = (-1, -1)  # (left, right) viewport margins
        self._column = -1              # current text width
        # The text is laid out at a fixed width (the column), not the widget width, so
        # resizing the editor (e.g. during the sidebar animation) doesn't re-lay out
        # the whole note unless the column itself has to shrink.
        self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        font = QFont(fonts.ui)
        font.setPixelSize(15)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.document().setDocumentMargin(DOC_MARGIN)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setPlaceholderText("Type '/' for commands")
        self.viewport().setMouseTracking(True)

        self._decor_timer = QTimer(self, singleShot=True, interval=15)
        self._decor_timer.timeout.connect(self._update_decorations)
        self._table_timer = QTimer(self, singleShot=True, interval=120)
        self._table_timer.timeout.connect(self._refresh_table_rows)
        self.highlighter = LiveHighlighter(self)
        self.highlighter.set_theme(theme, fonts)
        self.cursorPositionChanged.connect(self._on_cursor_moved)
        self._slash_start: Optional[int] = None  # document position of the "/" while the menu is open
        self.slash_popup = SlashPopup(self, theme, fonts.mono)
        self.slash_popup.set_theme(theme)
        self.slash_popup.chosen.connect(self._apply_slash)
        self.cursorPositionChanged.connect(self._update_slash)
        self.textChanged.connect(self._update_slash)
        self.verticalScrollBar().valueChanged.connect(self._position_slash)
        self.document().contentsChange.connect(self._on_contents_change)

    # ----- content -------------------------------------------------------

    def set_markdown(self, text: str) -> None:
        """Load a note. The note is built and styled in a fresh document *before* it is
        attached to this editor: restyling a document that a QTextEdit is already laying
        out costs time per block that grows with the document (quadratic overall; 4,000
        lines took ~3s), while styling a detached one is near-instant (~0.02s)."""
        self.close_slash()
        self._active_block = -1  # nothing revealed until the user moves the cursor
        self._active_table = None
        self._table_cache.clear()  # keyed by revision, which a new document starts over
        old = self.document()
        # Documents we created are ours to delete; QTextEdit deletes its own initial one.
        delete_old = old.parent() is self
        doc = QTextDocument(self)
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(DOC_MARGIN)
        # Attach the highlighter while the document is still empty: attaching to a
        # non-empty one queues a full pass that would run after setDocument() below,
        # i.e. on the slow, laid-out path. Inserted text is styled as it goes in.
        self.highlighter.setDocument(doc)
        # Airier lines: inserted lines inherit the cursor's block format. Block formats
        # aren't part of the plain text, so this never touches the saved markdown.
        cursor = QTextCursor(doc)
        spacing = QTextBlockFormat()
        spacing.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        spacing.setBottomMargin(2)
        cursor.setBlockFormat(spacing)
        cursor.insertText(text)
        doc.clearUndoRedoStacks()
        doc.setModified(False)  # loading isn't an edit; autosave keys off isModified()
        self._loading = True
        self.setDocument(doc)
        self._loading = False
        doc.contentsChange.connect(self._on_contents_change)
        if delete_old:
            old.deleteLater()
        self.schedule_decorations()

    def markdown(self) -> str:
        return self.toPlainText()

    def word_count(self) -> int:
        """Total words, summed from per-line counts the highlighter keeps up to date
        (words never span lines), so typing doesn't re-count the whole note."""
        total, block = 0, self.document().begin()
        while block.isValid():
            info = block.userData()
            total += info.words if isinstance(info, BlockInfo) else count_words(block.text())
            block = block.next()
        return total

    def character_count(self) -> int:
        return self.document().characterCount() - 1  # minus the final paragraph separator

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.highlighter.set_theme(theme, self.fonts)
        self.slash_popup.set_theme(theme)
        self.schedule_decorations()

    def set_reveal_all(self, reveal: bool) -> None:
        """Show every markdown character (the "Show markdown" toggle)."""
        self.reveal_all = reveal
        self.highlighter.rehighlight()
        self.viewport().update()

    def set_search(self, term: str) -> None:
        self.search_term = term.strip()
        self.schedule_decorations()

    def reveals(self, block: QTextBlock) -> bool:
        number = block.blockNumber()
        if self.reveal_all or number == self._active_block:
            return True
        return self._active_table is not None and self._active_table[0] <= number <= self._active_table[1]

    def _on_cursor_moved(self, force: bool = False) -> None:
        if self._loading:
            return  # attaching a new document moves the cursor; nothing is being edited yet
        block = self.textCursor().block()
        number = block.blockNumber()
        bounds = table_bounds(block)
        table = (bounds[0].blockNumber(), bounds[1].blockNumber()) if bounds else None
        if not force and number == self._active_block and table == self._active_table:
            return
        affected = {self._active_block, number}
        for span in (self._active_table, table):
            if span is not None:
                affected.update(range(span[0], span[1] + 1))
        self._active_block, self._active_table = number, table
        for n in sorted(affected):
            target = self.document().findBlockByNumber(n)
            if target.isValid():
                self.highlighter.rehighlightBlock(target)

    def _on_contents_change(self, position: int, _removed: int, _added: int) -> None:
        """Typing in one table line can change how its neighbours render (e.g. adding the
        delimiter row turns the lines into a table), so re-highlight the whole run."""
        if self._loading:
            return  # nothing is being edited; the full rehighlight below covers it
        block = self.document().findBlock(position)
        if any(TABLE_RE.match(b.text()) for b in (block, block.previous(), block.next()) if b.isValid()):
            QTimer.singleShot(0, self._refresh_tables_near_cursor)

    def _refresh_tables_near_cursor(self) -> None:
        block = self.textCursor().block()
        first = last = block
        while first.previous().isValid() and TABLE_RE.match(first.previous().text()):
            first = first.previous()
        while last.next().isValid() and TABLE_RE.match(last.next().text()):
            last = last.next()
        self._on_cursor_moved(force=True)
        blocks = [first.previous()] if first.previous().isValid() else []
        block = first
        while block.isValid():
            blocks.append(block)
            if block == last:
                break
            block = block.next()
        if last.next().isValid():
            blocks.append(last.next())
        for block in blocks:
            self.highlighter.rehighlightBlock(block)

    # ----- layout --------------------------------------------------------

    def resizeEvent(self, event) -> None:
        # Center a column of at most COLUMN_WIDTH. Only a change of the column width
        # itself triggers a relayout (see FixedPixelWidth in __init__).
        scrollbar = self.verticalScrollBar().width() if self.verticalScrollBar().isVisible() else 0
        column = max(100, min(COLUMN_WIDTH, self.width() - scrollbar - 16))
        if column != self._column:
            self._column = column
            self.setLineWrapColumnOrWidth(column)
            self._table_timer.start()  # wrapped table cells may need more or fewer lines
        spare = self.width() - scrollbar - column
        left = max(8, spare // 2)
        right = max(8, spare - left)
        if (left, right) != self._side_margin:
            if left != self._side_margin[0]:
                self.column_margin_changed.emit(left + DOC_MARGIN)
            self._side_margin = (left, right)
            self.setViewportMargins(left, 8, right, 0)
        super().resizeEvent(event)

    def schedule_decorations(self) -> None:
        self._decor_timer.start()

    def _update_decorations(self) -> None:
        """Search-match highlights (and a repaint for code-block backgrounds)."""
        selections = []
        if self.search_term:
            highlight = QColor(self.theme.highlight)
            cursor = QTextCursor(self.document())
            while True:
                cursor = self.document().find(self.search_term, cursor)
                if cursor.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(highlight)
                sel.format.setForeground(QColor(self.theme.highlight_text))
                sel.cursor = cursor
                selections.append(sel)
        self.setExtraSelections(selections)
        self.viewport().update()

    # ----- painting ------------------------------------------------------

    def _offset(self) -> QPointF:
        return QPointF(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())

    def _char_rect(self, block: QTextBlock, start: int, end: int) -> Optional[QRectF]:
        """Viewport rectangle covering block-relative UTF-16 positions [start, end)."""
        layout = block.layout()
        line = layout.lineForTextPosition(start)
        if not line.isValid():
            return None

        def x_at(pos: int) -> float:
            value = line.cursorToX(pos)
            return value[0] if isinstance(value, tuple) else value

        origin = layout.position() + self._offset()
        return QRectF(origin.x() + x_at(start), origin.y() + line.y(), x_at(end) - x_at(start), line.height())

    def _box_rect(self, block: QTextBlock, info: BlockInfo) -> Optional[QRectF]:
        chars = self._char_rect(block, *info.box)
        if chars is None:
            return None
        size = CHECKBOX_SIZE
        x = chars.left() + (chars.width() - size) / 2  # the box is padded to fit it (_box_pad)
        return QRectF(x, chars.center().y() - size / 2, size, size)

    def _visible_blocks(self, event):
        """(block, viewport rect, info or None) for blocks intersecting the paint event."""
        layout = self.document().documentLayout()
        offset = self._offset()
        bottom = event.rect().bottom()
        block = self.cursorForPosition(QPoint(0, 0)).block()
        if block.previous().isValid():
            block = block.previous()
        while block.isValid():
            rect = layout.blockBoundingRect(block).translated(offset)
            if rect.top() > bottom:
                break
            info = block.userData()
            yield block, rect, info if isinstance(info, BlockInfo) else None
            block = block.next()

    def paintEvent(self, event) -> None:
        # Code-block backgrounds go under the text: one rounded panel per block.
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.code_bg))
        panel: Optional[QRectF] = None
        for _block, rect, info in self._visible_blocks(event):
            if info is not None and info.kind in ("code", "fence"):
                row = QRectF(rect.left() - 12, rect.top(), rect.width() + 24, rect.height())
                panel = row if panel is None else panel.united(row)
            elif panel is not None:
                painter.drawRoundedRect(panel, 4, 4)
                panel = None
        if panel is not None:
            painter.drawRoundedRect(panel, 4, 4)
        painter.end()

        super().paintEvent(event)
        self._table_hits = []
        if self.reveal_all:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painted_tables = set()
        for block, rect, info in self._visible_blocks(event):
            if info is None or not info.kind:
                continue
            if info.kind in ("table_row", "table_rule"):
                bounds = table_bounds(block)
                if bounds is not None and bounds[0].blockNumber() not in painted_tables:
                    painted_tables.add(bounds[0].blockNumber())
                    self._paint_table(painter, *bounds)
            else:
                self._paint_block(painter, block, rect, info, self.theme)
        painter.end()

    def _paint_table(self, painter: QPainter, first: QTextBlock, last: QTextBlock) -> None:
        t = self.theme
        layout = self.document().documentLayout()
        offset = self._offset()
        rows = [first] + self._table_blocks(first, last)[2:]  # without the delimiter row
        aligns, widths, cells, _ = self._table_layout(first, last)

        # Each row band runs to the next row's top, so the collapsed delimiter line adds no gap.
        rects = [layout.blockBoundingRect(b).translated(offset) for b in rows]
        tops = [r.top() for r in rects]
        if len(tops) >= 3:  # last row: its line height plus spacing, as the next block's top would be
            fmt = last.blockFormat()
            factor = 1.0
            if fmt.lineHeightType() == QTextBlockFormat.LineHeightTypes.ProportionalHeight.value:
                factor = fmt.lineHeight() / 100
            tops.append(tops[-1] + rects[-1].height() * factor + fmt.bottomMargin())
        else:
            tops.append(layout.blockBoundingRect(last).translated(offset).bottom() + 4)
        xs = [rects[0].left()]
        for w in widths:
            xs.append(xs[-1] + w)
        grid = QRectF(xs[0], tops[0], xs[-1] - xs[0], tops[-1] - tops[0])

        outline = QPainterPath()
        outline.addRoundedRect(grid, 4, 4)
        painter.save()
        painter.setClipPath(outline)
        for r in range(len(rows)):
            fill = t.table_head if r == 0 else (t.stripe if r % 2 == 0 else None)
            if fill:
                painter.fillRect(QRectF(grid.left(), tops[r], grid.width(), tops[r + 1] - tops[r]), QColor(fill))
        painter.setPen(QPen(QColor(t.border), 1))
        for y in tops[1:-1]:
            painter.drawLine(QPointF(grid.left(), y), QPointF(grid.right(), y))
        for x in xs[1:-1]:
            painter.drawLine(QPointF(x, grid.top()), QPointF(x, grid.bottom()))
        pad = self.TABLE_PAD
        for r, row in enumerate(cells[:len(rows)]):
            for c, lines in enumerate(row):
                cell = QRectF(xs[c] + pad, tops[r], xs[c + 1] - xs[c] - 2 * pad, tops[r + 1] - tops[r])
                align = aligns[c] if c < len(aligns) else Qt.AlignmentFlag.AlignLeft
                self._draw_lines(painter, lines, cell, align)
        painter.restore()
        painter.setPen(QPen(QColor(t.border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)
        self._table_hits.append((grid, xs, tops, rows))

    # table layout ---------------------------------------------------------

    TABLE_PAD = 10.0  # horizontal padding inside a cell
    ROW_PX = 19       # pixel size of the row-height character for a one-line row

    @staticmethod
    def _table_blocks(first: QTextBlock, last: QTextBlock) -> List[QTextBlock]:
        blocks, block = [], first
        while block.isValid():
            blocks.append(block)
            if block == last:
                break
            block = block.next()
        return blocks

    def _table_width(self) -> float:
        column = self.lineWrapColumnOrWidth()
        return float((column if column > 0 else COLUMN_WIDTH) - 2 * DOC_MARGIN)

    def _table_layout(self, first: QTextBlock, last: QTextBlock):
        """(alignments, column widths, wrapped lines per cell, line count per row) of a
        table, cached per document revision and column width. Shared by the highlighter,
        which sizes each row to its line count, and the painter."""
        key = (first.blockNumber(), last.blockNumber(), first.document().revision(), self._table_width())
        cached = self._table_cache.get(key)
        if cached is not None:
            return cached
        texts = [b.text() for b in self._table_blocks(first, last)]
        aligns = [cell_alignment(c) for c, _ in split_cells(texts[1])]
        rows = [split_cells(text) for i, text in enumerate(texts) if i != 1]
        columns = max(1, len(rows[0]))
        pad = self.TABLE_PAD
        header = frozenset({"bold"})
        contents = [[cell_runs(cells[c][0], header if r == 0 else frozenset()) if c < len(cells) else []
                     for c in range(columns)] for r, cells in enumerate(rows)]
        widths = [48.0] * columns
        for row in contents:
            for c, runs in enumerate(row):
                text_width = sum(self._run_width(run) for run in runs) + 4  # +4: rounding slack
                widths[c] = max(widths[c], text_width + 2 * pad)
        widths = fit_columns(widths, self._table_width())
        cells = [[self._wrap_runs(runs, widths[c] - 2 * pad) for c, runs in enumerate(row)] for row in contents]
        row_lines = [max([len(lines) for lines in row] + [1]) for row in cells]
        if len(self._table_cache) > 64:
            self._table_cache.clear()
        result = self._table_cache[key] = (aligns, widths, cells, row_lines)
        return result

    def table_row_pixel_size(self, block: QTextBlock, bounds: Tuple[QTextBlock, QTextBlock]) -> int:
        """Pixel size of the invisible character that makes this grid row tall enough
        for its wrapped cells."""
        index = block.blockNumber() - bounds[0].blockNumber()  # 1 is the delimiter row
        row_lines = self._table_layout(*bounds)[3]
        return self._row_pixel_size(row_lines[index - 1 if index > 1 else 0])

    def _row_pixel_size(self, lines: int) -> int:
        if lines not in self._row_px_cache:
            def height(px: int) -> float:
                font = QFont(self.fonts.ui)
                font.setPixelSize(px)
                return QFontMetricsF(font).height()

            # Line heights are proportional (150%, see set_markdown), so each extra cell
            # line needs line_spacing / 1.5 more of the character's height.
            target = height(self.ROW_PX) + (lines - 1) * self._cell_line_height() / 1.5
            px = self.ROW_PX
            while height(px) < target:
                px += 1
            self._row_px_cache[lines] = px
        return self._row_px_cache[lines]

    def _cell_line_height(self) -> float:
        return QFontMetricsF(self._run_font(frozenset())).lineSpacing()

    def _refresh_table_rows(self) -> None:
        """Re-highlight grid rows whose height no longer fits (the column width changed)."""
        block = self.document().begin()
        while block.isValid():
            info = block.userData()
            if isinstance(info, BlockInfo) and info.kind == "table_row":
                bounds = table_bounds(block)
                if bounds is not None and self.table_row_pixel_size(block, bounds) != info.row_px:
                    self.highlighter.rehighlightBlock(block)
            block = block.next()
        self.viewport().update()

    # cell runs ------------------------------------------------------------

    CODE_PAD = 3.0  # horizontal padding inside an inline-code chip

    def _run_font(self, styles: frozenset) -> QFont:
        if "code" in styles:
            font = QFont(self.fonts.mono)
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPixelSize(13)
        else:
            font = QFont(self.fonts.ui)
            font.setPixelSize(14)
        if "bold" in styles:
            font.setWeight(QFont.Weight.DemiBold)
        font.setItalic("italic" in styles or "image" in styles)
        font.setStrikeOut("strike" in styles)
        font.setUnderline("link" in styles)
        return font

    def _metrics(self, styles: frozenset) -> QFontMetricsF:
        metrics = self._metrics_cache.get(styles)
        if metrics is None:
            # Measure for the viewport so widths match what gets drawn there.
            metrics = self._metrics_cache[styles] = QFontMetricsF(self._run_font(styles), self.viewport())
        return metrics

    def _run_width(self, run: CellRun) -> float:
        text, styles = run
        extra = 2 * self.CODE_PAD if "code" in styles else 0.0
        return self._metrics(styles).horizontalAdvance(text) + extra

    def _wrap_runs(self, runs: List[CellRun], width: float) -> List[CellLine]:
        """Word-wrap styled runs into lines that fit width. A word wider than the
        cell is broken between characters."""
        lines: List[CellLine] = [[]]
        x = 0.0

        def add(text: str, styles: frozenset) -> None:
            nonlocal x
            line = lines[-1]
            if line and line[-1][1] == styles:
                merged = line[-1][0] + text
                line[-1] = (merged, styles, self._run_width((merged, styles)))
            else:
                line.append((text, styles, self._run_width((text, styles))))
            x = sum(w for _, _, w in line)

        def new_line() -> None:
            nonlocal x
            line = lines[-1]
            while line and not line[-1][0].strip():
                line.pop()
            if line and line[-1][0] != line[-1][0].rstrip():
                text, styles, _ = line[-1]
                line[-1] = (text.rstrip(), styles, self._run_width((text.rstrip(), styles)))
            lines.append([])
            x = 0.0

        def extra_for(styles: frozenset) -> float:
            line = lines[-1]
            if "code" in styles and not (line and line[-1][1] == styles):
                return 2 * self.CODE_PAD
            return 0.0

        for text, styles in runs:
            metrics = self._metrics(styles)
            for token in _WRAP_TOKEN_RE.findall(text):
                if token.isspace():
                    if lines[-1]:
                        add(token, styles)
                    continue
                needed = metrics.horizontalAdvance(token) + extra_for(styles)
                if x + needed > width and lines[-1]:
                    new_line()
                    needed = metrics.horizontalAdvance(token) + extra_for(styles)
                while needed > width - x and len(token) > 1:  # too wide even alone: split it
                    room = width - x - extra_for(styles)
                    cut = 1
                    while cut < len(token) and metrics.horizontalAdvance(token[:cut + 1]) <= room:
                        cut += 1
                    add(token[:cut], styles)
                    token = token[cut:]
                    new_line()
                    needed = metrics.horizontalAdvance(token) + extra_for(styles)
                add(token, styles)
        new_line()
        lines.pop()
        return lines or [[]]

    def _draw_lines(self, painter: QPainter, lines: List[CellLine], cell: QRectF,
                    align: Qt.AlignmentFlag) -> None:
        """Draw a cell's wrapped lines, vertically centered and aligned in cell."""
        line_height = self._cell_line_height()
        top = cell.center().y() - len(lines) * line_height / 2
        for i, line in enumerate(lines):
            band = QRectF(cell.left(), top + i * line_height, cell.width(), line_height)
            self._draw_runs(painter, line, band, align)

    def _draw_runs(self, painter: QPainter, runs: CellLine, cell: QRectF, align: Qt.AlignmentFlag) -> None:
        """Draw styled runs on one line, aligned in cell; cut off with "…" if too long."""
        t = self.theme
        total = sum(width for _, _, width in runs)
        if align & Qt.AlignmentFlag.AlignRight and total <= cell.width():
            x = cell.right() - total
        elif align & Qt.AlignmentFlag.AlignHCenter and total <= cell.width():
            x = cell.left() + (cell.width() - total) / 2
        else:
            x = cell.left()
        for text, styles, width in runs:
            room = cell.right() - x
            if room <= 4:
                break
            painter.setFont(self._run_font(styles))
            metrics = self._metrics(styles)
            extra = 2 * self.CODE_PAD if "code" in styles else 0.0
            if width > room + 0.5:  # doesn't fit (shouldn't happen once wrapped): elide it
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room - extra)
                width = metrics.horizontalAdvance(text) + extra
            if "code" in styles:
                height = metrics.height() + 2
                chip = QRectF(x, cell.center().y() - height / 2, width, height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.code_bg))
                painter.drawRoundedRect(chip, 3, 3)
            if "link" in styles:
                color = t.accent
            elif styles & {"strike", "image"}:
                color = t.muted
            elif "code" in styles:
                color = t.heading_sub
            else:
                color = t.text
            painter.setPen(QColor(color))
            painter.drawText(QRectF(x + extra / 2, cell.top(), width - extra + 1, cell.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += width

    def _table_cell_at(self, pos: QPointF) -> Optional[int]:
        """Document position of the cell content under pos in a painted grid."""
        for grid, xs, tops, blocks in self._table_hits:
            if not grid.contains(pos):
                continue
            row = next((r for r in range(len(blocks)) if tops[r] <= pos.y() < tops[r + 1]), len(blocks) - 1)
            column = next((c for c in range(len(xs) - 1) if xs[c] <= pos.x() < xs[c + 1]), len(xs) - 2)
            block = blocks[row]
            line = block.text()
            cells = split_cells(line)
            if column < len(cells):
                content, start = cells[column]
                index = start + len(content)
            else:
                index = len(line)
            return block.position() + _utf16_len(line[:index])
        return None

    def _paint_block(self, painter: QPainter, block: QTextBlock, rect: QRectF, info: BlockInfo, t: Theme) -> None:
        if info.kind == "quote":
            bottom = rect.bottom()
            following = block.next()
            if following.isValid() and isinstance(following.userData(), BlockInfo) \
                    and following.userData().kind == "quote":
                # Run the bar into the next quoted line so a multi-line quote has one bar.
                bottom = self.document().documentLayout().blockBoundingRect(following).translated(
                    self._offset()).top()
            painter.fillRect(QRectF(rect.left() - 14, rect.top(), 3, bottom - rect.top()), QColor(t.accent))
        elif info.kind == "hr" and not self.reveals(block):
            painter.setPen(QPen(QColor(t.border), 1))
            y = rect.center().y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        elif info.kind == "bullet" and info.marker and not self.reveals(block):
            chars = self._char_rect(block, *info.marker)
            if chars is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.text))
                painter.drawEllipse(chars.center(), 2.6, 2.6)
        elif info.kind == "task" and info.box and not self.reveal_all:  # raw markdown shows "[ ]" itself
            box = self._box_rect(block, info)
            if box is None:
                return
            if info.checked:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.accent))
                painter.drawEllipse(box)
                tick = QPainterPath()
                tick.moveTo(box.left() + box.width() * 0.24, box.top() + box.height() * 0.53)
                tick.lineTo(box.left() + box.width() * 0.43, box.top() + box.height() * 0.72)
                tick.lineTo(box.left() + box.width() * 0.77, box.top() + box.height() * 0.32)
                painter.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(tick)
            else:
                painter.setPen(QPen(QColor(t.line_number), 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(box.adjusted(0.7, 0.7, -0.7, -0.7))

    # ----- mouse (checkboxes) -------------------------------------------

    def _task_block_at(self, pos: QPointF) -> Optional[QTextBlock]:
        if self.reveal_all:
            return None
        block = self.cursorForPosition(pos.toPoint()).block()
        info = block.userData()
        if isinstance(info, BlockInfo) and info.kind == "task" and info.box:
            box = self._box_rect(block, info)
            if box is not None and box.adjusted(-3, -3, 3, 3).contains(pos):
                return block
        return None

    def toggle_checkbox(self, block: QTextBlock) -> None:
        info = block.userData()
        if not isinstance(info, BlockInfo) or not info.box:
            return
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position() + info.box[0] + 1)
        cursor.setPosition(block.position() + info.box[0] + 2, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(" " if info.checked else "x")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            block = self._task_block_at(event.position())
            if block is not None:
                self.toggle_checkbox(block)
                event.accept()
                return
            position = self._table_cell_at(event.position())
            if position is not None:
                cursor = self.textCursor()
                cursor.setPosition(position)
                self.setTextCursor(cursor)
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        over_box = self._task_block_at(event.position()) is not None
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over_box else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    # ----- formatting commands ------------------------------------------

    def wrap_selection(self, marker: str) -> None:
        """Wrap the selection in marker (or unwrap it if already wrapped)."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            selected = cursor.selectedText().replace(" ", "\n")
            start = cursor.selectionStart()
            n = len(marker)
            if len(selected) >= 2 * n and selected.startswith(marker) and selected.endswith(marker):
                inner = selected[n:-n]
                cursor.insertText(inner)
                cursor.setPosition(start)
            else:
                inner = selected
                cursor.insertText(f"{marker}{inner}{marker}")
                cursor.setPosition(start + n)
                start += n
            cursor.setPosition(start + _utf16_len(inner), QTextCursor.MoveMode.KeepAnchor)
        else:
            cursor.insertText(marker * 2)
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, len(marker))
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def insert_link(self) -> None:
        """Turn the selection into [selection]() with the cursor inside the parentheses."""
        cursor = self.textCursor()
        selected = cursor.selectedText().replace(" ", " ")
        cursor.beginEditBlock()
        cursor.insertText(f"[{selected}]()")
        offset = 1 if selected else 3  # inside () when there's text, inside [] otherwise
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, offset)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _selected_blocks(self):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(cursor.selectionEnd())
        if cursor.hasSelection() and last.position() == cursor.selectionEnd() and last != first:
            last = last.previous()  # selection ending at column 0 doesn't include that line
        block = first
        while block.isValid():
            yield block
            if block == last:
                break
            block = block.next()

    def _replace_block_text(self, cursor: QTextCursor, block, text: str) -> None:
        cursor.setPosition(block.position())
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)

    def toggle_task(self) -> None:
        """Cycle the current line(s): text -> "- [ ] text" -> "- [x] text" -> "- [ ] text"."""
        cursor = self.textCursor()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in list(self._selected_blocks()):
            line = block.text()
            task = _TASK_RE.match(line)
            if task:
                box = " " if task.group(2) in "xX" else "x"
                new = task.group(1) + f"[{box}]" + line[task.end():]
            else:
                bullet = _BULLET_RE.match(line)
                if bullet:
                    new = bullet.group(1) + "[ ] " + line[bullet.end():]
                else:
                    stripped = line.lstrip()
                    new = line[: len(line) - len(stripped)] + "- [ ] " + stripped
            self._replace_block_text(edit, block, new)
        edit.endEditBlock()
        if not cursor.hasSelection():
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self.setTextCursor(cursor)

    def indent_lines(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection() and not _LIST_LINE_RE.match(cursor.block().text()):
            cursor.insertText(INDENT)
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in list(self._selected_blocks()):
            edit.setPosition(block.position())
            edit.insertText(INDENT)
        edit.endEditBlock()

    def unindent_lines(self) -> None:
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in list(self._selected_blocks()):
            text = block.text()
            if text.startswith("\t"):
                remove = 1
            else:
                remove = min(len(text) - len(text.lstrip(" ")), len(INDENT))
            if remove:
                edit.setPosition(block.position())
                edit.setPosition(block.position() + remove, QTextCursor.MoveMode.KeepAnchor)
                edit.removeSelectedText()
        edit.endEditBlock()

    def _continue_block(self) -> bool:
        """Enter in a list item or quote starts the next one; on an empty one, ends it."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        line = cursor.block().text()
        m = _LIST_LINE_RE.match(line) or _QUOTE_LINE_RE.match(line)
        if not m or cursor.positionInBlock() < _utf16_len(line[: m.end()]):
            return False
        if not line[m.end():].strip() and cursor.atBlockEnd():
            cursor.beginEditBlock()
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        if m.re is _QUOTE_LINE_RE:
            prefix = m.group(1)
        else:
            number = m.group(3)
            symbol = m.group(2) if not number else f"{int(number) + 1}{m.group(4)}"
            prefix = m.group(1) + symbol + " " + ("[ ] " if m.group(6) else "")
        cursor.insertText("\n" + prefix)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return True

    # ----- table editing -------------------------------------------------

    def _place_cursor(self, block: QTextBlock, index: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(block.position() + _utf16_len(block.text()[:index]))
        self.setTextCursor(cursor)

    def _cursor_index(self) -> int:
        """Python index of the cursor within its line."""
        cursor = self.textCursor()
        line, target, count = cursor.block().text(), cursor.positionInBlock(), 0
        for i, ch in enumerate(line):
            if count >= target:
                return i
            count += 2 if ord(ch) >= 0x10000 else 1
        return len(line)

    def _append_table_row(self, after: QTextBlock, columns: int) -> None:
        cursor = QTextCursor(after)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertText("\n|" + "  |" * max(1, columns))
        self._place_cursor(after.next(), 2)

    def _table_tab(self, backwards: bool) -> bool:
        """Tab / Shift+Tab move between cells; Tab past the last cell adds a row."""
        block = self.textCursor().block()
        bounds = table_bounds(block)
        if bounds is None or block == bounds[0].next():
            return False
        first, last = bounds
        columns = len(split_cells(first.text()))
        cells = split_cells(block.text())
        index = self._cursor_index()
        current = max((i for i, (_, start) in enumerate(cells) if start <= index), default=0)
        target = current - 1 if backwards else current + 1

        def row_step(row: QTextBlock, step: int) -> Optional[QTextBlock]:
            row = row.next() if step > 0 else row.previous()
            if row.isValid() and row == first.next():  # skip the delimiter row
                row = row.next() if step > 0 else row.previous()
            if not row.isValid() or row.blockNumber() < first.blockNumber() or row.blockNumber() > last.blockNumber():
                return None
            return row

        if 0 <= target < len(cells):
            content, start = cells[target]
            self._place_cursor(block, start + len(content))
            return True
        row = row_step(block, -1 if backwards else 1)
        if row is None:
            if not backwards:
                self._append_table_row(block, columns)
            return True
        row_cells = split_cells(row.text())
        content, start = row_cells[-1 if backwards else 0] if row_cells else ("", len(row.text()))
        self._place_cursor(row, start + len(content))
        return True

    def _table_enter(self) -> bool:
        """Enter in a row adds a row below (a newline would split the row and break the
        table); on an empty last row, it leaves the table instead."""
        cursor = self.textCursor()
        block = cursor.block()
        bounds = table_bounds(block)
        if bounds is None or block == bounds[0].next() or cursor.hasSelection():
            return False
        if block == bounds[1] and block != bounds[0] and not any(c for c, _ in split_cells(block.text())):
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self.setTextCursor(cursor)
            return True
        self._append_table_row(block, len(split_cells(bounds[0].text())))
        return True

    # ----- slash menu ----------------------------------------------------

    def open_slash(self) -> None:
        """Open the menu for the "/" just before the cursor."""
        self._slash_start = self.textCursor().position() - 1
        self._update_slash()

    def close_slash(self) -> None:
        self._slash_start = None
        self.slash_popup.hide()

    def _slash_query(self) -> Optional[str]:
        """Text typed after the "/", or None if the cursor has left it (menu should close)."""
        start = self._slash_start
        cursor = self.textCursor()
        if start is None or cursor.hasSelection() or cursor.position() <= start:
            return None
        block = self.document().findBlock(start)
        if block != cursor.block():
            return None
        typed = block.text()[start - block.position(): cursor.position() - block.position()]
        if not typed.startswith("/"):
            return None
        return typed[1:]

    def _update_slash(self) -> None:
        if self._slash_start is None:
            return
        query = self._slash_query()
        if query is None or (query.endswith(" ") and not self.slash_popup.commands) or len(query) > 24:
            self.close_slash()  # cursor moved away, or a space after a query with no results
            return
        self.slash_popup.set_query(query)
        self._position_slash()
        self.slash_popup.show()
        self.slash_popup.raise_()

    def _position_slash(self) -> None:
        if self._slash_start is None:
            return
        rect = self.cursorRect()
        below = self.viewport().mapTo(self, rect.bottomLeft()) + QPoint(-8, 6)
        popup = self.slash_popup
        if below.y() + popup.height() > self.height():  # no room below: open upwards
            below.setY(self.viewport().mapTo(self, rect.topLeft()).y() - popup.height() - 6)
        below.setX(max(0, min(below.x(), self.width() - popup.width())))
        popup.move(below)

    def _apply_slash(self, command: SlashCommand) -> None:
        start = self._slash_start
        self.close_slash()
        if start is None:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)  # select "/query"
        cursor.removeSelectedText()
        cursor.insertText(command.insert)
        if command.insert.startswith("```"):
            cursor.movePosition(QTextCursor.MoveOperation.Up)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        elif command.insert.startswith("|"):
            cursor.movePosition(QTextCursor.MoveOperation.Up)
            cursor.movePosition(QTextCursor.MoveOperation.Up)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, 2)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len("Column 1"))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.setFocus()

    def _slash_key(self, event: QKeyEvent) -> bool:
        """Keys the open menu handles; everything else is typed into the note."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_slash()
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.slash_popup.move_selection(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            command = self.slash_popup.current()
            if command is None:  # "No results": close and let the key act normally
                self.close_slash()
                return False
            self._apply_slash(command)
            return True
        return False

    def focusOutEvent(self, event) -> None:
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.close_slash()
        super().focusOutEvent(event)

    # ----- keys ----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.slash_popup.isVisible() and self._slash_key(event):
            return
        mods = event.modifiers()
        key = event.key()
        # ControlModifier is Cmd on macOS and Ctrl elsewhere.
        command = bool(mods & Qt.KeyboardModifier.ControlModifier) and not (
            mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
        )
        if command:
            if key == Qt.Key.Key_B:
                return self.wrap_selection("**")
            if key == Qt.Key.Key_I:
                return self.wrap_selection("*")
            if key == Qt.Key.Key_K:
                return self.insert_link()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                return self.toggle_task()
        if key == Qt.Key.Key_Tab and not (mods & Qt.KeyboardModifier.ControlModifier):
            if not self._table_tab(backwards=False):
                self.indent_lines()
            return
        if key == Qt.Key.Key_Backtab:
            if not self._table_tab(backwards=True):
                self.unindent_lines()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (mods & Qt.KeyboardModifier.ControlModifier):
            plain = not mods & ~(Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.ShiftModifier)
            if plain and not mods & Qt.KeyboardModifier.ShiftModifier and (self._table_enter() or self._continue_block()):
                return
            # Insert the line ourselves: on an empty line whose block format has our line
            # spacing, QTextEdit's Enter only resets the format and inserts nothing. This
            # also keeps Shift+Enter from inserting U+2028 into the markdown.
            cursor = self.textCursor()
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            cursor.insertBlock()
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            return
        if event.text() == "/" and not self.textCursor().hasSelection():
            before = self.textCursor().block().text()[: self.textCursor().positionInBlock()]
            super().keyPressEvent(event)
            if not before.strip():
                self.open_slash()
            return
        super().keyPressEvent(event)
