import json
import math
import shutil
from pathlib import Path
from typing import Optional, Dict, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QTextEdit, QMessageBox, QFileDialog,
    QHeaderView, QComboBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QRect, QPoint, QPointF, QRectF
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QFont, QPixmap, QPolygonF
)

from . import database as db
from . import config
from . import coord_utils


class WaferGridView(QWidget):
    """Custom widget for rendering and interacting with the wafer grid."""

    cell_clicked = Signal(int, int)  # row, col

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = 0
        self.cols = 0
        self.flake_counts = {}  # {(row, col): count}
        self.selected_cell = None
        self.setMinimumSize(300, 300)

    def set_grid(self, rows: int, cols: int, flake_counts: Dict):
        """Update grid dimensions and flake counts."""
        self.rows = rows
        self.cols = cols
        self.flake_counts = flake_counts
        self.selected_cell = None
        self.update()

    def set_selected_cell(self, row: int, col: int):
        """Highlight a specific cell."""
        self.selected_cell = (row, col)
        self.update()

    def mousePressEvent(self, event):
        """Handle cell clicks."""
        if self.rows == 0 or self.cols == 0:
            return

        cell_size = self._get_cell_size()
        label_width = 40
        label_height = 30

        x = event.pos().x() - label_width
        y = event.pos().y() - label_height

        if x < 0 or y < 0:
            return

        col = x // cell_size
        row = y // cell_size

        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.cell_clicked.emit(row, col)
            self.set_selected_cell(row, col)

    def paintEvent(self, event):
        """Paint the wafer grid."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(240, 240, 240))

        if self.rows == 0 or self.cols == 0:
            painter.drawText(self.rect(), Qt.AlignCenter, "No box selected")
            return

        cell_size = self._get_cell_size()
        label_width = 40
        label_height = 30

        # Draw column labels (1, 2, 3, ...)
        for col in range(self.cols):
            x = label_width + col * cell_size
            painter.drawText(
                QRect(x, 0, cell_size, label_height),
                Qt.AlignCenter,
                str(col + 1)
            )

        # Draw row labels (A, B, C, ...)
        for row in range(self.rows):
            y = label_height + row * cell_size
            label = chr(ord('A') + row)
            painter.drawText(
                QRect(0, y, label_width, cell_size),
                Qt.AlignCenter,
                label
            )

        # Draw grid cells
        for row in range(self.rows):
            for col in range(self.cols):
                x = label_width + col * cell_size
                y = label_height + row * cell_size
                rect = QRect(x, y, cell_size, cell_size)

                # Determine cell color
                count = self.flake_counts.get((row, col), 0)
                if count == 0:
                    brush = QBrush(QColor(220, 220, 220))
                elif count == 1:
                    brush = QBrush(QColor(100, 200, 100))
                else:
                    brush = QBrush(QColor(100, 150, 200))

                painter.fillRect(rect, brush)

                # Draw border
                is_selected = self.selected_cell == (row, col)
                pen_color = QColor(0, 0, 0) if is_selected else QColor(100, 100, 100)
                pen_width = 3 if is_selected else 1
                painter.setPen(QPen(pen_color, pen_width))
                painter.drawRect(rect)

                # Draw count
                if count > 0:
                    font = QFont()
                    font.setPointSize(12)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.setPen(QPen(QColor(0, 0, 0)))
                    painter.drawText(rect, Qt.AlignCenter, str(count))

    def _get_cell_size(self) -> int:
        """Calculate cell size based on available space."""
        label_width = 40
        label_height = 30
        available_width = self.width() - label_width
        available_height = self.height() - label_height

        if self.rows == 0 or self.cols == 0:
            return 50

        cell_w = max(30, available_width // self.cols)
        cell_h = max(30, available_height // self.rows)
        return min(cell_w, cell_h)

    def resizeEvent(self, event):
        """Redraw on resize."""
        super().resizeEvent(event)
        self.update()


class AddFlakeDialog(QDialog):
    """Dialog for adding a new flake to a wafer."""

    def __init__(self, wafer_id: str, parent=None):
        super().__init__(parent)
        self.wafer_id = wafer_id
        self.photo_path = None
        self.setWindowTitle("Add Flake")
        self.setGeometry(100, 100, 500, 400)

        layout = QVBoxLayout()

        # Flake ID
        layout.addWidget(QLabel("Flake ID:"))
        self.flake_id_input = QLineEdit()
        layout.addWidget(self.flake_id_input)

        # Material
        layout.addWidget(QLabel("Material:"))
        self.material_input = QLineEdit()
        layout.addWidget(self.material_input)

        # Thickness
        layout.addWidget(QLabel("Thickness:"))
        self.thickness_input = QLineEdit()
        layout.addWidget(self.thickness_input)

        # Magnification
        layout.addWidget(QLabel("Magnification:"))
        self.magnification_input = QLineEdit()
        layout.addWidget(self.magnification_input)

        # Coordinates
        coord_layout = QHBoxLayout()
        coord_layout.addWidget(QLabel("Coord X:"))
        self.coord_x = QDoubleSpinBox()
        self.coord_x.setRange(-10000, 10000)
        self.coord_x.setSingleStep(0.1)
        coord_layout.addWidget(self.coord_x)

        coord_layout.addWidget(QLabel("Coord Y:"))
        self.coord_y = QDoubleSpinBox()
        self.coord_y.setRange(-10000, 10000)
        self.coord_y.setSingleStep(0.1)
        coord_layout.addWidget(self.coord_y)
        layout.addLayout(coord_layout)

        # Photo
        layout.addWidget(QLabel("Photo:"))
        photo_layout = QHBoxLayout()
        self.photo_label = QLabel("No photo selected")
        photo_layout.addWidget(self.photo_label)
        photo_btn = QPushButton("Browse...")
        photo_btn.clicked.connect(self.select_photo)
        photo_layout.addWidget(photo_btn)
        layout.addLayout(photo_layout)

        # Notes
        layout.addWidget(QLabel("Notes:"))
        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(100)
        layout.addWidget(self.notes_input)

        # Buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Create")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def select_photo(self):
        """Open file dialog to select photo."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Photo", "", "Image Files (*.png *.jpg *.jpeg *.tiff)"
        )
        if file_path:
            self.photo_path = file_path
            self.photo_label.setText(Path(file_path).name)

    def get_data(self) -> dict:
        """Return entered flake data."""
        return {
            'flake_id': self.flake_id_input.text().strip(),
            'material': self.material_input.text().strip(),
            'thickness': self.thickness_input.text().strip(),
            'magnification': self.magnification_input.text().strip(),
            'coord_x': self.coord_x.value(),
            'coord_y': self.coord_y.value(),
            'notes': self.notes_input.toPlainText().strip(),
            'photo_path': self.photo_path
        }


