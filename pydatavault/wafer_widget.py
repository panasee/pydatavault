import json
import shutil
from pathlib import Path
from typing import Optional, Dict, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QTextEdit, QMessageBox, QFileDialog,
    QHeaderView, QComboBox
)
from PySide6.QtCore import Qt, Signal, QSize, QRect, QPoint
from PySide6.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPixmap

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


class CoordTransformDialog(QDialog):
    """Dialog for transforming coordinates using reference points."""

    def __init__(self, ref_points: List[dict], parent=None,
                 target: tuple | None = None):
        super().__init__(parent)
        self.ref_points = ref_points
        self._prefill_target = target
        self.setWindowTitle("Coordinate Transform")
        self.setGeometry(100, 100, 600, 400)

        layout = QVBoxLayout()

        layout.addWidget(QLabel("Select 2 Reference Points:"))

        # Reference point selectors
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel("Ref Point 1:"))
        self.ref1_combo = QComboBox()
        for i, rp in enumerate(ref_points):
            self.ref1_combo.addItem(f"Point {i+1} ({rp.get('x', 0)}, {rp.get('y', 0)})", i)
        ref_layout.addWidget(self.ref1_combo)

        ref_layout.addWidget(QLabel("Ref Point 2:"))
        self.ref2_combo = QComboBox()
        for i, rp in enumerate(ref_points):
            self.ref2_combo.addItem(f"Point {i+1} ({rp.get('x', 0)}, {rp.get('y', 0)})", i)
        if len(ref_points) > 1:
            self.ref2_combo.setCurrentIndex(1)
        ref_layout.addWidget(self.ref2_combo)
        layout.addLayout(ref_layout)

        # New coordinates for reference points
        layout.addWidget(QLabel("New Reference Coordinates:"))

        new_ref_layout = QHBoxLayout()
        new_ref_layout.addWidget(QLabel("Ref 1 new X:"))
        self.ref1_new_x = QDoubleSpinBox()
        self.ref1_new_x.setRange(-10000, 10000)
        self.ref1_new_x.setSingleStep(0.1)
        new_ref_layout.addWidget(self.ref1_new_x)

        new_ref_layout.addWidget(QLabel("Ref 1 new Y:"))
        self.ref1_new_y = QDoubleSpinBox()
        self.ref1_new_y.setRange(-10000, 10000)
        self.ref1_new_y.setSingleStep(0.1)
        new_ref_layout.addWidget(self.ref1_new_y)
        layout.addLayout(new_ref_layout)

        new_ref2_layout = QHBoxLayout()
        new_ref2_layout.addWidget(QLabel("Ref 2 new X:"))
        self.ref2_new_x = QDoubleSpinBox()
        self.ref2_new_x.setRange(-10000, 10000)
        self.ref2_new_x.setSingleStep(0.1)
        new_ref2_layout.addWidget(self.ref2_new_x)

        new_ref2_layout.addWidget(QLabel("Ref 2 new Y:"))
        self.ref2_new_y = QDoubleSpinBox()
        self.ref2_new_y.setRange(-10000, 10000)
        self.ref2_new_y.setSingleStep(0.1)
        new_ref2_layout.addWidget(self.ref2_new_y)
        layout.addLayout(new_ref2_layout)

        # Target point
        layout.addWidget(QLabel("Target Point (original coordinates):"))
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("X:"))
        self.target_x = QDoubleSpinBox()
        self.target_x.setRange(-1e9, 1e9)
        self.target_x.setDecimals(4)
        self.target_x.setSingleStep(0.1)
        if target is not None:
            self.target_x.setValue(target[0])
        target_layout.addWidget(self.target_x)

        target_layout.addWidget(QLabel("Y:"))
        self.target_y = QDoubleSpinBox()
        self.target_y.setRange(-1e9, 1e9)
        self.target_y.setDecimals(4)
        self.target_y.setSingleStep(0.1)
        if target is not None:
            self.target_y.setValue(target[1])
        target_layout.addWidget(self.target_y)
        layout.addLayout(target_layout)

        # Result
        layout.addWidget(QLabel("Transformed Result:"))
        self.result_label = QLabel("(pending)")
        self.result_label.setStyleSheet("background-color: #f0f0f0; padding: 10px;")
        layout.addWidget(self.result_label)

        # Compute button
        compute_btn = QPushButton("Compute Transform")
        compute_btn.clicked.connect(self.compute_transform)
        layout.addWidget(compute_btn)

        layout.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self.setLayout(layout)

    def compute_transform(self):
        """Compute the coordinate transformation."""
        try:
            ref1_idx = self.ref1_combo.currentData()
            ref2_idx = self.ref2_combo.currentData()

            if ref1_idx == ref2_idx:
                QMessageBox.warning(self, "Error", "Must select different reference points")
                return

            ref1 = self.ref_points[ref1_idx]
            ref2 = self.ref_points[ref2_idx]

            result = coord_utils.coor_transition(
                (ref1['x'], ref1['y']),
                (self.ref1_new_x.value(), self.ref1_new_y.value()),
                (ref2['x'], ref2['y']),
                (self.ref2_new_x.value(), self.ref2_new_y.value()),
                (self.target_x.value(), self.target_y.value())
            )

            self.result_label.setText(f"X: {result[0]:.4f}, Y: {result[1]:.4f}")
        except Exception as e:
            QMessageBox.critical(self, "Transform Error", str(e))


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
            wafer = db.get_or_create_wafer(
                self.current_box_id,
                self.grid_view.selected_cell[0],
                self.grid_view.selected_cell[1]
            )

            ref_points_str = wafer.get('ref_points', '[]')
            ref_points = json.loads(ref_points_str) if ref_points_str else []

            if len(ref_points) < 2:
                QMessageBox.warning(
                    self, "Error",
                    "Need at least 2 reference points for transformation"
                )
                return

            # Pre-fill target from the selected flake row if one is selected
            target = None
            row = self.flake_table.currentRow()
            if row >= 0:
                flake_id = self.flake_table.item(row, 0).text()
                flake = db.get_flake(flake_id)
                if flake:
                    target = (flake['coord_x'], flake['coord_y'])

            dialog = CoordTransformDialog(ref_points, self, target=target)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open transform dialog: {str(e)}")
