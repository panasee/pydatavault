"""Independent microscope-image adjustment and RGB line-profile tool."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from PySide6.QtCore import (
    QPoint,
    QPointF,
    QDir,
    QRectF,
    QTemporaryDir,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import style


def qimage_to_rgb_array(image: QImage) -> np.ndarray:
    """Copy a QImage into an H x W x 3 uint8 RGB array."""
    if image.isNull():
        raise ValueError("No microscope image is loaded")
    canonical = image.convertToFormat(QImage.Format_RGBA8888)
    packed = np.frombuffer(
        canonical.constBits(),
        dtype=np.uint8,
        count=canonical.sizeInBytes(),
    ).reshape(canonical.height(), canonical.bytesPerLine())
    rgba = packed[:, : canonical.width() * 4].reshape(
        canonical.height(), canonical.width(), 4
    )
    return rgba[:, :, :3].copy()


def rgb_array_to_qimage(rgb: np.ndarray) -> QImage:
    """Copy an H x W x 3 array into an owned QImage."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image data must have shape (height, width, 3)")
    contiguous = np.ascontiguousarray(np.clip(rgb, 0, 255), dtype=np.uint8)
    height, width, _ = contiguous.shape
    image = QImage(
        contiguous.data,
        width,
        height,
        contiguous.strides[0],
        QImage.Format_RGB888,
    )
    return image.copy()


def bounded_region(
    shape: tuple[int, int, int],
    rect: tuple[int, int, int, int],
) -> tuple[slice, slice]:
    """Convert an image-space rectangle to non-empty bounded array slices."""
    height, width = shape[:2]
    x, y, rect_width, rect_height = rect
    x0 = max(0, min(width, x))
    y0 = max(0, min(height, y))
    x1 = max(0, min(width, x + rect_width))
    y1 = max(0, min(height, y + rect_height))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("White-balance reference area is outside the image")
    return slice(y0, y1), slice(x0, x1)


def white_balance_gains(
    rgb: np.ndarray,
    rect: tuple[int, int, int, int],
    strength: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return interpolated gray-world gains and the reference-region RGB mean."""
    if not 0.0 <= strength <= 1.0:
        raise ValueError("White-balance strength must be between 0 and 1")
    rows, columns = bounded_region(rgb.shape, rect)
    region_mean = rgb[rows, columns].astype(np.float64).mean(axis=(0, 1))
    if np.any(region_mean <= 0):
        raise ValueError("White-balance reference area contains a zero RGB channel")
    neutral_level = float(region_mean.mean())
    full_gains = neutral_level / region_mean
    gains = 1.0 + strength * (full_gains - 1.0)
    return gains, region_mean


def apply_image_adjustments(
    rgb: np.ndarray,
    *,
    white_balance_rect: tuple[int, int, int, int] | None = None,
    white_balance_strength: float = 1.0,
    auto_brightness_target: float | None = None,
    exposure_ev: float = 0.0,
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    gamma: float = 1.0,
) -> np.ndarray:
    """Apply non-destructive RGB adjustments to an original uint8 image."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image data must have shape (height, width, 3)")
    if contrast < 0 or saturation < 0 or gamma <= 0:
        raise ValueError("Contrast and saturation must be non-negative; gamma must be positive")
    if auto_brightness_target is not None and not 0 < auto_brightness_target <= 255:
        raise ValueError("Automatic background brightness target must be between 0 and 255")

    adjusted = rgb.astype(np.float32) / 255.0
    if white_balance_rect is not None:
        gains, _region_mean = white_balance_gains(
            rgb,
            white_balance_rect,
            white_balance_strength,
        )
        adjusted *= gains.astype(np.float32)
    if auto_brightness_target is not None:
        if white_balance_rect is None:
            raise ValueError(
                "Select a white-balance reference area before using automatic brightness"
            )
        rows, columns = bounded_region(rgb.shape, white_balance_rect)
        reference_brightness = float(adjusted[rows, columns].mean())
        if reference_brightness <= 0:
            raise ValueError("Automatic-brightness reference area is black")
        adjusted *= (auto_brightness_target / 255.0) / reference_brightness
    adjusted *= 2.0 ** exposure_ev
    adjusted += brightness / 255.0
    adjusted = (adjusted - 0.5) * contrast + 0.5
    adjusted = np.clip(adjusted, 0.0, 1.0)
    adjusted = np.power(adjusted, 1.0 / gamma)
    luminance = (
        0.2126 * adjusted[:, :, 0]
        + 0.7152 * adjusted[:, :, 1]
        + 0.0722 * adjusted[:, :, 2]
    )[:, :, None]
    adjusted = luminance + saturation * (adjusted - luminance)
    return np.rint(np.clip(adjusted, 0.0, 1.0) * 255.0).astype(np.uint8)