class RefPointSlot(QWidget):
    """One of the three reference-point slots shown inside RefPointsDialog."""

    def __init__(self, index: int, data: dict | None, parent=None):
        super().__init__(parent)
        self._photo_path: str = data.get('photo_path', '') if data else ''
        self._index = index

        outer = QVBoxLayout()
        outer.setAlignment(Qt.AlignTop)

        outer.addWidget(QLabel(f"Ref {index + 1}"))

        # Photo thumbnail
        self.thumb = QLabel()
        self.thumb.setFixedSize(120, 90)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("border: 1px solid #aaa; background: #f5f5f5;")
        self._update_thumb()
        outer.addWidget(self.thumb)

        photo_btn = QPushButton("Set Photo…")
        photo_btn.clicked.connect(self._pick_photo)
        outer.addWidget(photo_btn)

        clear_btn = QPushButton("Clear Slot")
        clear_btn.clicked.connect(self._clear)
        outer.addWidget(clear_btn)

        # Coordinates
        coord_grid = QHBoxLayout()
        coord_grid.addWidget(QLabel("X:"))
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-1e9, 1e9)
        self.x_spin.setDecimals(4)
        self.x_spin.setSingleStep(1.0)
        self.x_spin.setValue(data['x'] if data else 0.0)
        coord_grid.addWidget(self.x_spin)
        outer.addLayout(coord_grid)

        coord_grid2 = QHBoxLayout()
        coord_grid2.addWidget(QLabel("Y:"))
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-1e9, 1e9)
        self.y_spin.setDecimals(4)
        self.y_spin.setSingleStep(1.0)
        self.y_spin.setValue(data['y'] if data else 0.0)
        coord_grid2.addWidget(self.y_spin)
        outer.addLayout(coord_grid2)

        self.setLayout(outer)

    # ── internal helpers ────────────────────────────────────────────────

    def _update_thumb(self):
        if self._photo_path and Path(self._photo_path).exists():
            px = QPixmap(self._photo_path).scaled(
                120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.thumb.setPixmap(px)
        else:
            self.thumb.setText("No photo")
            self.thumb.setPixmap(QPixmap())

    def _pick_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Reference Photo", "",
            "Images (*.png *.jpg *.jpeg *.tiff *.bmp)")
        if path:
            self._photo_path = path
            self._update_thumb()

    def _clear(self):
        self._photo_path = ''
        self.x_spin.setValue(0.0)
        self.y_spin.setValue(0.0)
        self._update_thumb()

    # ── public API ──────────────────────────────────────────────────────

    def is_set(self) -> bool:
        """Return True if this slot has a photo assigned."""
        return bool(self._photo_path)

    def to_dict(self, dest_dir: Path) -> dict | None:
        """Return serialisable dict, copying photo to dest_dir if needed.

        Returns None if the slot has no photo (slot is empty/unused).
        """
        if not self._photo_path:
            return None
        src = Path(self._photo_path)
        dest = dest_dir / src.name
        if src != dest:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))
        return {
            'x': self.x_spin.value(),
            'y': self.y_spin.value(),
            'photo_path': str(dest),
        }


class RefPointsDialog(QDialog):
    """Dialog for editing all three reference points of a wafer at once.

    Each slot shows a photo thumbnail, X/Y stage coordinates, and controls
    to set or clear the photo. On accept, only slots with a photo are saved.
    """

    def __init__(self, wafer_id: int, ref_points: list[dict], parent=None):
        super().__init__(parent)
        self.wafer_id = wafer_id
        self.setWindowTitle("Edit Reference Points")
        self.setMinimumWidth(480)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Set up to 3 reference points. Each point needs a microscope photo "
            "and the corresponding stage coordinates (X, Y)."
        ))

        # Three slots side by side
        slots_layout = QHBoxLayout()
        # Pad existing data to length 3
        padded = (ref_points + [None, None, None])[:3]
        self.slots = [RefPointSlot(i, padded[i], self) for i in range(3)]
        for slot in self.slots:
            slots_layout.addWidget(slot)
        layout.addLayout(slots_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Save")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _save(self):
        dest_dir = config.SHARED_DIR / "wafer_refs" / str(self.wafer_id)
        new_points = []
        for slot in self.slots:
            entry = slot.to_dict(dest_dir)
            if entry is not None:
                new_points.append(entry)
        try:
            db.update_wafer(self.wafer_id, ref_points=json.dumps(new_points))
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))


