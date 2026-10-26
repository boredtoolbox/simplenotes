"""macOS window chrome that Qt has no API for: hiding the title text and moving the
traffic lights down so they line up with a taller toolbar (as Electron's
trafficLightPosition does). Both message the Objective-C runtime through ctypes, so
there is no extra dependency. Everything here is cosmetic and fails silently."""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

TITLE_HIDDEN = 1  # NSWindowTitleHidden
BUTTON_SPACING = 23  # AppKit's own distance between the traffic lights


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Runtime:
    def __init__(self):
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.sel = objc.sel_registerName
        ptr = ctypes.c_void_p
        # One prototype per signature; on arm64 objc_msgSend also returns structs.
        self.get = ctypes.CFUNCTYPE(ptr, ptr, ptr)(("objc_msgSend", objc))
        self.get_long = ctypes.CFUNCTYPE(ptr, ptr, ptr, ctypes.c_long)(("objc_msgSend", objc))
        self.put_long = ctypes.CFUNCTYPE(None, ptr, ptr, ctypes.c_long)(("objc_msgSend", objc))
        self.get_rect = ctypes.CFUNCTYPE(_Rect, ptr, ptr)(("objc_msgSend", objc))
        self.put_rect = ctypes.CFUNCTYPE(None, ptr, ptr, _Rect)(("objc_msgSend", objc))
        self.put_point = ctypes.CFUNCTYPE(None, ptr, ptr, _Point)(("objc_msgSend", objc))

    def ns_window(self, widget) -> Optional[int]:
        return self.get(int(widget.winId()), self.sel(b"window"))  # winId() is the NSView


_runtime: Optional[_Runtime] = None


def _rt() -> Optional[_Runtime]:
    global _runtime
    if _runtime is None:
        try:
            _runtime = _Runtime()
        except (OSError, AttributeError, TypeError):
            return None
    return _runtime


def hide_title(widget) -> None:
    """Stop macOS drawing the window title; it still names the window in the Window menu."""
    rt = _rt()
    window = rt.ns_window(widget) if rt else None
    if window:
        rt.put_long(window, rt.sel(b"setTitleVisibility:"), TITLE_HIDDEN)


def place_traffic_lights(widget, center_y: float, left: float) -> None:
    """Center the traffic lights center_y points below the window's top edge, the close
    button's left edge at `left`. AppKit puts them back on every relayout (resize,
    full screen), so call this again after each one."""
    rt = _rt()
    window = rt.ns_window(widget) if rt else None
    if not window:
        return
    buttons = [rt.get_long(window, rt.sel(b"standardWindowButton:"), i) for i in range(3)]
    if not all(buttons):
        return
    container = rt.get(rt.get(buttons[0], rt.sel(b"superview")), rt.sel(b"superview"))
    if not container:
        return
    frame = rt.get_rect(container, rt.sel(b"frame"))
    window_height = rt.get_rect(window, rt.sel(b"frame")).h
    height = 2 * center_y  # the title bar area grows so the buttons can sit lower
    rt.put_rect(container, rt.sel(b"setFrame:"), _Rect(frame.x, window_height - height, frame.w, height))
    for i, button in enumerate(buttons):
        size = rt.get_rect(button, rt.sel(b"frame"))
        rt.put_point(button, rt.sel(b"setFrameOrigin:"), _Point(left + i * BUTTON_SPACING, (height - size.h) / 2))
