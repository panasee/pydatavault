"""Presentation helpers for the PyDataVault desktop UI."""

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyleFactory


ACCENT = "#2563eb"
DANGER = "#dc2626"
SUCCESS = "#059669"
TEXT = "#1f2937"
MUTED = "#64748b"


APP_STYLESHEET = f"""
QMainWindow {{
    background: #f3f6fa;
}}

QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", "Microsoft YaHei UI", "Arial";
    font-size: 10pt;
}}

QMenuBar {{
    background: #ffffff;
    border-bottom: 1px solid #d7dee8;
    padding: 2px 8px;
}}

QMenuBar::item {{
    border-radius: 6px;
    padding: 6px 10px;
}}

QMenuBar::item:selected {{
    background: #eef4ff;
    color: #174ea6;
}}

QMenu {{
    background: #ffffff;
    border: 1px solid #d7dee8;
    border-radius: 8px;
    padding: 6px;
}}

QMenu::item {{
    border-radius: 6px;
    padding: 7px 28px 7px 24px;
}}

QMenu::item:selected {{
    background: #eef4ff;
    color: #174ea6;
}}

QStatusBar {{
    background: #ffffff;
    border-top: 1px solid #d7dee8;
    color: {MUTED};
    padding: 4px 10px;
}}

QTabWidget::pane {{
    border: 0;
    background: #f3f6fa;
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    color: {MUTED};
    margin: 7px 4px 8px 4px;
    min-height: 32px;
    padding: 7px 16px;
}}

QTabBar::tab:selected {{
    background: #ffffff;
    border: 1px solid #d7dee8;
    color: {TEXT};
}}

QTabBar::tab:hover:!selected {{
    background: #eaf0f8;
    color: {TEXT};
}}

QSplitter::handle {{
    background: #e2e8f0;
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QWidget#sidePanel,
QWidget#contentPanel,
QWidget#gridPanel {{
    background: #ffffff;
    border: 1px solid #d7dee8;
    border-radius: 10px;
}}

QLabel[heading="true"] {{
    color: #0f172a;
    font-size: 12pt;
    font-weight: 600;
}}

QLabel[subtle="true"] {{
    color: {MUTED};
}}

QListWidget,
QTableWidget,
QTextEdit,
QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {{
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 7px;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
}}

QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {{
    min-height: 28px;
    padding: 4px 8px;
}}

QTextEdit {{
    padding: 7px;
}}

QLineEdit:focus,
QTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QListWidget {{
    padding: 5px;
}}

QListWidget::item {{
    border-radius: 7px;
    margin: 1px;
    padding: 8px 10px;
}}

QListWidget::item:selected {{
    background: #dbeafe;
    color: #0f172a;
}}

QListWidget::item:hover:!selected {{
    background: #f1f5f9;
}}

QTableWidget {{
    alternate-background-color: #f8fafc;
    gridline-color: #e2e8f0;
}}

QTableWidget::item {{
    padding: 7px;
}}

QTableWidget::item:selected {{
    background: #dbeafe;
    color: #0f172a;
}}

QHeaderView::section {{
    background: #f8fafc;
    border: 0;
    border-bottom: 1px solid #d7dee8;
    color: {MUTED};
    font-weight: 600;
    padding: 8px 9px;
}}

QPushButton {{
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 7px;
    color: {TEXT};
    min-height: 30px;
    padding: 6px 12px;
}}

QPushButton:hover {{
    background: #f8fafc;
    border-color: #b6c2d2;
}}

QPushButton:pressed {{
    background: #eaf0f8;
}}

QPushButton[role="primary"] {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}

QPushButton[role="primary"]:hover {{
    background: #1d4ed8;
    border-color: #1d4ed8;
}}

QPushButton[role="danger"] {{
    color: {DANGER};
    border-color: #fecaca;
}}

QPushButton[role="danger"]:hover {{
    background: #fef2f2;
    border-color: #fca5a5;
}}

QPushButton[role="utility"] {{
    color: #174ea6;
    border-color: #bfdbfe;
}}

QPushButton[role="utility"]:hover {{
    background: #eff6ff;
}}

QDialog {{
    background: #f3f6fa;
}}

QDialog QLabel {{
    color: {TEXT};
}}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply the application-wide visual theme."""
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(app_icon())

    palette = app.palette()
    palette.setColor(QPalette.Window, QColor("#f3f6fa"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor("#dbeafe"))
    palette.setColor(QPalette.HighlightedText, QColor("#0f172a"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Return a generated app icon so the app has no external asset dependency."""
    icon_obj = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        outer = QRectF(1, 1, size - 2, size - 2)
        gradient = QLinearGradient(outer.topLeft(), outer.bottomRight())
        gradient.setColorAt(0, QColor("#60a5fa"))
        gradient.setColorAt(1, QColor("#1d4ed8"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(outer, size * 0.22, size * 0.22)

        margin = size * 0.24
        grid = QRectF(margin, margin, size - margin * 2, size - margin * 2)
        painter.setPen(QPen(QColor("#ffffff"), max(1, size // 16)))
        painter.drawRoundedRect(grid, size * 0.05, size * 0.05)
        for i in (1, 2):
            x = grid.left() + grid.width() * i / 3
            y = grid.top() + grid.height() * i / 3
            painter.drawLine(QPointF(x, grid.top()), QPointF(x, grid.bottom()))
            painter.drawLine(QPointF(grid.left(), y), QPointF(grid.right(), y))

        painter.setBrush(QColor("#bbf7d0"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(grid.center().x(), grid.center().y()), size * 0.08, size * 0.08)
        painter.end()

        icon_obj.addPixmap(pixmap)
    return icon_obj


def symbol_icon(name: str, color: str = ACCENT) -> QIcon:
    """Create a small Fluent-style line icon for common actions."""
    icon_obj = QIcon()
    for size in (16, 20, 24, 32):
        icon_obj.addPixmap(_symbol_pixmap(name, QColor(color), size))
    return icon_obj


def decorate_button(button, role: str = "neutral", icon_name: str | None = None) -> None:
    """Attach visual metadata to a button without changing its behavior."""
    button.setProperty("role", role)
    button.setCursor(Qt.PointingHandCursor)
    if icon_name:
        color = "#ffffff" if role == "primary" else DANGER if role == "danger" else ACCENT
        button.setIcon(symbol_icon(icon_name, color))
        button.setIconSize(QSize(16, 16))


def decorate_heading(label) -> None:
    """Mark a label as a section heading."""
    label.setProperty("heading", True)


def decorate_panel(widget, name: str = "contentPanel") -> None:
    """Mark a widget as a styled panel."""
    widget.setObjectName(name)


def decorate_table(table) -> None:
    """Apply presentation-only table defaults."""
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)


def decorate_list(list_widget) -> None:
    """Apply presentation-only list defaults."""
    list_widget.setUniformItemSizes(False)


def decorate_status_item(item, value: str | None) -> None:
    """Tint a table item by status text without changing its value."""
    status = (value or "").strip().lower()
    colors = {
        "planned": ("#eff6ff", "#1d4ed8"),
        "fabricated": ("#ecfdf5", "#047857"),
        "measured": ("#f0fdf4", "#15803d"),
        "retired": ("#f8fafc", "#64748b"),
        "available": ("#ecfdf5", "#047857"),
        "used": ("#f8fafc", "#64748b"),
        "reserved": ("#fffbeb", "#b45309"),
    }
    background, foreground = colors.get(status, ("#ffffff", TEXT))
    item.setBackground(QBrush(QColor(background)))
    item.setForeground(QBrush(QColor(foreground)))


def _symbol_pixmap(name: str, color: QColor, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = QPen(color, max(1.5, size / 12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    pad = size * 0.22
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)

    if name == "plus":
        painter.drawLine(QPointF(size / 2, pad), QPointF(size / 2, size - pad))
        painter.drawLine(QPointF(pad, size / 2), QPointF(size - pad, size / 2))
    elif name == "edit":
        painter.drawLine(QPointF(size * 0.35, size * 0.7), QPointF(size * 0.72, size * 0.33))
        painter.drawLine(QPointF(size * 0.64, size * 0.25), QPointF(size * 0.78, size * 0.39))
        painter.drawLine(QPointF(size * 0.29, size * 0.76), QPointF(size * 0.43, size * 0.72))
    elif name == "delete":
        painter.drawLine(QPointF(size * 0.3, size * 0.38), QPointF(size * 0.7, size * 0.38))
        painter.drawRect(QRectF(size * 0.34, size * 0.42, size * 0.32, size * 0.36))
        painter.drawLine(QPointF(size * 0.42, size * 0.3), QPointF(size * 0.58, size * 0.3))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(size * 0.18, size * 0.36)
        path.lineTo(size * 0.42, size * 0.36)
        path.lineTo(size * 0.5, size * 0.46)
        path.lineTo(size * 0.82, size * 0.46)
        path.lineTo(size * 0.82, size * 0.76)
        path.lineTo(size * 0.18, size * 0.76)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "refresh":
        painter.drawArc(rect, 35 * 16, 285 * 16)
        painter.drawLine(QPointF(size * 0.72, size * 0.25), QPointF(size * 0.81, size * 0.43))
        painter.drawLine(QPointF(size * 0.72, size * 0.25), QPointF(size * 0.55, size * 0.3))
    elif name == "photo":
        painter.drawRoundedRect(rect, size * 0.06, size * 0.06)
        painter.drawEllipse(QPointF(size * 0.65, size * 0.38), size * 0.06, size * 0.06)
        painter.drawLine(QPointF(size * 0.3, size * 0.7), QPointF(size * 0.47, size * 0.52))
        painter.drawLine(QPointF(size * 0.47, size * 0.52), QPointF(size * 0.72, size * 0.7))
    elif name == "transform":
        painter.drawLine(QPointF(size * 0.24, size * 0.35), QPointF(size * 0.76, size * 0.35))
        painter.drawLine(QPointF(size * 0.62, size * 0.25), QPointF(size * 0.76, size * 0.35))
        painter.drawLine(QPointF(size * 0.62, size * 0.45), QPointF(size * 0.76, size * 0.35))
        painter.drawLine(QPointF(size * 0.76, size * 0.65), QPointF(size * 0.24, size * 0.65))
        painter.drawLine(QPointF(size * 0.38, size * 0.55), QPointF(size * 0.24, size * 0.65))
        painter.drawLine(QPointF(size * 0.38, size * 0.75), QPointF(size * 0.24, size * 0.65))
    elif name == "wafer":
        painter.drawEllipse(rect)
        painter.drawLine(QPointF(size * 0.5, size * 0.25), QPointF(size * 0.5, size * 0.75))
        painter.drawLine(QPointF(size * 0.25, size * 0.5), QPointF(size * 0.75, size * 0.5))
    elif name == "projects":
        painter.drawRoundedRect(QRectF(size * 0.22, size * 0.28, size * 0.56, size * 0.18), 2, 2)
        painter.drawRoundedRect(QRectF(size * 0.22, size * 0.54, size * 0.56, size * 0.18), 2, 2)
    elif name == "database":
        painter.drawEllipse(QRectF(size * 0.25, size * 0.22, size * 0.5, size * 0.2))
        painter.drawLine(QPointF(size * 0.25, size * 0.32), QPointF(size * 0.25, size * 0.68))
        painter.drawLine(QPointF(size * 0.75, size * 0.32), QPointF(size * 0.75, size * 0.68))
        painter.drawEllipse(QRectF(size * 0.25, size * 0.58, size * 0.5, size * 0.2))
    elif name == "info":
        painter.drawEllipse(rect)
        painter.drawLine(QPointF(size * 0.5, size * 0.47), QPointF(size * 0.5, size * 0.68))
        painter.drawPoint(QPointF(size * 0.5, size * 0.34))
    else:
        painter.drawRoundedRect(rect, size * 0.05, size * 0.05)

    painter.end()
    return pixmap