def sample_rgb_profile(
    rgb: np.ndarray,
    start: tuple[float, float],
    end: tuple[float, float],
    averaging_width: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample processed RGB intensity along a line in pixel-distance coordinates."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image data must have shape (height, width, 3)")
    if averaging_width < 1:
        raise ValueError("Profile averaging width must be at least one pixel")
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length < 1.0:
        raise ValueError("Draw a profile path at least one pixel long")

    count = max(2, math.ceil(length) + 1)
    x_values = np.linspace(start[0], end[0], count)
    y_values = np.linspace(start[1], end[1], count)
    x_indices = np.clip(np.rint(x_values).astype(int), 0, rgb.shape[1] - 1)
    y_indices = np.clip(np.rint(y_values).astype(int), 0, rgb.shape[0] - 1)
    if averaging_width == 1:
        values = rgb[y_indices, x_indices].astype(np.float64)
    else:
        radius = averaging_width // 2
        values = np.empty((count, 3), dtype=np.float64)
        for index, (x, y) in enumerate(zip(x_indices, y_indices)):
            x0 = max(0, x - radius)
            x1 = min(rgb.shape[1], x + radius + 1)
            y0 = max(0, y - radius)
            y1 = min(rgb.shape[0], y + radius + 1)
            values[index] = rgb[y0:y1, x0:x1].mean(axis=(0, 1))
    return np.linspace(0.0, length, count), values


def plotly_react_script(figure: go.Figure, config: dict) -> str:
    """Build one independently scoped Plotly update script."""
    figure_json = figure.to_json()
    config_json = json.dumps(config)
    return (
        "(() => {"
        f"const figure = {figure_json};"
        "Plotly.react('rgb-profile', figure.data, figure.layout, "
        f"{config_json});"
        "})();"
    )


