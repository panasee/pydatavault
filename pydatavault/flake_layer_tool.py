"""Calibration-based optical microscope flake layer estimator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import style
from .flake_calibration_store import CalibrationSample, FlakeCalibrationStore


@dataclass(frozen=True)
class CalibrationEntry:
    """One layer centroid in substrate-normalized RGB space."""

    layers: int
    normalized_rgb: tuple[float, float, float]


def mean_rgb_in_circle(
    image: QImage,
    center: tuple[float, float],
    radius: int,
) -> tuple[float, float, float]:
    """Return mean RGB values inside a source-image circle."""
    if image.isNull():
        raise ValueError("No microscope image is loaded")
    if radius < 1:
        raise ValueError("Sample radius must be at least one pixel")

    cx, cy = center
    x0 = max(0, math.floor(cx - radius))
    x1 = min(image.width() - 1, math.ceil(cx + radius))
    y0 = max(0, math.floor(cy - radius))
    y1 = min(image.height() - 1, math.ceil(cy + radius))
    radius_sq = radius * radius

    total_r = total_g = total_b = count = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 > radius_sq:
                continue
            color = image.pixelColor(x, y)
            total_r += color.red()
            total_g += color.green()
            total_b += color.blue()
            count += 1

    if count == 0:
        raise ValueError("The sample region does not contain any image pixels")
    return total_r / count, total_g / count, total_b / count


def rgb_optical_contrast(
    substrate_rgb: tuple[float, float, float],
    flake_rgb: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Compute 100 * (I_substrate - I_flake) / I_substrate per channel."""
    if any(value <= 0 for value in substrate_rgb):
        raise ValueError("Substrate RGB values must all be greater than zero")
    return tuple(
        100.0 * (substrate - flake) / substrate
        for substrate, flake in zip(substrate_rgb, flake_rgb)
    )


