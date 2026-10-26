"""Flat line icons, drawn with QPainter paths (no emoji, no image files).

Shapes are defined on a 16×16 grid and scaled to the target rect, stroked with
round caps and joins so they match the thin style of the rest of the UI.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

ICON_NAMES = ("chevron-right", "chevron-down", "chevron-left", "folder", "folder-plus", "note", "pin", "plus", "close",
              "compose", "more", "search", "sidebar", "trash", "code")


def _path(name: str) -> QPainterPath:
    p = QPainterPath()
    if name == "chevron-right":
        p.moveTo(6.5, 4)
        p.lineTo(10.5, 8)
        p.lineTo(6.5, 12)
    elif name == "chevron-left":
        p.moveTo(9.5, 4)
        p.lineTo(5.5, 8)
        p.lineTo(9.5, 12)
    elif name == "chevron-down":
        p.moveTo(4, 6.5)
        p.lineTo(8, 10.5)
        p.lineTo(12, 6.5)
    elif name in ("folder", "folder-plus"):
        p.moveTo(1.75, 12)
        p.lineTo(1.75, 4)
        p.quadTo(1.75, 2.75, 3, 2.75)
        p.lineTo(5.9, 2.75)
        p.lineTo(7.4, 4.5)
        p.lineTo(13, 4.5)
        p.quadTo(14.25, 4.5, 14.25, 5.75)
        p.lineTo(14.25, 12)
        p.quadTo(14.25, 13.25, 13, 13.25)
        p.lineTo(3, 13.25)
        p.quadTo(1.75, 13.25, 1.75, 12)
        p.closeSubpath()
        if name == "folder-plus":
            p.moveTo(8, 6.9)
            p.lineTo(8, 11.1)
            p.moveTo(5.9, 9)
            p.lineTo(10.1, 9)
    elif name == "note":
        p.moveTo(3.25, 2.75)
        p.quadTo(3.25, 1.75, 4.25, 1.75)
        p.lineTo(9.25, 1.75)
        p.lineTo(12.75, 5.25)
        p.lineTo(12.75, 13.25)
        p.quadTo(12.75, 14.25, 11.75, 14.25)
        p.lineTo(4.25, 14.25)
        p.quadTo(3.25, 14.25, 3.25, 13.25)
        p.closeSubpath()
        p.moveTo(9.25, 1.75)
        p.lineTo(9.25, 5.25)
        p.lineTo(12.75, 5.25)
        p.moveTo(5.75, 8.5)
        p.lineTo(10.25, 8.5)
        p.moveTo(5.75, 11)
        p.lineTo(9, 11)
    elif name == "pin":
        p.moveTo(5.5, 2)
        p.lineTo(10.5, 2)
        p.moveTo(6.5, 2)
        p.lineTo(6.5, 6.5)
        p.lineTo(4.25, 9.5)
        p.lineTo(11.75, 9.5)
        p.lineTo(9.5, 6.5)
        p.lineTo(9.5, 2)
        p.moveTo(8, 9.5)
        p.lineTo(8, 14)
    elif name == "close":
        p.moveTo(4.75, 4.75)
        p.lineTo(11.25, 11.25)
        p.moveTo(11.25, 4.75)
        p.lineTo(4.75, 11.25)
    elif name == "plus":
        p.moveTo(8, 3.25)
        p.lineTo(8, 12.75)
        p.moveTo(3.25, 8)
        p.lineTo(12.75, 8)
    elif name == "compose":
        p.moveTo(8.5, 2.25)
        p.lineTo(4, 2.25)
        p.quadTo(2.25, 2.25, 2.25, 4)
        p.lineTo(2.25, 12)
        p.quadTo(2.25, 13.75, 4, 13.75)
        p.lineTo(12, 13.75)
        p.quadTo(13.75, 13.75, 13.75, 12)
        p.lineTo(13.75, 7.5)
        p.moveTo(6.75, 9.25)
        p.lineTo(7.25, 7.25)
        p.lineTo(12.5, 2)
        p.lineTo(14, 3.5)
        p.lineTo(8.75, 8.75)
        p.closeSubpath()
    elif name == "more":
        for x in (3.25, 8, 12.75):  # round caps turn each tiny circle into a dot
            p.addEllipse(QPointF(x, 8), 0.45, 0.45)
    elif name == "search":
        p.addEllipse(QPointF(7, 7), 4.5, 4.5)
        p.moveTo(10.4, 10.4)
        p.lineTo(13.75, 13.75)
    elif name == "sidebar":
        p.addRoundedRect(QRectF(1.75, 2.75, 12.5, 10.5), 2, 2)
        p.moveTo(6.25, 2.75)
        p.lineTo(6.25, 13.25)
    elif name == "trash":
        p.moveTo(2.75, 4.25)
        p.lineTo(13.25, 4.25)
        p.moveTo(6.25, 4.25)
        p.lineTo(6.25, 2.5)
        p.lineTo(9.75, 2.5)
        p.lineTo(9.75, 4.25)
        p.moveTo(4.25, 4.25)
        p.lineTo(5, 13.75)
        p.lineTo(11, 13.75)
        p.lineTo(11.75, 4.25)
    elif name == "code":
        p.moveTo(5, 4.5)
        p.lineTo(1.75, 8)
        p.lineTo(5, 11.5)
        p.moveTo(11, 4.5)
        p.lineTo(14.25, 8)
        p.lineTo(11, 11.5)
        p.moveTo(9.25, 3)
        p.lineTo(6.75, 13)
    else:
        raise ValueError(f"unknown icon {name!r}")
    return p


def paint_icon(painter: QPainter, name: str, rect: QRectF, color, stroke: float = 1.3) -> None:
    """Stroke icon `name` into rect (square, any size) in color."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center() - QPointF(side / 2, side / 2))
    painter.scale(side / 16, side / 16)
    pen = QPen(QColor(color), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_path(name))
    painter.restore()


def make_icon(name: str, color: str, hover_color: Optional[str] = None, size: int = 16) -> QIcon:
    """QIcon for buttons: `color` normally, `hover_color` when hovered (Active mode)."""
    icon = QIcon()
    for mode, tint in ((QIcon.Mode.Normal, color), (QIcon.Mode.Active, hover_color or color)):
        for scale in (1, 2):  # crisp on Retina
            pixmap = QPixmap(size * scale, size * scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            paint_icon(painter, name, QRectF(0, 0, size, size), tint)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def set_button_icon(button, name: str, color: str, hover_color: str, size: int = 16) -> None:
    button.setText("")
    button.setIcon(make_icon(name, color, hover_color, size))
    button.setIconSize(QSize(size, size))