class ImageSelectionView(QGraphicsView):
    """Zoomable image view for rectangle and line selections."""

    white_balance_region_selected = Signal(QRectF)
    profile_path_selected = Signal(QPointF, QPointF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._wb_item: QGraphicsRectItem | None = None
        self._profile_item: QGraphicsLineItem | None = None
        self._mode: str | None = None
        self._selection_start: QPointF | None = None
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#111827"))
        self.setMinimumSize(600, 480)

    def set_image(self, image: QImage, *, fit: bool = False) -> None:
        if self._pixmap_item is None:
            self._pixmap_item = self._scene.addPixmap(QPixmap.fromImage(image))
            self._pixmap_item.setZValue(0)
            fit = True
        else:
            self._pixmap_item.setPixmap(QPixmap.fromImage(image))
        self._scene.setSceneRect(QRectF(image.rect()))
        if fit:
            self.fit_image()

    def clear_selections(self) -> None:
        for item_name in ("_wb_item", "_profile_item"):
            item = getattr(self, item_name)
            if item is not None:
                self._scene.removeItem(item)
                setattr(self, item_name, None)

    def fit_image(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    def set_selection_mode(self, mode: str | None) -> None:
        if mode not in (None, "white_balance", "profile"):
            raise ValueError(f"Unknown image selection mode: {mode}")
        self._mode = mode
        self._selection_start = None
        selecting = mode is not None
        self.setDragMode(QGraphicsView.NoDrag if selecting else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if selecting else Qt.ArrowCursor)

    def _bounded_point(self, viewport_point: QPoint) -> QPointF:
        point = self.mapToScene(viewport_point)
        rect = self._pixmap_item.sceneBoundingRect()
        return QPointF(
            min(max(point.x(), rect.left()), rect.right()),
            min(max(point.y(), rect.top()), rect.bottom()),
        )

    def wheelEvent(self, event) -> None:
        if self._pixmap_item is None:
            super().wheelEvent(event)
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_scale = self.transform().m11() * factor
        if 0.02 <= new_scale <= 100.0:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if (
            self._pixmap_item is None
            or self._mode is None
            or event.button() != Qt.LeftButton
        ):
            super().mousePressEvent(event)
            return
        self._selection_start = self._bounded_point(event.position().toPoint())
        if self._mode == "white_balance":
            if self._wb_item is None:
                self._wb_item = self._scene.addRect(
                    QRectF(self._selection_start, self._selection_start),
                    QPen(QColor("#facc15"), 2, Qt.DashLine),
                )
                self._wb_item.setZValue(2)
        else:
            if self._profile_item is None:
                self._profile_item = self._scene.addLine(
                    self._selection_start.x(),
                    self._selection_start.y(),
                    self._selection_start.x(),
                    self._selection_start.y(),
                    QPen(QColor("#f8fafc"), 2),
                )
                self._profile_item.setZValue(2)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode is None or self._selection_start is None:
            super().mouseMoveEvent(event)
            return
        point = self._bounded_point(event.position().toPoint())
        if self._mode == "white_balance" and self._wb_item is not None:
            self._wb_item.setRect(QRectF(self._selection_start, point).normalized())
        elif self._profile_item is not None:
            self._profile_item.setLine(
                self._selection_start.x(),
                self._selection_start.y(),
                point.x(),
                point.y(),
            )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if (
            self._mode is None
            or self._selection_start is None
            or event.button() != Qt.LeftButton
        ):
            super().mouseReleaseEvent(event)
            return
        point = self._bounded_point(event.position().toPoint())
        if self._mode == "white_balance" and self._wb_item is not None:
            rect = QRectF(self._selection_start, point).normalized()
            self._wb_item.setRect(rect)
            if rect.width() >= 2 and rect.height() >= 2:
                self.white_balance_region_selected.emit(rect)
        elif self._profile_item is not None:
            self._profile_item.setLine(
                self._selection_start.x(),
                self._selection_start.y(),
                point.x(),
                point.y(),
            )
            if math.hypot(
                point.x() - self._selection_start.x(),
                point.y() - self._selection_start.y(),
            ) >= 1.0:
                self.profile_path_selected.emit(self._selection_start, point)
        self._selection_start = None
        event.accept()


class RGBProfilePlot(QWidget):
    """Offline interactive Plotly view of RGB intensity against path distance."""

    SERIES = (
        ("R", "#d55e00", "solid"),
        ("G", "#009e73", "dash"),
        ("B", "#0072b2", "dot"),
    )
    CONFIG = {
        "responsive": True,
        "displaylogo": False,
        "scrollZoom": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "toImageButtonOptions": {
            "format": "png",
            "filename": "rgb_path_profile",
            "scale": 2,
        },
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.distances = np.empty(0)
        self.values = np.empty((0, 3))
        self.setMinimumHeight(270)
        temporary_template = str(
            Path(QDir.tempPath()) / "pydatavault-plotly-XXXXXX"
        )
        self._temporary_directory = QTemporaryDir(temporary_template)
        if not self._temporary_directory.isValid():
            raise RuntimeError("Could not create a temporary directory for Plotly")
        self._html_path = Path(self._temporary_directory.path()) / "rgb_profile.html"
        self._page_loaded = False
        self._pending_figure: go.Figure | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web_view = QWebEngineView()
        layout.addWidget(self.web_view)
        self.web_view.loadFinished.connect(self._plotly_page_loaded)

        initial_figure = self._build_figure()
        initial_figure.write_html(
            self._html_path,
            include_plotlyjs="directory",
            full_html=True,
            config=self.CONFIG,
            div_id="rgb-profile",
        )
        self.web_view.load(QUrl.fromLocalFile(str(self._html_path)))

    def set_profile(self, distances: np.ndarray, values: np.ndarray) -> None:
        self.distances = np.asarray(distances, dtype=float)
        self.values = np.asarray(values, dtype=float)
        self._set_figure(self._build_figure())

    def clear(self) -> None:
        self.distances = np.empty(0)
        self.values = np.empty((0, 3))
        self._set_figure(self._build_figure())

    def _build_figure(self) -> go.Figure:
        figure = go.Figure()
        has_profile = (
            self.distances.size >= 2
            and self.values.shape == (self.distances.size, 3)
        )
        if has_profile:
            for channel, (label, color, dash) in enumerate(self.SERIES):
                figure.add_trace(
                    go.Scattergl(
                        x=self.distances,
                        y=self.values[:, channel],
                        mode="lines",
                        name=label,
                        line={"color": color, "width": 2, "dash": dash},
                        hovertemplate=(
                            "Distance: %{x:.2f} px<br>"
                            + f"{label}: "
                            + "%{y:.2f}<extra></extra>"
                        ),
                    )
                )
        else:
            figure.add_annotation(
                text="Draw a path on the image",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font={"color": "#64748b", "size": 14},
            )

        figure.update_layout(
            template="plotly_white",
            autosize=True,
            margin={"l": 58, "r": 18, "t": 38, "b": 52},
            hovermode="x unified",
            dragmode="zoom",
            legend={
                "orientation": "h",
                "x": 1.0,
                "xanchor": "right",
                "y": 1.02,
                "yanchor": "bottom",
            },
            xaxis={
                "title": "Distance along path (px)",
                "showspikes": True,
                "spikemode": "across",
                "spikesnap": "cursor",
            },
            yaxis={
                "title": "Intensity (0–255)",
                "range": [0, 255],
                "fixedrange": False,
            },
            uirevision="rgb-profile",
        )
        return figure

    def _plotly_page_loaded(self, succeeded: bool) -> None:
        self._page_loaded = succeeded
        if succeeded and self._pending_figure is not None:
            self._apply_figure(self._pending_figure)

    def _set_figure(self, figure: go.Figure) -> None:
        self._pending_figure = figure
        if self._page_loaded:
            self._apply_figure(figure)

    def _apply_figure(self, figure: go.Figure) -> None:
        self.web_view.page().runJavaScript(
            plotly_react_script(figure, self.CONFIG)
        )


class MicroscopeImageProcessorDialog(QDialog):
    """Non-destructive microscope image adjustment and RGB profile dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Microscope Image Processor")
        self.setWindowIcon(style.app_icon())
        self.resize(1380, 880)

        self.image_path: Path | None = None
        self.original_rgb: np.ndarray | None = None
        self.processed_rgb: np.ndarray | None = None
        self.processed_image = QImage()
        self.white_balance_rect: tuple[int, int, int, int] | None = None
        self.profile_path: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._process_timer = QTimer(self)
        self._process_timer.setSingleShot(True)
        self._process_timer.timeout.connect(self._reprocess)

        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.open_button = QPushButton("Open Image...")
        self.fit_button = QPushButton("Fit Image")
        self.save_button = QPushButton("Save Processed Image...")
        self.save_button.setEnabled(False)
        style.decorate_button(self.open_button, "primary")
        for button in (self.open_button, self.fit_button, self.save_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)
        self.image_view = ImageSelectionView()
        splitter.addWidget(self.image_view)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        self.image_label = QLabel("No image loaded")
        self.image_label.setWordWrap(True)
        controls_layout.addWidget(self.image_label)

        white_balance_group = QGroupBox("Regional white balance")
        white_balance_layout = QVBoxLayout(white_balance_group)
        self.select_white_balance_button = QPushButton("Select Reference Area")
        self.select_white_balance_button.setCheckable(True)
        white_balance_layout.addWidget(self.select_white_balance_button)
        self.white_balance_label = QLabel("Reference area: not selected")
        self.white_balance_label.setWordWrap(True)
        white_balance_layout.addWidget(self.white_balance_label)
        white_balance_form = QFormLayout()
        self.white_balance_strength_spin = QDoubleSpinBox()
        self.white_balance_strength_spin.setRange(0.0, 100.0)
        self.white_balance_strength_spin.setValue(100.0)
        self.white_balance_strength_spin.setSuffix(" %")
        white_balance_form.addRow("White-balance strength:", self.white_balance_strength_spin)
        self.auto_brightness_check = QCheckBox("Normalize reference brightness")
        self.auto_brightness_check.setChecked(True)
        self.auto_brightness_check.setEnabled(False)
        white_balance_form.addRow(self.auto_brightness_check)
        self.auto_brightness_target_spin = QSpinBox()
        self.auto_brightness_target_spin.setRange(1, 255)
        self.auto_brightness_target_spin.setValue(130)
        self.auto_brightness_target_spin.setSuffix(" / 255")
        self.auto_brightness_target_spin.setEnabled(False)
        white_balance_form.addRow(
            "Automatic brightness target:",
            self.auto_brightness_target_spin,
        )
        white_balance_layout.addLayout(white_balance_form)
        controls_layout.addWidget(white_balance_group)

        adjustment_group = QGroupBox("Image adjustments")
        adjustment_form = QFormLayout(adjustment_group)
        self.exposure_spin = self._double_spin(-4.0, 4.0, 0.0, 0.1, " EV")
        self.brightness_spin = self._double_spin(-100.0, 100.0, 0.0, 1.0, "")
        self.contrast_spin = self._double_spin(0.0, 300.0, 100.0, 1.0, " %")
        self.saturation_spin = self._double_spin(0.0, 300.0, 100.0, 1.0, " %")
        self.gamma_spin = self._double_spin(0.10, 3.00, 1.00, 0.05, "")
        adjustment_form.addRow("Exposure:", self.exposure_spin)
        adjustment_form.addRow("Brightness offset:", self.brightness_spin)
        adjustment_form.addRow("Contrast:", self.contrast_spin)
        adjustment_form.addRow("Saturation:", self.saturation_spin)
        adjustment_form.addRow("Gamma:", self.gamma_spin)
        self.reset_button = QPushButton("Reset Adjustments")
        adjustment_form.addRow(self.reset_button)
        controls_layout.addWidget(adjustment_group)

        profile_group = QGroupBox("RGB path profile")
        profile_layout = QVBoxLayout(profile_group)
        profile_row = QHBoxLayout()
        self.select_profile_button = QPushButton("Draw Profile Path")
        self.select_profile_button.setCheckable(True)
        profile_row.addWidget(self.select_profile_button)
        self.profile_width_spin = QSpinBox()
        self.profile_width_spin.setRange(1, 51)
        self.profile_width_spin.setSingleStep(2)
        self.profile_width_spin.setValue(1)
        self.profile_width_spin.setSuffix(" px")
        profile_row.addWidget(QLabel("Averaging width:"))
        profile_row.addWidget(self.profile_width_spin)
        profile_layout.addLayout(profile_row)
        self.profile_label = QLabel("Path: not selected")
        self.profile_label.setWordWrap(True)
        profile_layout.addWidget(self.profile_label)
        self.profile_plot = RGBProfilePlot()
        profile_layout.addWidget(self.profile_plot)
        controls_layout.addWidget(profile_group, 1)

        note = QLabel(
            "Processing is non-destructive and always starts from the imported image. "
            "The RGB profile is sampled from the currently processed result."
        )
        note.setWordWrap(True)
        note.setProperty("subtle", True)
        controls_layout.addWidget(note)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls)
        controls_scroll.setMinimumWidth(470)
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
        self.save_button.clicked.connect(self.save_image)
        self.select_white_balance_button.toggled.connect(self._toggle_white_balance_mode)
        self.select_profile_button.toggled.connect(self._toggle_profile_mode)
        self.image_view.white_balance_region_selected.connect(
            self._white_balance_region_selected
        )
        self.image_view.profile_path_selected.connect(self._profile_path_selected)
        self.reset_button.clicked.connect(self.reset_adjustments)
        self.profile_width_spin.valueChanged.connect(self._update_profile)
        self.auto_brightness_check.toggled.connect(self._auto_brightness_toggled)
        self.auto_brightness_target_spin.valueChanged.connect(self._schedule_reprocess)
        for spin in (
            self.white_balance_strength_spin,
            self.exposure_spin,
            self.brightness_spin,
            self.contrast_spin,
            self.saturation_spin,
            self.gamma_spin,
        ):
            spin.valueChanged.connect(self._schedule_reprocess)

    @staticmethod
    def _double_spin(
        minimum: float,
        maximum: float,
        value: float,
        step: float,
        suffix: str,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.setSuffix(suffix)
        return spin

    def open_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
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
        self.image_path = Path(path)
        self.original_rgb = qimage_to_rgb_array(image)
        self.white_balance_rect = None
        self.profile_path = None
        self.auto_brightness_check.setEnabled(False)
        self.auto_brightness_target_spin.setEnabled(False)
        self.image_view.clear_selections()
        self.white_balance_label.setText("Reference area: not selected")
        self.profile_label.setText("Path: not selected")
        self.profile_plot.clear()
        self.reset_adjustments()
        self.image_label.setText(
            f"{self.image_path.name} — {image.width()} × {image.height()} px"
        )
        self.save_button.setEnabled(True)
        self._reprocess(fit=True)

    def reset_adjustments(self) -> None:
        self.white_balance_strength_spin.setValue(100.0)
        self.auto_brightness_check.setChecked(True)
        self.auto_brightness_target_spin.setValue(130)
        self.exposure_spin.setValue(0.0)
        self.brightness_spin.setValue(0.0)
        self.contrast_spin.setValue(100.0)
        self.saturation_spin.setValue(100.0)
        self.gamma_spin.setValue(1.0)
        self._schedule_reprocess()

    def _toggle_white_balance_mode(self, enabled: bool) -> None:
        if enabled:
            self.select_profile_button.setChecked(False)
        self.image_view.set_selection_mode("white_balance" if enabled else None)

    def _toggle_profile_mode(self, enabled: bool) -> None:
        if enabled:
            self.select_white_balance_button.setChecked(False)
        self.image_view.set_selection_mode("profile" if enabled else None)

    def _white_balance_region_selected(self, rect: QRectF) -> None:
        aligned = rect.normalized().toAlignedRect()
        self.white_balance_rect = (
            aligned.x(),
            aligned.y(),
            aligned.width(),
            aligned.height(),
        )
        rows, columns = bounded_region(self.original_rgb.shape, self.white_balance_rect)
        mean = self.original_rgb[rows, columns].mean(axis=(0, 1))
        self.white_balance_label.setText(
            "Reference mean RGB: " + ", ".join(f"{value:.1f}" for value in mean)
        )
        self.auto_brightness_check.setEnabled(True)
        self.auto_brightness_target_spin.setEnabled(
            self.auto_brightness_check.isChecked()
        )
        self.select_white_balance_button.setChecked(False)
        self._schedule_reprocess()

    def _auto_brightness_toggled(self, enabled: bool) -> None:
        self.auto_brightness_target_spin.setEnabled(
            enabled and self.white_balance_rect is not None
        )
        self._schedule_reprocess()

    def _profile_path_selected(self, start: QPointF, end: QPointF) -> None:
        self.profile_path = ((start.x(), start.y()), (end.x(), end.y()))
        length = math.hypot(end.x() - start.x(), end.y() - start.y())
        self.profile_label.setText(
            f"Path: ({start.x():.1f}, {start.y():.1f}) → "
            f"({end.x():.1f}, {end.y():.1f}), length {length:.1f} px"
        )
        self.select_profile_button.setChecked(False)
        self._update_profile()

    def _schedule_reprocess(self) -> None:
        if self.original_rgb is not None:
            self._process_timer.start(40)

    def _reprocess(self, *, fit: bool = False) -> None:
        if self.original_rgb is None:
            return
        try:
            self.processed_rgb = apply_image_adjustments(
                self.original_rgb,
                white_balance_rect=self.white_balance_rect,
                white_balance_strength=self.white_balance_strength_spin.value() / 100.0,
                auto_brightness_target=(
                    self.auto_brightness_target_spin.value()
                    if self.auto_brightness_check.isChecked()
                    and self.white_balance_rect is not None
                    else None
                ),
                exposure_ev=self.exposure_spin.value(),
                brightness=self.brightness_spin.value(),
                contrast=self.contrast_spin.value() / 100.0,
                saturation=self.saturation_spin.value() / 100.0,
                gamma=self.gamma_spin.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Process Image", str(exc))
            return
        self.processed_image = rgb_array_to_qimage(self.processed_rgb)
        self.image_view.set_image(self.processed_image, fit=fit)
        self._update_profile()

    def _update_profile(self) -> None:
        if self.processed_rgb is None or self.profile_path is None:
            return
        try:
            distances, values = sample_rgb_profile(
                self.processed_rgb,
                self.profile_path[0],
                self.profile_path[1],
                self.profile_width_spin.value(),
            )
        except ValueError as exc:
            self.profile_label.setText(str(exc))
            self.profile_plot.clear()
            return
        self.profile_plot.set_profile(distances, values)

    def save_image(self) -> None:
        if self.processed_image.isNull():
            return
        default_name = "processed.png"
        if self.image_path is not None:
            default_name = f"{self.image_path.stem}_processed.png"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Save Processed Image",
            default_name,
            "PNG (*.png);;TIFF (*.tif *.tiff);;JPEG (*.jpg *.jpeg);;All Files (*)",
        )
        if path and not self.processed_image.save(path):
            QMessageBox.warning(self, "Cannot Save Image", f"Could not save image: {path}")