def substrate_normalized_rgb(
    substrate_rgb: tuple[float, float, float],
    region_rgb: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Normalize each RGB channel so the local substrate equals 100%."""
    if any(value <= 0 for value in substrate_rgb):
        raise ValueError("Substrate RGB values must all be greater than zero")
    return tuple(
        100.0 * region / substrate
        for substrate, region in zip(substrate_rgb, region_rgb)
    )


def substrate_quality_warning(
    substrate_rgb: tuple[float, float, float],
) -> str | None:
    """Warn when clipping makes substrate normalization unreliable."""
    if any(value >= 250 for value in substrate_rgb):
        return "substrate is near saturation; reduce exposure before calibration"
    if any(value <= 5 for value in substrate_rgb):
        return "substrate is near black; increase exposure before calibration"
    return None


def calibration_centroids(
    samples: list[CalibrationSample],
) -> list[CalibrationEntry]:
    """Average all labeled regions for each layer count."""
    if not samples:
        raise ValueError("Add at least one known layer region")
    grouped: dict[int, list[tuple[float, float, float]]] = {}
    for sample in samples:
        grouped.setdefault(sample.layers, []).append(sample.normalized_rgb)
    return [
        CalibrationEntry(
            layers,
            tuple(
                sum(values[channel] for values in layer_samples) / len(layer_samples)
                for channel in range(3)
            ),
        )
        for layers, layer_samples in sorted(grouped.items())
    ]


def nearest_calibration(
    measured: tuple[float, float, float],
    entries: list[CalibrationEntry],
) -> tuple[CalibrationEntry, float, float | None]:
    """Return nearest entry, RGB distance, and margin to the runner-up."""
    if not entries:
        raise ValueError("At least one calibration row is required")

    ranked = sorted(
        (
            (math.dist(measured, entry.normalized_rgb), entry)
            for entry in entries
        ),
        key=lambda item: item[0],
    )
    best_distance, best_entry = ranked[0]
    margin = ranked[1][0] - best_distance if len(ranked) > 1 else None
    return best_entry, best_distance, margin


def cropped_image(image: QImage, rect: QRectF) -> QImage:
    """Return an in-memory crop without modifying the source image."""
    if image.isNull():
        raise ValueError("No microscope image is loaded")
    crop_rect = rect.normalized().toAlignedRect().intersected(image.rect())
    if crop_rect.width() < 2 or crop_rect.height() < 2:
        raise ValueError("Select a crop region at least 2 × 2 pixels")
    return image.copy(crop_rect)


class MicroscopeImageView(QGraphicsView):
    """Image view that reports clicks in source-image coordinates."""

    image_clicked = Signal(float, float)
    crop_selected = Signal(QRectF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._markers: dict[str, QGraphicsEllipseItem] = {}
        self._crop_item: QGraphicsRectItem | None = None
        self._crop_start: QPointF | None = None
        self._press_position: QPoint | None = None
        self._crop_mode = False
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#111827"))
        self.setMinimumSize(560, 440)

    def set_image(self, image: QImage) -> None:
        self._scene.clear()
        self._markers.clear()
        self._crop_item = None
        self._crop_start = None
        self._pixmap_item = self._scene.addPixmap(QPixmap.fromImage(image))
        self._scene.setSceneRect(QRectF(image.rect()))
        self.fit_image()

    def fit_image(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    def zoom_by_factor(self, factor: float) -> None:
        """Zoom while keeping the transform within usable limits."""
        if self._pixmap_item is None or factor <= 0:
            return
        new_scale = self.transform().m11() * factor
        if 0.02 <= new_scale <= 100.0:
            self.scale(factor, factor)

    def set_crop_mode(self, enabled: bool) -> None:
        self._crop_mode = enabled
        self._crop_start = None
        self.setDragMode(QGraphicsView.NoDrag if enabled else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if enabled:
            self.clear_crop_selection()

    def crop_rect(self) -> QRectF | None:
        if self._crop_item is None:
            return None
        return self._crop_item.rect()

    def clear_crop_selection(self) -> None:
        if self._crop_item is not None:
            self._scene.removeItem(self._crop_item)
            self._crop_item = None
        self._crop_start = None

    def set_marker(self, name: str, point: tuple[float, float], radius: int) -> None:
        old_marker = self._markers.pop(name, None)
        if old_marker is not None:
            self._scene.removeItem(old_marker)
        colors = {"substrate": QColor("#22c55e"), "flake": QColor("#ef4444")}
        x, y = point
        marker = self._scene.addEllipse(
            x - radius,
            y - radius,
            radius * 2,
            radius * 2,
            QPen(colors[name], max(2, radius / 5)),
        )
        marker.setZValue(1)
        self._markers[name] = marker

    def clear_marker(self, name: str) -> None:
        marker = self._markers.pop(name, None)
        if marker is not None:
            self._scene.removeItem(marker)

    def wheelEvent(self, event) -> None:
        if self._pixmap_item is None:
            super().wheelEvent(event)
            return
        self.zoom_by_factor(1.25 if event.angleDelta().y() > 0 else 0.8)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if self._pixmap_item is None or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_position = event.position().toPoint()
        if self._crop_mode:
            point = self.mapToScene(self._press_position)
            image_rect = self._pixmap_item.sceneBoundingRect()
            if image_rect.contains(point):
                self.clear_crop_selection()
                self._crop_start = point
                self._crop_item = self._scene.addRect(
                    QRectF(point, point),
                    QPen(QColor("#facc15"), 2, Qt.DashLine),
                    QBrush(QColor(250, 204, 21, 35)),
                )
                self._crop_item.setZValue(2)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._crop_mode and self._crop_start is not None and self._crop_item is not None:
            point = self.mapToScene(event.position().toPoint())
            image_rect = self._pixmap_item.sceneBoundingRect()
            point.setX(min(max(point.x(), image_rect.left()), image_rect.right()))
            point.setY(min(max(point.y(), image_rect.top()), image_rect.bottom()))
            self._crop_item.setRect(QRectF(self._crop_start, point).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pixmap_item is None or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._crop_mode:
            if self._crop_item is not None:
                rect = self._crop_item.rect().normalized()
                if rect.width() >= 2 and rect.height() >= 2:
                    self.crop_selected.emit(rect)
                else:
                    self.clear_crop_selection()
            self._crop_start = None
            event.accept()
            return

        super().mouseReleaseEvent(event)
        if self._press_position is None:
            return
        release_position = event.position().toPoint()
        if (release_position - self._press_position).manhattanLength() <= 4:
            point = self.mapToScene(release_position)
            if self._pixmap_item.contains(self._pixmap_item.mapFromScene(point)):
                self.image_clicked.emit(point.x(), point.y())
        self._press_position = None


class FlakeLayerAnalyzerDialog(QDialog):
    """Estimate flake layer count from user-calibrated optical contrast."""

    def __init__(
        self,
        parent=None,
        *,
        store: FlakeCalibrationStore | None = None,
        calibration_mode: bool = False,
        material_id: int | None = None,
    ):
        super().__init__(parent)
        self.store = store or FlakeCalibrationStore()
        self.calibration_mode = calibration_mode
        self.calibration_material_id = material_id
        self.setWindowTitle(
            "Material Calibration" if calibration_mode else "Flake Layer Analyzer"
        )
        self.setWindowIcon(style.app_icon())
        self.resize(1160, 720)

        self.image = QImage()
        self.original_image = QImage()
        self.image_path: Path | None = None
        self.is_cropped = False
        self.sample_mode = "substrate"
        self.sample_points: dict[str, tuple[float, float]] = {}
        self.sample_values: dict[str, tuple[float, float, float]] = {}
        self.calibration_samples: list[CalibrationSample] = []

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_toolbar = QHBoxLayout()
        self.open_button = QPushButton("Open Microscope Image...")
        style.decorate_button(self.open_button, "primary", "photo")
        self.fit_button = QPushButton("Fit Image")
        self.crop_button = QPushButton("Crop")
        self.crop_button.setCheckable(True)
        self.apply_crop_button = QPushButton("Apply Crop")
        self.apply_crop_button.setEnabled(False)
        self.reset_image_button = QPushButton("Reset Original")
        self.reset_image_button.setEnabled(False)
        image_toolbar.addWidget(self.open_button)
        image_toolbar.addWidget(self.fit_button)
        image_toolbar.addWidget(self.crop_button)
        image_toolbar.addWidget(self.apply_crop_button)
        image_toolbar.addWidget(self.reset_image_button)
        image_toolbar.addStretch()
        image_layout.addLayout(image_toolbar)

        self.image_view = MicroscopeImageView()
        image_layout.addWidget(self.image_view, 1)
        self.image_name_label = QLabel("No image loaded")
        self.image_name_label.setProperty("subtle", True)
        image_layout.addWidget(self.image_name_label)
        navigation_label = QLabel(
            "Mouse wheel: zoom · Drag: pan · Single click: sample · "
            "Crop: drag a rectangle, then Apply Crop"
        )
        navigation_label.setProperty("subtle", True)
        image_layout.addWidget(navigation_label)
        splitter.addWidget(image_panel)

        controls = QWidget()
        controls.setMinimumWidth(400)
        controls.setMinimumHeight(780 if self.calibration_mode else 0)
        controls_layout = QVBoxLayout(controls)

        sample_group = QGroupBox("1. Normalize the current photo")
        sample_layout = QVBoxLayout(sample_group)
        sample_buttons = QHBoxLayout()
        self.substrate_button = QPushButton("Pick Substrate")
        self.substrate_button.setCheckable(True)
        self.substrate_button.setChecked(True)
        self.flake_button = QPushButton("Pick Flake / Region")
        self.flake_button.setCheckable(True)
        sample_buttons.addWidget(self.substrate_button)
        sample_buttons.addWidget(self.flake_button)
        sample_layout.addLayout(sample_buttons)
        radius_form = QFormLayout()
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(1, 200)
        self.radius_spin.setValue(8)
        self.radius_spin.setSuffix(" px")
        radius_form.addRow("Circular sample radius:", self.radius_spin)
        sample_layout.addLayout(radius_form)
        self.substrate_value_label = QLabel("Substrate RGB: not sampled")
        self.flake_value_label = QLabel("Flake RGB: not sampled")
        self.contrast_label = QLabel("Substrate-normalized RGB: not available")
        self.contrast_label.setWordWrap(True)
        sample_layout.addWidget(self.substrate_value_label)
        sample_layout.addWidget(self.flake_value_label)
        sample_layout.addWidget(self.contrast_label)
        controls_layout.addWidget(sample_group)

        calibration_group = QGroupBox("2. Build calibration from known regions")
        calibration_layout = QVBoxLayout(calibration_group)
        calibration_note = QLabel(
            "For each known photo: pick nearby bare substrate, pick a known layer region, "
            "set the layer count below, then add it. You can combine several photos."
        )
        calibration_note.setWordWrap(True)
        calibration_layout.addWidget(calibration_note)

        profile_form = QFormLayout()
        self.material_input = QLineEdit()
        self.material_input.setPlaceholderText("e.g. Graphene")
        self.substrate_input = QLineEdit()
        self.substrate_input.setPlaceholderText("e.g. 285 nm SiO2/Si")
        profile_form.addRow("Material:", self.material_input)
        profile_form.addRow("Fixed substrate:", self.substrate_input)
        calibration_layout.addLayout(profile_form)

        known_region_row = QHBoxLayout()
        known_region_row.addWidget(QLabel("Known layer count:"))
        self.known_layers_spin = QSpinBox()
        self.known_layers_spin.setRange(1, 200)
        self.known_layers_spin.setValue(1)
        known_region_row.addWidget(self.known_layers_spin)
        self.add_known_region_button = QPushButton("Add Picked Region")
        style.decorate_button(self.add_known_region_button, "utility")
        known_region_row.addWidget(self.add_known_region_button, 1)
        calibration_layout.addLayout(known_region_row)

        self.calibration_table = QTableWidget(0, 5)
        self.calibration_table.setHorizontalHeaderLabels(
            ["Photo", "Layers", "Norm R (%)", "Norm G (%)", "Norm B (%)"]
        )
        self.calibration_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.calibration_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.calibration_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.calibration_table.setMinimumHeight(150)
        calibration_layout.addWidget(self.calibration_table)
        calibration_buttons = QHBoxLayout()
        self.remove_row_button = QPushButton("Remove Selected")
        self.clear_samples_button = QPushButton("Clear All")
        self.save_calibration_button = QPushButton("Save Calibration to Database")
        style.decorate_button(self.save_calibration_button, "primary")
        for button in (
            self.remove_row_button,
            self.clear_samples_button,
            self.save_calibration_button,
        ):
            calibration_buttons.addWidget(button)
        calibration_layout.addLayout(calibration_buttons)
        controls_layout.addWidget(calibration_group, 1)

        result_group = QGroupBox("2. Estimated layer count")
        result_layout = QVBoxLayout(result_group)
        material_row = QHBoxLayout()
        material_row.addWidget(QLabel("Material:"))
        self.material_combo = QComboBox()
        material_row.addWidget(self.material_combo, 1)
        self.manage_calibrations_button = QPushButton("Manage Database...")
        material_row.addWidget(self.manage_calibrations_button)
        result_layout.addLayout(material_row)
        self.estimate_button = QPushButton("Estimate from Calibration")
        style.decorate_button(self.estimate_button, "primary")
        self.result_label = QLabel("Add or load known layer regions to estimate.")
        self.result_label.setWordWrap(True)
        result_layout.addWidget(self.estimate_button)
        result_layout.addWidget(self.result_label)
        controls_layout.addWidget(result_group)

        warning = QLabel(
            "Optical contrast is a preliminary estimate. Confirm critical assignments "
            "with Raman spectroscopy, AFM, or another independent measurement."
        )
        warning.setWordWrap(True)
        warning.setProperty("subtle", True)
        controls_layout.addWidget(warning)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls)
        controls_scroll.setMinimumWidth(420)
        splitter.addWidget(controls_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        self.open_button.clicked.connect(self.open_image)
        self.fit_button.clicked.connect(self.image_view.fit_image)
        self.crop_button.toggled.connect(self._toggle_crop_mode)
        self.apply_crop_button.clicked.connect(self.apply_crop)
        self.reset_image_button.clicked.connect(self.reset_original_image)
        self.substrate_button.clicked.connect(lambda: self._set_sample_mode("substrate"))
        self.flake_button.clicked.connect(lambda: self._set_sample_mode("flake"))
        self.image_view.image_clicked.connect(self._sample_image)
        self.image_view.crop_selected.connect(
            lambda _rect: self.apply_crop_button.setEnabled(True)
        )
        self.radius_spin.valueChanged.connect(self._resample_points)
        self.add_known_region_button.clicked.connect(self.add_known_region)
        self.remove_row_button.clicked.connect(self.remove_calibration_rows)
        self.clear_samples_button.clicked.connect(self.clear_calibration_samples)
        self.save_calibration_button.clicked.connect(self.save_calibration_to_database)
        self.estimate_button.clicked.connect(self.estimate_layers)
        self.material_combo.currentIndexChanged.connect(self._material_changed)
        self.manage_calibrations_button.clicked.connect(self.open_calibration_manager)

        self.calibration_group = calibration_group
        self.result_group = result_group
        if self.calibration_mode:
            self.result_group.hide()
            self._load_calibration_material()
        else:
            self.calibration_group.hide()
            self.refresh_materials()

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Microscope Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All Files (*)",
        )
        if not path:
            return
        try:
            self.load_image(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Open Image", str(exc))

    def load_image(self, path: str | Path) -> None:
        image = QImage(str(path))
        if image.isNull():
            raise ValueError(f"Could not read image: {path}")
        self.original_image = image.convertToFormat(QImage.Format_RGB32)
        self.image_path = Path(path)
        self._set_working_image(self.original_image, is_cropped=False)

    def _set_working_image(self, image: QImage, *, is_cropped: bool) -> None:
        """Replace the in-memory working image and clear coordinate-based state."""
        self.image = image.copy()
        self.is_cropped = is_cropped
        self.sample_points.clear()
        self.sample_values.clear()
        self._set_sample_mode("substrate")
        self.image_view.set_image(self.image)
        self.crop_button.setChecked(False)
        self.apply_crop_button.setEnabled(False)
        self.reset_image_button.setEnabled(is_cropped)
        suffix = " (cropped in memory)" if is_cropped else ""
        name = self.image_path.name if self.image_path is not None else "image"
        self.image_name_label.setText(
            f"{name} — {self.image.width()} × {self.image.height()} px{suffix}"
        )
        self._update_measurement_labels()

    def _toggle_crop_mode(self, enabled: bool) -> None:
        if enabled and self.image.isNull():
            self.crop_button.setChecked(False)
            return
        self.image_view.set_crop_mode(enabled)
        if not enabled:
            self.image_view.clear_crop_selection()
            self.apply_crop_button.setEnabled(False)

    def apply_crop(self) -> None:
        rect = self.image_view.crop_rect()
        if rect is None:
            return
        try:
            cropped = cropped_image(self.image, rect)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Crop Image", str(exc))
            return
        self._set_working_image(cropped, is_cropped=True)

    def reset_original_image(self) -> None:
        if not self.original_image.isNull():
            self._set_working_image(self.original_image, is_cropped=False)

    def _set_sample_mode(self, mode: str) -> None:
        self.sample_mode = mode
        self.substrate_button.setChecked(mode == "substrate")
        self.flake_button.setChecked(mode == "flake")

    def _sample_image(self, x: float, y: float) -> None:
        if self.image.isNull():
            return
        self.sample_points[self.sample_mode] = (x, y)
        self._resample(self.sample_mode)
        if self.sample_mode == "substrate":
            self._set_sample_mode("flake")

    def _resample(self, name: str) -> None:
        point = self.sample_points[name]
        radius = self.radius_spin.value()
        self.sample_values[name] = mean_rgb_in_circle(self.image, point, radius)
        self.image_view.set_marker(name, point, radius)
        self._update_measurement_labels()

    def _resample_points(self) -> None:
        for name in list(self.sample_points):
            self._resample(name)

    @staticmethod
    def _format_rgb(rgb: tuple[float, float, float]) -> str:
        return ", ".join(f"{value:.1f}" for value in rgb)

    def measured_contrast(self) -> tuple[float, float, float] | None:
        if "substrate" not in self.sample_values or "flake" not in self.sample_values:
            return None
        return rgb_optical_contrast(
            self.sample_values["substrate"],
            self.sample_values["flake"],
        )

    def measured_normalized_rgb(self) -> tuple[float, float, float] | None:
        if "substrate" not in self.sample_values or "flake" not in self.sample_values:
            return None
        return substrate_normalized_rgb(
            self.sample_values["substrate"],
            self.sample_values["flake"],
        )

    def _validated_current_normalized_rgb(self) -> tuple[float, float, float]:
        normalized = self.measured_normalized_rgb()
        if normalized is None:
            raise ValueError("Pick both a bare substrate and a flake/region")
        warning = substrate_quality_warning(self.sample_values["substrate"])
        if warning:
            raise ValueError(f"Substrate reference is unsuitable: {warning}")
        return normalized

    def _update_measurement_labels(self) -> None:
        substrate = self.sample_values.get("substrate")
        flake = self.sample_values.get("flake")
        self.substrate_value_label.setText(
            "Substrate RGB: " + (self._format_rgb(substrate) if substrate else "not sampled")
        )
        self.flake_value_label.setText(
            "Flake RGB: " + (self._format_rgb(flake) if flake else "not sampled")
        )
        normalized = self.measured_normalized_rgb()
        if normalized is None:
            self.contrast_label.setText("Substrate-normalized RGB: not available")
        else:
            contrast = self.measured_contrast()
            warning = substrate_quality_warning(substrate)
            warning_text = f" Warning: {warning}." if warning else ""
            self.contrast_label.setText(
                "Normalized RGB (substrate = 100% per channel): "
                + ", ".join(f"{value:.3f}%" for value in normalized)
                + ". Optical contrast: "
                + ", ".join(f"{value:.3f}%" for value in contrast)
                + "."
                + warning_text
            )
        self.result_label.setText(
            "Add known layer regions, then save the calibration."
            if self.calibration_mode
            else "Pick the substrate and flake, then estimate with the selected material."
        )

    def add_known_region(self) -> None:
        try:
            normalized = self._validated_current_normalized_rgb()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Add Known Region", str(exc))
            return

        source_name = self.image_path.name if self.image_path is not None else "in-memory image"
        if self.is_cropped:
            source_name += " (cropped)"
        sample = CalibrationSample(
            self.known_layers_spin.value(),
            normalized,
            source_name,
            self.sample_values["substrate"],
            self.sample_values["flake"],
        )
        self.calibration_samples.append(sample)
        self._refresh_calibration_table()
        self.sample_points.pop("flake", None)
        self.sample_values.pop("flake", None)
        self.image_view.clear_marker("flake")
        self._set_sample_mode("flake")
        self._update_measurement_labels()
        self.result_label.setText(
            f"Added {sample.layers}-layer region from {sample.source_image}."
        )

    def _refresh_calibration_table(self) -> None:
        self.calibration_table.setRowCount(0)
        for sample in self.calibration_samples:
            row = self.calibration_table.rowCount()
            self.calibration_table.insertRow(row)
            values = (
                sample.source_image,
                str(sample.layers),
                *(f"{value:.3f}" for value in sample.normalized_rgb),
            )
            for column, value in enumerate(values):
                self.calibration_table.setItem(row, column, QTableWidgetItem(value))

    def remove_calibration_rows(self) -> None:
        rows = sorted(
            {index.row() for index in self.calibration_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            del self.calibration_samples[row]
        self._refresh_calibration_table()

    def clear_calibration_samples(self) -> None:
        """Clear the editor only; persisted data remains until Save is clicked."""
        self.calibration_samples.clear()
        self._refresh_calibration_table()

    def calibration_entries(self) -> list[CalibrationEntry]:
        return calibration_centroids(self.calibration_samples)

    def estimate_layers(self) -> None:
        try:
            normalized = self._validated_current_normalized_rgb()
            entry, distance, margin = nearest_calibration(
                normalized,
                self.calibration_entries(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Estimate Layer Count", str(exc))
            return

        margin_text = (
            f"; separation from the next match: {margin:.3f} percentage points"
            if margin is not None
            else ""
        )
        material = self.material_input.text().strip() or "unnamed material"
        substrate = self.substrate_input.text().strip()
        profile_name = f"{material} on {substrate}" if substrate else material
        self.result_label.setText(
            f"{profile_name}: nearest calibration is {entry.layers} "
            f"layer{'s' if entry.layers != 1 else ''}. "
            f"RGB distance: {distance:.3f} percentage points{margin_text}."
        )

    def _load_calibration_material(self) -> None:
        if self.calibration_material_id is None:
            self.material_input.clear()
            self.substrate_input.clear()
            return
        material = self.store.get_material(self.calibration_material_id)
        if material is None:
            raise ValueError("Selected calibration material no longer exists")
        self.material_input.setText(material["material_name"])
        self.substrate_input.setText(material["substrate"])
        self.material_input.setReadOnly(True)
        self.substrate_input.setReadOnly(True)
        self.calibration_samples = self.store.get_samples(self.calibration_material_id)
        self._refresh_calibration_table()
        self.setWindowTitle(
            f"Recalibrate {material['material_name']} — {material['substrate']}"
        )

    def save_calibration_to_database(self) -> None:
        try:
            material_id = self.store.save_calibration(
                self.material_input.text(),
                self.substrate_input.text(),
                self.calibration_samples,
                self.calibration_material_id,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Save Calibration", str(exc))
            return
        self.calibration_material_id = material_id
        QMessageBox.information(
            self,
            "Calibration Saved",
            "The material calibration was saved to the local calibration database.",
        )
        self.accept()

    def refresh_materials(self, selected_id: int | None = None) -> None:
        current_id = selected_id
        if current_id is None:
            current_id = self.material_combo.currentData()
        materials = self.store.list_materials()
        self.material_combo.blockSignals(True)
        self.material_combo.clear()
        for material in materials:
            label = f"{material['material_name']} — {material['substrate']}"
            self.material_combo.addItem(label, material["material_id"])
        self.material_combo.blockSignals(False)

        if not materials:
            self.calibration_samples = []
            self.material_input.clear()
            self.substrate_input.clear()
            self.result_label.setText(
                "No calibrated materials. Use Manage Database to add one."
            )
            self.estimate_button.setEnabled(False)
            return
        target_index = self.material_combo.findData(current_id)
        self.material_combo.setCurrentIndex(target_index if target_index >= 0 else 0)
        self.estimate_button.setEnabled(True)
        self._material_changed()

    def _material_changed(self) -> None:
        material_id = self.material_combo.currentData()
        if material_id is None:
            return
        material = self.store.get_material(material_id)
        if material is None:
            self.refresh_materials()
            return
        self.material_input.setText(material["material_name"])
        self.substrate_input.setText(material["substrate"])
        self.calibration_samples = self.store.get_samples(material_id)
        self.result_label.setText(
            f"Selected {material['material_name']} on {material['substrate']} "
            f"({len(self.calibration_samples)} known regions)."
        )

    def open_calibration_manager(self) -> None:
        dialog = CalibrationDatabaseDialog(self.store, self)
        dialog.exec()
        self.refresh_materials(dialog.last_material_id)


class CalibrationDatabaseDialog(QDialog):
    """List stored materials and route additions/recalibrations to the editor."""

    def __init__(self, store: FlakeCalibrationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.last_material_id: int | None = None
        self.setWindowTitle("Flake Calibration Database")
        self.setWindowIcon(style.app_icon())
        self.resize(620, 480)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Select an existing material to recalibrate it, or use + to create a "
            "new material for a fixed substrate."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.material_list = QListWidget()
        layout.addWidget(self.material_list, 1)

        buttons = QHBoxLayout()
        self.add_material_button = QPushButton("+")
        self.add_material_button.setToolTip("Add a new calibrated material")
        style.decorate_button(self.add_material_button, "primary", "plus")
        buttons.addWidget(self.add_material_button)
        buttons.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.material_list.itemClicked.connect(self._recalibrate_item)
        self.add_material_button.clicked.connect(self._add_material)
        self.refresh()

    def refresh(self) -> None:
        self.material_list.clear()
        for material in self.store.list_materials():
            layers = material.get("layer_counts") or "none"
            item = QListWidgetItem(
                f"{material['material_name']} — {material['substrate']}\n"
                f"{material['sample_count']} regions · calibrated layers: {layers}"
            )
            item.setData(Qt.UserRole, material["material_id"])
            self.material_list.addItem(item)

    def _add_material(self) -> None:
        self._open_editor(None)

    def _recalibrate_item(self, item: QListWidgetItem) -> None:
        self._open_editor(item.data(Qt.UserRole))

    def _open_editor(self, material_id: int | None) -> None:
        editor = FlakeLayerAnalyzerDialog(
            self,
            store=self.store,
            calibration_mode=True,
            material_id=material_id,
        )
        if editor.exec() == QDialog.Accepted:
            self.last_material_id = editor.calibration_material_id
            self.refresh()
