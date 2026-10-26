"""Folder and Note models plus the text rules shared by the data and UI layers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

DEFAULT_NOTE_TITLE = "Untitled Note"
MAX_TITLE_LENGTH = 200
MAX_FOLDER_NAME_LENGTH = 60

# Folder names: letters, digits, spaces, "_" and "-".
FOLDER_NAME_PATTERN = r"^[A-Za-z0-9 _-]+$"
FOLDER_NAME_RE = re.compile(FOLDER_NAME_PATTERN)

# Note titles: letters/digits (any script) plus common punctuation.
TITLE_ALLOWED_CHARS = r"\w .,!?'\"()\[\]&:;#@%+=*/\-"
_TITLE_DISALLOWED_RE = re.compile(r"[^" + TITLE_ALLOWED_CHARS + r"]")

# Subtle color tags for folders (name -> hex, None means no tag).
FOLDER_COLORS = {
    "None": None,
    "Red": "#E57373",
    "Orange": "#F0A35E",
    "Yellow": "#E3C55B",
    "Green": "#6FBF8E",
    "Blue": "#6A9FE8",
    "Purple": "#A88BE0",
    "Gray": "#A1A1AA",
}

_WORD_RE = re.compile(r"\w+(?:['’]\w+)*")
_TASK_MARKER_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[[ xX]\]", re.M)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_valid_folder_name(name: str) -> bool:
    name = name.strip()
    return bool(name) and len(name) <= MAX_FOLDER_NAME_LENGTH and bool(FOLDER_NAME_RE.match(name))


def sanitize_title(title: str) -> str:
    cleaned = _TITLE_DISALLOWED_RE.sub("", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:MAX_TITLE_LENGTH]
    return cleaned or DEFAULT_NOTE_TITLE


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(_TASK_MARKER_RE.sub(r"\1", text or "")))


def preview_snippet(markdown_text: str, length: int = 50) -> str:
    """A plain-text preview of a note: markdown syntax stripped, whitespace collapsed."""
    text = markdown_text or ""
    text = re.sub(r"^\s*(```|~~~).*$", " ", text, flags=re.M)
    text = re.sub(r"^\s*\|?(\s*:?-+:?\s*\|)+\s*(:?-+:?\s*)?$", " ", text, flags=re.M)  # table rule rows
    text = re.sub(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", " ", text, flags=re.M)  # dividers
    text = re.sub(r"^\s*\|.*$", lambda m: m.group(0).replace("|", " "), text, flags=re.M)  # table cell borders
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(#{1,6}\s+|>\s?|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)", "", text, flags=re.M)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > length:
        return text[:length].rstrip() + "…"
    return text


@dataclass
class Folder:
    id: int
    name: str
    color_tag: Optional[str]
    created_date: str
    is_archived: bool = False
    position: int = 0  # manual (drag-and-drop) order in the sidebar


@dataclass
class Note:
    id: int
    title: str
    markdown_content: str
    folder_id: Optional[int]
    created_date: str
    modified_date: str
    word_count: int = 0
    is_pinned: bool = False
    position: int = 0  # manual order within its folder (or among standalone notes)