class WaferDiagramWidget(QWidget):
    """QPainter-based diagram that overlays old and new coordinate systems.

    Old system (blue, hollow / dashed):
      - Parallelogram outline of the wafer
      - Ref points as hollow circles; thumbnail photos drawn beside them
      - Flakes as hollow circles labelled with sequential numbers
      - Click any point to display its old-system coordinates

    New system (red, solid):  appears after set_new_transform() is called.
      - Same physical points transformed to the new coordinate frame
      - Plotted in the same screen space as old coords (same px/unit scale)
      - Solid circles; flakes keep their number labels

    Scale is fixed so the longest edge of the (old-coord) parallelogram
    equals TARGET_LONG_EDGE_PX pixels.
    """

    TARGET_LONG_EDGE_PX = 300
    R_CIRCLE = 6          # circle radius in pixels
    THUMB_W, THUMB_H = 72, 54

    def __init__(self, ref_points: list[dict], flakes: list[dict],
                 parent=None):
        super().__init__(parent)
        self.ref_points = ref_points
        self.flakes = flakes
        self._new_filled: list[tuple[int, tuple]] = []
        self._thumbnails: dict[int, QPixmap] = {}
        self._click_info = ""
        # screen-space hit-test caches (populated during paintEvent)
        self._old_ref_sp: list[QPointF] = []
        self._old_flake_sp: list[QPointF] = []
        # layout params (computed in _compute_layout)
        self._scale = 1.0
        self._cx = 0.0
        self._cy = 0.0
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._load_thumbnails()
        self._compute_layout()

    # ── thumbnail loading ─────────────────────────────────────────────

    def _load_thumbnails(self):
        for i, rp in enumerate(self.ref_points):
            path = rp.get("photo_path", "")
            if path and Path(path).exists():
                pm = QPixmap(str(path))
                if not pm.isNull():
                    self._thumbnails[i] = pm.scaled(
                        self.THUMB_W, self.THUMB_H,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )

    # ── geometry helpers ──────────────────────────────────────────────

    def _para_vertices(self) -> list[tuple[float, float]]:
        """Return the 4 parallelogram vertices (old coords) in draw order.

        Given 3 ref points A, B, C, there are 3 candidate 4th vertices.
        We pick the one that produces the *smallest* parallelogram area,
        which corresponds to the two shorter sides (not the diagonal)
        spanning the shape — i.e. the physically correct wafer outline.
        """
        rp = self.ref_points
        if len(rp) < 2:
            return [(rp[i]["x"], rp[i]["y"]) for i in range(len(rp))]
        if len(rp) == 2:
            ax, ay = rp[0]["x"], rp[0]["y"]
            bx, by = rp[1]["x"], rp[1]["y"]
            return [(ax, ay), (bx, by)]

        ax, ay = rp[0]["x"], rp[0]["y"]
        bx, by = rp[1]["x"], rp[1]["y"]
        cx, cy = rp[2]["x"], rp[2]["y"]

        def area(u, v):
            return abs(u[0] * v[1] - u[1] * v[0])

        candidates = [
            # (4th vertex,  draw order)
            ((ax + bx - cx, ay + by - cy),
             [(cx, cy), (ax, ay), (bx + ax - cx, by + ay - cy), (bx, by)]),
            ((ax + cx - bx, ay + cy - by),
             [(bx, by), (ax, ay), (ax + cx - bx, ay + cy - by), (cx, cy)]),
            ((bx + cx - ax, by + cy - ay),
             [(ax, ay), (bx, by), (bx + cx - ax, by + cy - ay), (cx, cy)]),
        ]
        best_order = candidates[0][1]
        best_area = float("inf")
        for (dx, dy), order in candidates:
            u = (order[1][0] - order[0][0], order[1][1] - order[0][1])
            v = (order[3][0] - order[0][0], order[3][1] - order[0][1])
            a = area(u, v)
            if a < best_area:
                best_area = a
                best_order = order
        return best_order

    def _compute_layout(self):
        """Determine scale and world-centre from old-coord parallelogram."""
        verts = self._para_vertices()
        if len(verts) < 2:
            self._scale, self._cx, self._cy = 1.0, 0.0, 0.0
            return
        longest = max(
            math.hypot(verts[(i + 1) % len(verts)][0] - verts[i][0],
                       verts[(i + 1) % len(verts)][1] - verts[i][1])
            for i in range(len(verts))
        )
        if longest == 0:
            longest = 1.0
        self._scale = self.TARGET_LONG_EDGE_PX / longest
        all_pts = verts + [(fl.get("coord_x", 0), fl.get("coord_y", 0))
                           for fl in self.flakes]
        self._cx = (min(p[0] for p in all_pts) + max(p[0] for p in all_pts)) / 2
        self._cy = (min(p[1] for p in all_pts) + max(p[1] for p in all_pts)) / 2

    def _to_screen(self, x: float, y: float) -> QPointF:
        sx = (x - self._cx) * self._scale + self.width() / 2
        sy = -(y - self._cy) * self._scale + self.height() / 2
        return QPointF(sx, sy)

    def _new_center(self) -> tuple[float, float] | None:
        """Centroid of the transformed ref points in new-coord space.

        The instruments share the same orientation; displacement merely
        reflects coordinate zeroing.  By centering the new overlay on its
        own centroid (same screen centre as the old overlay) we eliminate
        translation and show only the rotational difference — which is what
        actually matters for wafer-placement calibration.
        """
        if len(self._new_filled) < 2:
            return None
        pts = [self._fwd(rp["x"], rp["y"]) for rp in self.ref_points]
        pts = [p for p in pts if p is not None]
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts))

    def _to_screen_new(self, x_new: float, y_new: float) -> QPointF:
        """Map a new-system point to screen, centred at the same pixel as
        the old system.  Translation is ignored — only rotation/scale show."""
        nc = self._new_center()
        if nc is None:
            return self._to_screen(x_new, y_new)
        sx = (x_new - nc[0]) * self._scale + self.width() / 2
        sy = -(y_new - nc[1]) * self._scale + self.height() / 2
        return QPointF(sx, sy)

    # ── public API ────────────────────────────────────────────────────

    def set_new_transform(self, filled: list[tuple[int, tuple]]):
        """Update the new-coord overlay.  filled = [(idx,(x_new,y_new)),…]"""
        self._new_filled = filled
        self.update()

    # ── transform helper ──────────────────────────────────────────────

    def _fwd(self, x: float, y: float) -> tuple[float, float] | None:
        """Forward similarity transform (old → new) for one point."""
        if len(self._new_filled) < 2:
            return None
        i0, c0 = self._new_filled[0]
        i1, c1 = self._new_filled[1]
        r1 = (self.ref_points[i0]["x"], self.ref_points[i0]["y"])
        r2 = (self.ref_points[i1]["x"], self.ref_points[i1]["y"])
        try:
            return coord_utils.coor_transition(r1, c0, r2, c1, (x, y))
        except Exception:
            return None

    # ── painting ──────────────────────────────────────────────────────

    def paintEvent(self, _event):
        self._compute_layout()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f8f8f8"))

        has_new = len(self._new_filled) >= 2
        C_OLD = QColor("#2255cc")
        C_NEW = QColor("#cc2222")

        verts = self._para_vertices()

        # ── old: dashed parallelogram ─────────────────────────────────
        if len(verts) >= 3:
            poly = QPolygonF([self._to_screen(x, y) for x, y in verts])
            pen = QPen(C_OLD, 1.5, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(poly)

        # ── old: ref points (hollow) + thumbnails ─────────────────────
        self._old_ref_sp = []
        for i, rp in enumerate(self.ref_points):
            sp = self._to_screen(rp["x"], rp["y"])
            self._old_ref_sp.append(sp)
            painter.setPen(QPen(C_OLD, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(sp, self.R_CIRCLE, self.R_CIRCLE)
            # thumbnail
            if i in self._thumbnails:
                pm = self._thumbnails[i]
                tx = max(0, min(int(sp.x()) + 10, self.width()  - pm.width()))
                ty = max(0, min(int(sp.y()) - pm.height() // 2,
                                self.height() - pm.height()))
                painter.drawPixmap(tx, ty, pm)

        # ── old: flakes (hollow, numbered) ───────────────────────────
        self._old_flake_sp = []
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for idx, fl in enumerate(self.flakes):
            sp = self._to_screen(fl.get("coord_x", 0), fl.get("coord_y", 0))
            self._old_flake_sp.append(sp)
            painter.setPen(QPen(C_OLD, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(sp, self.R_CIRCLE, self.R_CIRCLE)
            painter.setPen(QPen(C_OLD))
            painter.drawText(QPointF(sp.x() + self.R_CIRCLE + 2,
                                     sp.y() - 3), str(idx + 1))

        # ── new: solid parallelogram + points ────────────────────────
        # Translation is removed by centering the new overlay on its own
        # centroid (same screen centre as old).  Only rotation and scale
        # remain visible, which is what matters for wafer-placement check.
        if has_new:
            # parallelogram
            new_verts = [self._fwd(x, y) for x, y in verts]
            new_poly = [self._to_screen_new(nx, ny)
                        for nxy in new_verts if nxy is not None
                        for nx, ny in [nxy]]
            if len(new_poly) >= 3:
                painter.setPen(QPen(C_NEW, 1.5, Qt.SolidLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawPolygon(QPolygonF(new_poly))

            # ref points (solid)
            for rp in self.ref_points:
                tp = self._fwd(rp["x"], rp["y"])
                if tp:
                    sp = self._to_screen_new(*tp)
                    painter.setPen(QPen(C_NEW, 2))
                    painter.setBrush(QBrush(C_NEW))
                    painter.drawEllipse(sp, self.R_CIRCLE, self.R_CIRCLE)

            # flakes (solid, numbered)
            for idx, fl in enumerate(self.flakes):
                tp = self._fwd(fl.get("coord_x", 0), fl.get("coord_y", 0))
                if tp:
                    sp = self._to_screen_new(*tp)
                    painter.setPen(QPen(C_NEW, 1.5))
                    painter.setBrush(QBrush(C_NEW))
                    painter.drawEllipse(sp, self.R_CIRCLE, self.R_CIRCLE)
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QPen(C_NEW))
                    painter.drawText(QPointF(sp.x() + self.R_CIRCLE + 2,
                                             sp.y() - 3), str(idx + 1))

        # ── click annotation ─────────────────────────────────────────
        if self._click_info:
            painter.setPen(QPen(QColor("#333333")))
            painter.drawText(
                QRectF(4, self.height() - 20, self.width() - 8, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._click_info,
            )

    # ── mouse ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        best_d = 14.0
        best_info = ""

        for i, sp in enumerate(self._old_ref_sp):
            d = math.hypot(pos.x() - sp.x(), pos.y() - sp.y())
            if d < best_d:
                rp = self.ref_points[i]
                best_info = (f"Ref {i + 1}:  "
                             f"({rp.get('x', 0):.4f},  {rp.get('y', 0):.4f})")
                best_d = d

        for idx, sp in enumerate(self._old_flake_sp):
            d = math.hypot(pos.x() - sp.x(), pos.y() - sp.y())
            if d < best_d:
                fl = self.flakes[idx]
                best_info = (f"Flake {idx + 1} [{fl['flake_id']}]:  "
                             f"({fl.get('coord_x', 0):.4f},  "
                             f"{fl.get('coord_y', 0):.4f})")
                best_d = d

        self._click_info = best_info
        self.update()


class CoordTransformDialog(QDialog):
    """Coordinate-system transformation dialog.

    Displays the ref points stored for a wafer (up to 3).  The user types
    the corresponding coordinates measured in the *new* coordinate system
    (e.g. SEM stage) into the input fields on the right.

    Behaviour
    ---------
    * Inputs are QLineEdit so they can be left empty ("not yet measured").
    * As soon as **2** slots are filled the transform parameters
      (translation dx/dy and rotation θ) are shown immediately.
    * When all **3** slots are filled, two independent estimates are
      computed (using pairs 1-2 and 2-3) and their deviation is shown as a
      reliability indicator.
    * Selecting a flake from the combo box displays its transformed
      new-system coordinates (always using the first two filled-in points).
    * Nothing is written to the database — all results are temporary.
    """

    def __init__(self, ref_points: list[dict], flakes: list[dict],
                 parent=None):
        super().__init__(parent)
        self.ref_points = ref_points
        self.flakes = flakes
        self.setWindowTitle("Coordinate Transform")
        self.setMinimumSize(980, 420)
        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setSpacing(12)

        # ── Left: wafer diagram ───────────────────────────────────────
        self._diagram = WaferDiagramWidget(self.ref_points, self.flakes, self)
        self._diagram.setMinimumSize(380, 340)
        outer.addWidget(self._diagram, stretch=2)

        # ── Right: controls ───────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(8)

        # Ref-point input table
        right.addWidget(QLabel(
            "<b>Reference Points</b> — enter new-system coordinates:"
        ))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        for col, text in enumerate(["", "Old X", "Old Y", "→", "New X", "New Y"]):
            lbl = QLabel(f"<b>{text}</b>" if text else "")
            lbl.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

        self._new_x_edits: list[QLineEdit] = []
        self._new_y_edits: list[QLineEdit] = []
        for i, rp in enumerate(self.ref_points):
            grid.addWidget(QLabel(f"Ref {i + 1}"), i + 1, 0)
            grid.addWidget(QLabel(f"{rp.get('x', 0):.4f}"), i + 1, 1)
            grid.addWidget(QLabel(f"{rp.get('y', 0):.4f}"), i + 1, 2)
            grid.addWidget(QLabel("→"), i + 1, 3)
            xe = QLineEdit()
            xe.setPlaceholderText("x")
            xe.setFixedWidth(100)
            ye = QLineEdit()
            ye.setPlaceholderText("y")
            ye.setFixedWidth(100)
            xe.textChanged.connect(self._on_input_changed)
            ye.textChanged.connect(self._on_input_changed)
            grid.addWidget(xe, i + 1, 4)
            grid.addWidget(ye, i + 1, 5)
            self._new_x_edits.append(xe)
            self._new_y_edits.append(ye)
        right.addLayout(grid)

        # Transform parameters
        right.addWidget(QLabel("<b>Transform Parameters</b>:"))
        self._params_label = QLabel(
            "(fill in at least 2 reference points above)"
        )
        self._params_label.setWordWrap(True)
        self._params_label.setStyleSheet(
            "background:#f5f5f5; padding:8px; border-radius:4px;"
        )
        self._params_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self._params_label)

        # Flake picker
        right.addWidget(QLabel("<b>Flake Coordinates</b>:"))
        flake_row = QHBoxLayout()
        flake_row.addWidget(QLabel("Flake:"))
        self._flake_combo = QComboBox()
        self._flake_combo.addItem("— select —", None)
        for fl in self.flakes:
            label = (
                f"{fl['flake_id']}  "
                f"({fl.get('coord_x', 0):.3f}, {fl.get('coord_y', 0):.3f})"
            )
            self._flake_combo.addItem(label, fl)
        self._flake_combo.currentIndexChanged.connect(self._on_flake_changed)
        flake_row.addWidget(self._flake_combo, 1)
        right.addLayout(flake_row)

        self._flake_result_label = QLabel("(no flake selected)")
        self._flake_result_label.setWordWrap(True)
        self._flake_result_label.setStyleSheet(
            "background:#f0f8ff; padding:8px; border-radius:4px;"
        )
        self._flake_result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self._flake_result_label)

        right.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        right.addWidget(close_btn)

        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setMinimumWidth(420)
        outer.addWidget(right_widget, stretch=3)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _parse_new_coords(self) -> list[tuple[float, float] | None]:
        """Return (x, y) for each slot, or None if not yet filled."""
        result = []
        for xe, ye in zip(self._new_x_edits, self._new_y_edits):
            try:
                result.append((float(xe.text()), float(ye.text())))
            except ValueError:
                result.append(None)
        return result

    @staticmethod
    def _fmt_transform(info: dict, label: str = "") -> str:
        dx, dy = info["displacement"]
        prefix = f"{label}:  " if label else ""
        return (
            f"{prefix}"
            f"dx = {dx:.4f},  dy = {dy:.4f},  "
            f"θ = {info['rotation_deg']:.4f}°,  "
            f"scale = {info['scale']:.6f}"
        )

    # ------------------------------------------------------------------ #
    #  Slots                                                               #
    # ------------------------------------------------------------------ #

    def _on_input_changed(self):
        coords = self._parse_new_coords()
        filled = [(i, c) for i, c in enumerate(coords) if c is not None]

        if len(filled) < 2:
            self._params_label.setText(
                "(fill in at least 2 reference points above)"
            )
            self._diagram.set_new_transform([])
            self._update_flake_result(filled)
            return

        old_of = lambda i: (
            self.ref_points[i].get("x", 0),
            self.ref_points[i].get("y", 0),
        )

        if len(filled) == 2:
            i0, c0 = filled[0]
            i1, c1 = filled[1]
            info = coord_utils.compute_transform_info(
                old_of(i0), c0, old_of(i1), c1
            )
            text = self._fmt_transform(info, f"Ref {i0+1}+{i1+1}")

        else:
            # 3 points — two independent estimates + reliability indicators
            i0, c0 = filled[0]
            i1, c1 = filled[1]
            i2, c2 = filled[2]
            infoAB = coord_utils.compute_transform_info(
                old_of(i0), c0, old_of(i1), c1
            )
            infoBC = coord_utils.compute_transform_info(
                old_of(i1), c1, old_of(i2), c2
            )
            ddx  = abs(infoAB["displacement"][0] - infoBC["displacement"][0])
            ddy  = abs(infoAB["displacement"][1] - infoBC["displacement"][1])
            dang = abs(infoAB["rotation_deg"]    - infoBC["rotation_deg"])
            text = (
                f"{self._fmt_transform(infoAB, f'Ref {i0+1}+{i1+1}')}\n"
                f"{self._fmt_transform(infoBC, f'Ref {i1+1}+{i2+1}')}\n"
                f"Deviation:  "
                f"Δdx = {ddx:.4f},  Δdy = {ddy:.4f},  Δθ = {dang:.4f}°\n"
                f"Scale:  "
                f"s({i0+1},{i1+1}) = {infoAB['scale']:.6f},  "
                f"s({i1+1},{i2+1}) = {infoBC['scale']:.6f}"
            )

        self._params_label.setText(text)
        self._diagram.set_new_transform(filled)
        self._update_flake_result(filled)

    def _on_flake_changed(self, _index):
        coords = self._parse_new_coords()
        filled = [(i, c) for i, c in enumerate(coords) if c is not None]
        self._diagram.set_new_transform(filled)
        self._update_flake_result(filled)

    def _update_flake_result(self, filled: list):
        flake = self._flake_combo.currentData()
        if flake is None:
            self._flake_result_label.setText("(no flake selected)")
            return
        if len(filled) < 2:
            self._flake_result_label.setText(
                "(establish a transform first — fill in at least 2 ref points)"
            )
            return
        # Always use the first two filled points for the actual transform
        i0, c0 = filled[0]
        i1, c1 = filled[1]
        old_of = lambda i: (
            self.ref_points[i].get("x", 0),
            self.ref_points[i].get("y", 0),
        )
        try:
            nx, ny = coord_utils.coor_transition(
                old_of(i0), c0,
                old_of(i1), c1,
                (flake.get("coord_x", 0), flake.get("coord_y", 0)),
            )
            self._flake_result_label.setText(
                f"Old:  ({flake.get('coord_x', 0):.4f},  "
                f"{flake.get('coord_y', 0):.4f})\n"
                f"New:  ({nx:.4f},  {ny:.4f})"
            )
        except Exception as exc:
            self._flake_result_label.setText(f"Transform error: {exc}")


class WaferWidget(QWidget):
    """Main widget for wafer and flake management."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_box_id = None
        self.current_wafer_id = None
        self.init_ui()
        self.load_boxes()

    def init_ui(self):
        """Initialize the user interface."""
        main_layout = QHBoxLayout()

        # Left panel - Box list
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Wafer Boxes"))

        self.box_list = QListWidget()
        self.box_list.itemSelectionChanged.connect(self.on_box_selected)
        left_layout.addWidget(self.box_list)

        box_btn_layout = QVBoxLayout()
        add_box_btn = QPushButton("Add Box")
        add_box_btn.clicked.connect(self.add_box)
        box_btn_layout.addWidget(add_box_btn)

        rename_box_btn = QPushButton("Rename Box")
        rename_box_btn.clicked.connect(self.rename_box)
        box_btn_layout.addWidget(rename_box_btn)

        delete_box_btn = QPushButton("Delete Box")
        delete_box_btn.clicked.connect(self.delete_box)
        box_btn_layout.addWidget(delete_box_btn)

        left_layout.addLayout(box_btn_layout)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)

        # Center panel - Wafer grid
        center_layout = QVBoxLayout()
        center_layout.addWidget(QLabel("Wafer Grid"))
        self.grid_view = WaferGridView()
        self.grid_view.cell_clicked.connect(self.on_cell_clicked)
        center_layout.addWidget(self.grid_view)
        center_widget = QWidget()
        center_widget.setLayout(center_layout)

        # Right panel - Flake details
        right_layout = QVBoxLayout()
        self.wafer_header = QLabel("No wafer selected")
        right_layout.addWidget(self.wafer_header)

        # Reference points section
        ref_header = QHBoxLayout()
        ref_header.addWidget(QLabel("Reference Points:"))
        edit_ref_btn = QPushButton("Edit…")
        edit_ref_btn.setFixedWidth(60)
        edit_ref_btn.clicked.connect(self.edit_ref_points)
        ref_header.addWidget(edit_ref_btn)
        right_layout.addLayout(ref_header)

        self.ref_points_label = QLabel("None")
        self.ref_points_label.setWordWrap(True)
        right_layout.addWidget(self.ref_points_label)

        # Flake table
        right_layout.addWidget(QLabel("Flakes:"))
        self.flake_table = QTableWidget()
        self.flake_table.setColumnCount(6)
        self.flake_table.setHorizontalHeaderLabels(
            ["ID", "Material", "Thickness", "Magnification", "Status", "Notes"]
        )
        self.flake_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.flake_table.itemChanged.connect(self.on_flake_cell_changed)
        right_layout.addWidget(self.flake_table)

        # Flake buttons
        flake_btn_layout = QHBoxLayout()
        add_flake_btn = QPushButton("Add Flake")
        add_flake_btn.clicked.connect(self.add_flake)
        flake_btn_layout.addWidget(add_flake_btn)

        delete_flake_btn = QPushButton("Delete Flake")
        delete_flake_btn.clicked.connect(self.delete_flake)
        flake_btn_layout.addWidget(delete_flake_btn)

        view_photo_btn = QPushButton("View Photo")
        view_photo_btn.clicked.connect(self.view_photo)
        flake_btn_layout.addWidget(view_photo_btn)

        transform_btn = QPushButton("Coordinate Transform")
        transform_btn.clicked.connect(self.show_transform_dialog)
        flake_btn_layout.addWidget(transform_btn)

        right_layout.addLayout(flake_btn_layout)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)

        # Main splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([200, 400, 400])

        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

    def refresh(self):
        """Reload all data from the database."""
        self.load_boxes()
        if self.current_box_id is not None:
            self.load_grid(self.current_box_id)
        if self.current_wafer_id is not None:
            self.load_flakes_for_wafer(self.current_wafer_id)
            self.load_ref_points()

    def load_boxes(self):
        """Load all wafer boxes into the list."""
        try:
            boxes = db.get_all_boxes()
            self.box_list.clear()
            for box in boxes:
                item = QListWidgetItem(f"{box['name']} ({box['rows']}x{box['cols']})")
                item.setData(Qt.UserRole, box['box_id'])
                self.box_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to load boxes: {str(e)}")

    def on_box_selected(self):
        """Handle box selection."""
        items = self.box_list.selectedItems()
        if not items:
            return

        self.current_box_id = items[0].data(Qt.UserRole)
        self.current_wafer_id = None
        self.load_grid()
        self.flake_table.setRowCount(0)
        self.wafer_header.setText("No wafer selected")
        self.ref_points_label.setText("None")

    def load_grid(self):
        """Load and display the wafer grid for current box."""
        if not self.current_box_id:
            return

        try:
            boxes = db.get_all_boxes()
            box = next((b for b in boxes if b['box_id'] == self.current_box_id), None)

            if not box:
                return

            flake_counts = db.get_wafer_flake_counts(self.current_box_id)
            self.grid_view.set_grid(box['rows'], box['cols'], flake_counts)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to load grid: {str(e)}")

    def on_cell_clicked(self, row: int, col: int):
        """Handle wafer cell click."""
        if not self.current_box_id:
            return

        try:
            wafer = db.get_or_create_wafer(self.current_box_id, row, col)
            self.current_wafer_id = wafer['wafer_id']
            self.load_flakes_for_wafer(wafer)
            self.load_ref_points(wafer)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to load wafer: {str(e)}")

    def load_flakes_for_wafer(self, wafer: dict):
        """Load flakes for the selected wafer."""
        try:
            flakes = db.get_flakes_for_wafer(wafer['wafer_id'])
            label = wafer.get('label', '')
            row_label = chr(ord('A') + wafer['row'])
            col_label = wafer['col'] + 1
            self.wafer_header.setText(
                f"Wafer {row_label}{col_label} - {label}"
            )

            self.flake_table.setRowCount(len(flakes))
            for i, flake in enumerate(flakes):
                self.flake_table.setItem(i, 0, QTableWidgetItem(flake['flake_id']))
                self.flake_table.setItem(i, 1, QTableWidgetItem(flake.get('material', '')))
                self.flake_table.setItem(i, 2, QTableWidgetItem(flake.get('thickness', '')))
                self.flake_table.setItem(i, 3, QTableWidgetItem(flake.get('magnification', '')))
                self.flake_table.setItem(i, 4, QTableWidgetItem(flake.get('status', '')))
                self.flake_table.setItem(i, 5, QTableWidgetItem(flake.get('notes', '')))

                # Store flake_id in row
                for col in range(6):
                    self.flake_table.item(i, col).setData(Qt.UserRole, flake['flake_id'])
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to load flakes: {str(e)}")

    def load_ref_points(self, wafer: dict):
        """Load and display reference points summary for wafer."""
        try:
            ref_points = json.loads(wafer.get('ref_points', '[]') or '[]')
            if not ref_points:
                self.ref_points_label.setText("None")
                return
            lines = []
            for i, rp in enumerate(ref_points):
                photo_ok = "📷" if rp.get('photo_path') and Path(rp['photo_path']).exists() else "—"
                lines.append(f"Ref {i+1}: {photo_ok}  ({rp.get('x', 0):.2f}, {rp.get('y', 0):.2f})")
            self.ref_points_label.setText("\n".join(lines))
        except Exception:
            self.ref_points_label.setText("Error loading ref points")

    def on_flake_cell_changed(self, item):
        """Handle flake table cell edits."""
        if not self.current_wafer_id or item is None:
            return

        try:
            row = item.row()
            flake_id = self.flake_table.item(row, 0).data(Qt.UserRole)

            update_data = {
                'material': self.flake_table.item(row, 1).text(),
                'thickness': self.flake_table.item(row, 2).text(),
                'magnification': self.flake_table.item(row, 3).text(),
                'status': self.flake_table.item(row, 4).text(),
                'notes': self.flake_table.item(row, 5).text(),
            }

            db.update_flake(flake_id, **update_data)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to update flake: {str(e)}")

    def add_box(self):
        """Add a new wafer box."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Wafer Box")
        dialog.setGeometry(100, 100, 400, 200)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Box Name:"))
        name_input = QLineEdit()
        layout.addWidget(name_input)

        layout.addWidget(QLabel("Rows:"))
        rows_spin = QSpinBox()
        rows_spin.setValue(5)
        rows_spin.setMinimum(1)
        layout.addWidget(rows_spin)

        layout.addWidget(QLabel("Columns:"))
        cols_spin = QSpinBox()
        cols_spin.setValue(5)
        cols_spin.setMinimum(1)
        layout.addWidget(cols_spin)

        layout.addWidget(QLabel("Notes:"))
        notes_input = QTextEdit()
        notes_input.setMaximumHeight(80)
        layout.addWidget(notes_input)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Create")
        cancel_btn = QPushButton("Cancel")

        def create_box():
            try:
                if not name_input.text().strip():
                    QMessageBox.warning(dialog, "Error", "Please enter a box name")
                    return

                db.create_box(
                    name_input.text().strip(),
                    rows=rows_spin.value(),
                    cols=cols_spin.value(),
                    notes=notes_input.toPlainText()
                )
                dialog.accept()
                self.load_boxes()
            except Exception as e:
                QMessageBox.critical(dialog, "Database Error", str(e))

        ok_btn.clicked.connect(create_box)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)
        dialog.exec()

    def rename_box(self):
        """Rename the selected box."""
        items = self.box_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "Error", "Please select a box to rename")
            return

        box_id = items[0].data(Qt.UserRole)
        old_name = items[0].text().split(" (")[0]

        dialog = QDialog(self)
        dialog.setWindowTitle("Rename Box")
        dialog.setGeometry(100, 100, 300, 100)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("New Name:"))
        name_input = QLineEdit(old_name)
        layout.addWidget(name_input)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Rename")
        cancel_btn = QPushButton("Cancel")

        def rename():
            try:
                if not name_input.text().strip():
                    QMessageBox.warning(dialog, "Error", "Name cannot be empty")
                    return
                db.update_box(box_id, name=name_input.text().strip())
                dialog.accept()
                self.load_boxes()
            except Exception as e:
                QMessageBox.critical(dialog, "Database Error", str(e))

        ok_btn.clicked.connect(rename)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)
        dialog.exec()

    def delete_box(self):
        """Delete the selected box."""
        items = self.box_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "Error", "Please select a box to delete")
            return

        box_id = items[0].data(Qt.UserRole)
        box_name = items[0].text().split(" (")[0]

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete box '{box_name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                db.delete_box(box_id)
                self.load_boxes()
                self.current_box_id = None
                self.grid_view.set_grid(0, 0, {})
                self.flake_table.setRowCount(0)
            except Exception as e:
                QMessageBox.critical(self, "Database Error", f"Failed to delete box: {str(e)}")

    def add_flake(self):
        """Add a new flake to the current wafer."""
        if not self.current_wafer_id:
            QMessageBox.warning(self, "Error", "Please select a wafer first")
            return

        dialog = AddFlakeDialog(self.current_wafer_id, self)
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            data = dialog.get_data()

            if not data['flake_id']:
                QMessageBox.warning(self, "Error", "Flake ID is required")
                return

            photo_path = None
            if data['photo_path']:
                flake_dir = config.FLAKES_DIR / data['flake_id']
                flake_dir.mkdir(parents=True, exist_ok=True)
                dest = flake_dir / Path(data['photo_path']).name
                shutil.copy(data['photo_path'], dest)
                photo_path = str(dest)

            db.create_flake(
                flake_id=data['flake_id'],
                wafer_id=self.current_wafer_id,
                material=data['material'],
                thickness=data['thickness'],
                magnification=data['magnification'],
                photo_path=photo_path or '',
                coord_x=data['coord_x'],
                coord_y=data['coord_y'],
                notes=data['notes']
            )

            wafer = db.get_or_create_wafer(
                self.current_box_id,
                self.grid_view.selected_cell[0],
                self.grid_view.selected_cell[1]
            )
            self.load_flakes_for_wafer(wafer)
            self.load_grid()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to add flake: {str(e)}")

    def delete_flake(self):
        """Delete the selected flake."""
        current_row = self.flake_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Error", "Please select a flake to delete")
            return

        flake_id = self.flake_table.item(current_row, 0).data(Qt.UserRole)

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete flake '{flake_id}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                db.delete_flake(flake_id)
                wafer = db.get_or_create_wafer(
                    self.current_box_id,
                    self.grid_view.selected_cell[0],
                    self.grid_view.selected_cell[1]
                )
                self.load_flakes_for_wafer(wafer)
                self.load_grid()
            except Exception as e:
                QMessageBox.critical(self, "Database Error", f"Failed to delete flake: {str(e)}")

    def view_photo(self):
        """Open the photo for the selected flake."""
        current_row = self.flake_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Error", "Please select a flake to view")
            return

        flake_id = self.flake_table.item(current_row, 0).data(Qt.UserRole)

        try:
            flakes = db.get_flakes_for_wafer(self.current_wafer_id)
            flake = next((f for f in flakes if f['flake_id'] == flake_id), None)

            if not flake or not flake.get('photo_path'):
                QMessageBox.warning(self, "Error", "No photo available for this flake")
                return

            photo_path = Path(flake['photo_path'])
            if photo_path.exists():
                import subprocess
                subprocess.Popen(['xdg-open', str(photo_path)])
            else:
                QMessageBox.warning(self, "Error", "Photo file not found")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to view photo: {str(e)}")

    def edit_ref_points(self):
        """Open the reference points editor for the current wafer."""
        if not self.current_wafer_id:
            QMessageBox.warning(self, "Error", "Please select a wafer first")
            return
        try:
            wafer = db.get_wafer_by_id(self.current_wafer_id)
            ref_points = json.loads(wafer.get('ref_points', '[]') or '[]')
            dialog = RefPointsDialog(self.current_wafer_id, ref_points, self)
            if dialog.exec() == QDialog.Accepted:
                wafer = db.get_wafer_by_id(self.current_wafer_id)
                self.load_ref_points(wafer)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def show_transform_dialog(self):
        """Show coordinate transform dialog."""
        if not self.current_wafer_id:
            QMessageBox.warning(self, "Error", "Please select a wafer first")
            return

        try:
            wafer = db.get_wafer_by_id(self.current_wafer_id)
            ref_points = json.loads(wafer.get('ref_points', '[]') or '[]')

            if len(ref_points) < 2:
                QMessageBox.warning(
                    self, "Error",
                    "Need at least 2 reference points for transformation.\n"
                    "Use 'Edit…' to add them first."
                )
                return

            flakes = db.get_flakes_for_wafer(self.current_wafer_id)
            dialog = CoordTransformDialog(ref_points, flakes, self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open transform dialog: {str(e)}")
