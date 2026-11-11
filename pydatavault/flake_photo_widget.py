"""Photo library with manual substrate/flake polygons and local thickness estimates."""

from __future__ import annotations

from pathlib import Path
import json
import sqlite3

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .flake_analysis import FlakeAnalysisService
from .flake_imaging import (MAX_REGIONS, decode_image, polygon_mask,
                            region_boundaries, substrate_reference)
from .flake_photo_store import FlakePhotoStore
from .flake_training import CHANNEL_OPTIONS


class _JobSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _ImageJob(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = _JobSignals()

    def run(self):
        try:
            self.signals.done.emit(self.operation())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class RegionCanvas(QGraphicsView):
    picked = Signal(int)
    polygon_finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pixmap = self.scene().addPixmap(QPixmap())
        pen = QPen(QColor("#ffffff"), 2)
        pen.setCosmetic(True)
        self.outline = self.scene().addPolygon(QPolygonF(), pen)
        self.outline.setZValue(2)
        self.labels = None
        self.mode = "Select / pan"
        self.points = []
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setBackgroundBrush(QColor("#141b25"))
        self.setMinimumSize(460, 400)

    def set_mode(self, mode):
        self.mode = mode
        self.cancel_polygon()
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "Select / pan" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if mode == "Select / pan" else Qt.CrossCursor)

    def cancel_polygon(self):
        self.points = []
        self.outline.setPolygon(QPolygonF())

    def finish_polygon(self):
        if len(self.points) >= 3:
            points = [(p.x(), p.y()) for p in self.points]
            self.cancel_polygon()
            self.polygon_finished.emit(points)

    def display(self, rgb, labels, *, fit=False, scale=1):
        self.labels = labels
        data = np.ascontiguousarray(rgb, dtype=np.uint8)
        image = QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_RGB888).copy()
        self.pixmap.setPixmap(QPixmap.fromImage(image))
        self.pixmap.setScale(scale)
        self.scene().setSceneRect(QRectF(0, 0, labels.shape[1], labels.shape[0]))
        if fit:
            self.fitInView(self.pixmap, Qt.KeepAspectRatio)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if 0.01 < self.transform().m11() * factor < 100:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if self.labels is None or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint())
        self.setFocus()
        if self.mode != "Select / pan":
            if not (0 <= point.x() <= self.labels.shape[1] - 1 and 0 <= point.y() <= self.labels.shape[0] - 1):
                return
            if len(self.points) < 512 and (not self.points or self.points[-1] != point):
                self.points.append(point)
            self.outline.setPolygon(QPolygonF(self.points))
        else:
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self.labels.shape[1] and 0 <= y < self.labels.shape[0]:
                self.picked.emit(int(self.labels[y, x]))
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if self.mode != "Select / pan" and self.points:
            self.outline.setPolygon(QPolygonF(self.points + [point]))
        else:
            super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.mode != "Select / pan" and event.button() == Qt.LeftButton:
            self.finish_polygon()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if self.mode != "Select / pan":
            if event.key() == Qt.Key_Escape:
                self.cancel_polygon()
                event.accept()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.finish_polygon()
                event.accept()
                return
            if event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
                self.points = self.points[:-1]
                self.outline.setPolygon(QPolygonF(self.points))
                event.accept()
                return
        super().keyPressEvent(event)


class FlakePhotoLibraryDialog(QDialog):
    def __init__(self, parent=None, *, store=None):
        super().__init__(parent)
        self.store = store or FlakePhotoStore()
        self.service = FlakeAnalysisService(store=self.store)
        self.original = None
        self.rgb = self.labels = self.substrate_mask = None
        self.photo_id = self.revision = self.profile_id = None
        self.source_name = ""
        self.photo_metadata = {}
        self.annotations = {}
        self.predictions = {}
        self.selected_region = None
        self._reference = None
        self.history = []
        self.dirty = False
        self.busy = False
        self.setWindowTitle("Flake Photo Library — Manual Thickness")
        self.resize(1400, 900)
        layout = QVBoxLayout(self)
        splitter = QSplitter()
        layout.addWidget(splitter)
        controls = QWidget()
        side = QVBoxLayout(controls)
        self.profile_combo = QComboBox()
        self.profile_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(20)
        side.addWidget(QLabel("Material / substrate / optical setup"))
        side.addWidget(self.profile_combo)
        self._button(side, "New profile…", self.new_profile)
        self.model_info = QLabel()
        self.model_info.setWordWrap(True)
        side.addWidget(self.model_info)
        self.photos = QListWidget()
        self.photos.setMaximumHeight(140)
        self.photos.itemDoubleClicked.connect(self.open_saved)
        side.addWidget(self.photos)
        self._button(side, "Import photo…", self.open_image)
        self._button(side, "Export saved original…", self.export_original)
        saved_hint = QLabel("Double-click a saved photo to revise its annotations.")
        saved_hint.setWordWrap(True)
        side.addWidget(saved_hint)
        side.addWidget(QLabel("Flake / training group (optional)"))
        self.training_group = QLineEdit()
        self.training_group.setMaxLength(200)
        self.training_group.setPlaceholderText("Same flake / repeated captures → same group")
        self.training_group.setToolTip("Use the same group for repeated photos of one physical flake. "
                                      "Blank treats each different image as independent.")
        self.training_group.textEdited.connect(self._changed)
        side.addWidget(self.training_group)
        side.addWidget(QLabel("Training channels"))
        self.training_channels = QComboBox()
        for channels in CHANNEL_OPTIONS:
            self.training_channels.addItem("Auto (single target uses RGB)" if channels == "auto" else channels, channels)
        side.addWidget(self.training_channels)
        self._button(side, "Export trained parameters…", self.export_model)
        training_hint = QLabel("Train from saved labeled photos: at least 3 independent groups per thickness. "
                               "One thickness learns target similarity only; other thicknesses remain untested.")
        training_hint.setWordWrap(True)
        side.addWidget(training_hint)

        self.mode = QComboBox()
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode.setMinimumContentsLength(20)
        self.mode.addItems(["Select / pan", "Draw substrate polygon", "Draw flake polygon", "Redraw selected flake"])
        steps = QLabel("1. Draw clean substrate; 2. Draw each flake region")
        steps.setWordWrap(True)
        side.addWidget(steps)
        side.addWidget(self.mode)
        self._button(side, "Draw substrate polygon", lambda: self.mode.setCurrentText("Draw substrate polygon"))
        self._button(side, "Draw a new flake region", lambda: self.mode.setCurrentText("Draw flake polygon"))
        self._button(side, "Redraw selected region", lambda: self.mode.setCurrentText("Redraw selected flake"))
        self._button(side, "Remove selected regions", self.remove_regions)
        self._button(side, "Clear substrate reference", self.clear_background)
        self._button(side, "Undo edit", self.undo)
        self.background_info = QLabel("Select clean silicon/oxide background with a polygon.")
        self.background_info.setWordWrap(True)
        side.addWidget(self.background_info)
        self.display_mode = QComboBox()
        self.display_mode.addItems(["Original + regions", "Normalized + regions", "Original only"])
        self.display_mode.currentIndexChanged.connect(self.redraw)
        side.addWidget(self.display_mode)
        self._button(side, "Fit photo", lambda: self.canvas.fitInView(self.canvas.pixmap, Qt.KeepAspectRatio))
        hint = QLabel("Click vertices; double-click or Enter to finish. Backspace removes the last "
                      "vertex; Esc cancels. Add multiple substrate polygons if needed. "
                      "Known layers/nm and label source are only needed to save reference data. "
                      "Predictions do not become reference labels.")
        hint.setWordWrap(True)
        side.addWidget(hint)
        limits = QLabel("Normalization compensates overall brightness and RGB channel gains relative "
                        "to the selected substrate. Clipping, gamma, uneven illumination and spectral "
                        "changes can still affect thickness estimates; use matching optical setups.")
        limits.setWordWrap(True)
        side.addWidget(limits)
        side.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(350)
        splitter.addWidget(scroll)
        workspace = QWidget()
        right = QVBoxLayout(workspace)
        self.canvas = RegionCanvas()
        right.addWidget(self.canvas, 3)
        actions = QHBoxLayout()
        self.predict_button = self._button(actions, "3. Estimate thickness", self.predict)
        self._button(actions, "Save as labeled reference photo", self.save)
        self.train_button = self._button(actions, "Train / update profile", self.train_profile)
        actions.addStretch()
        right.addLayout(actions)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Region", "Area px", "Known layers", "Known nm", "Label source", "Estimate / status", "Relative RGB %"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.cellChanged.connect(self._table_changed)
        self.table.itemSelectionChanged.connect(self._table_selection)
        right.addWidget(self.table, 1)
        self.status = QLabel("Import a photo, draw the substrate and flake regions, then estimate thickness.")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        splitter.addWidget(workspace)
        splitter.setSizes([350, 1050])
        self.mode.currentTextChanged.connect(self.canvas.set_mode)
        self.canvas.picked.connect(self.pick_region)
        self.canvas.polygon_finished.connect(self.add_polygon)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.refresh_profiles()
        self._update_prediction_button()

    @staticmethod
    def _button(layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def error(self, exc):
        QMessageBox.warning(self, "Flake Photo Library", str(exc))

    def run_task(self, message, operation, callback):
        """Keep the event loop responsive while image analysis runs on a worker."""
        self.busy = True
        self.setEnabled(False)
        self.status.setText(message)
        self._task_callback = callback
        self._job = _ImageJob(operation)
        self._job.signals.done.connect(self._finish_task)
        self._job.signals.failed.connect(self._fail_task)
        QThreadPool.globalInstance().start(self._job)

    def _finish_task(self, result):
        self.busy = False
        self.setEnabled(True)
        try:
            self._task_callback(result)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error(exc)
        self._update_prediction_button()

    def _fail_task(self, message):
        self.busy = False
        self.setEnabled(True)
        self.status.setText("Operation failed; the previous photo is still available.")
        self.error(message)
        self._update_prediction_button()

    def _changed(self, *args):
        if self.rgb is not None:
            self.dirty = True
            self.predictions = {}
            # Stale predictions must not survive mask/background changes.
            for row in range(self.table.rowCount()):
                for column in (5, 6):
                    item = self.table.item(row, column)
                    if item:
                        item.setText("")
                        item.setToolTip("")
            self.status.setText("Selections or labels changed. Estimate thickness to refresh results.")
            self._update_prediction_button()

    def _update_prediction_button(self):
        self.train_button.setEnabled(not self.busy and self.profile_id is not None)
        self.predict_button.setEnabled(not self.busy and self.profile_id is not None
            and self._reference is not None and self.labels is not None and bool(np.any(self.labels > 0)))

    def _refresh_background_info(self):
        self._reference = None
        if self.rgb is not None:
            try:
                reference, warnings = substrate_reference(self.rgb, self.substrate_mask)
                self._reference = reference
                raw = ", ".join(f"{v:.1f}" for v in reference)
                scale = ", ".join(f"{v:.3f}" for v in 100.0 / reference)
                self.background_info.setText(f"Substrate RGB: {raw}\nChannel scales: {scale}\n"
                    "Each substrate channel = 100%." + ("\nReview: " + ", ".join(warnings) if warnings else ""))
            except ValueError as exc:
                self.background_info.setText(str(exc))
        self._update_prediction_button()

    def discard_changes(self):
        return not self.dirty or QMessageBox.question(self, "Unsaved photo", "Discard unsaved photo/annotation changes?",
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Discard

    def closeEvent(self, event):
        if not self.busy and self.discard_changes():
            event.accept()
        else:
            event.ignore()

    def reject(self):
        if self.canvas.points:
            self.canvas.cancel_polygon()
            return
        if not self.busy and self.discard_changes():
            super().reject()

    def refresh_profiles(self, selected=None):
        selected = selected if selected is not None else self.profile_id
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.store.list_profiles():
            self.profile_combo.addItem(f"{profile['material']} | {profile['substrate']} | {profile['optical_setup']}", profile["profile_id"])
        index = self.profile_combo.findData(selected)
        self.profile_combo.setCurrentIndex(max(0, index))
        self.profile_combo.blockSignals(False)
        self.profile_id = self.profile_combo.currentData()
        self.refresh_photos()
        self._update_prediction_button()

    def _profile_changed(self):
        if not self.discard_changes():
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(self.profile_id))
            self.profile_combo.blockSignals(False)
            return
        self.profile_id = self.profile_combo.currentData()
        self.photo_id = self.revision = None
        self.annotations = {}
        self.predictions = {}
        self.history = []
        self.selected_region = None
        self.canvas.cancel_polygon()
        self._changed()
        self.refresh_table()
        self.redraw()
        self.refresh_photos()
        self._update_prediction_button()

    def refresh_photos(self):
        self.photos.clear()
        if self.profile_id is not None:
            for photo in self.store.list_photos(self.profile_id):
                item = QListWidgetItem(f"#{photo['photo_id']} {photo['source_name']} · {photo['region_count']} regions · rev {photo['revision']}")
                item.setData(Qt.UserRole, photo["photo_id"])
                self.photos.addItem(item)
        self.refresh_model_info()

    def refresh_model_info(self):
        model = self.store.get_model(self.profile_id) if self.profile_id is not None else None
        if model is None:
            self.model_info.setText("No trained parameters. Save references, then train this profile. "
                                    "Estimation currently uses the reference baseline.")
            return
        state = "STALE — retrain after reference changes" if model["stale"] else "Current"
        validation = model["training"]["validation"]
        detail = ("Target similarity only; specificity untested" if model["mode"] == "single_target"
                  else f"Training CV accuracy: {validation['balanced_accuracy']:.0%} (not an independent test)")
        self.model_info.setText(f"Model #{model['model_id']} / rev {model['revision']} · {state}\n"
            f"Channels: {''.join(model['channels'])} · {model['training']['group_count']} groups\n{detail}")
        self.model_info.setToolTip("\n".join(model["training"]["diagnostics"]))

    def train_profile(self):
        if self.profile_id is None:
            self.error("Create/select a profile and save labeled reference photos first")
            return
        profile_id, channels = self.profile_id, self.training_channels.currentData()
        self.run_task("Training from saved reference photos (current unsaved edits are excluded)…",
                      lambda: self.service.train(profile_id, channels=channels), self._trained)

    def _trained(self, model):
        self.predictions = {}
        self.refresh_table()
        self.refresh_model_info()
        self.status.setText(f"Saved model #{model['model_id']}, revision {model['revision']}; "
            f"channels {''.join(model['channels'])}. "
            + ("Single target: similarity only; other thicknesses have not been tested. " if model["mode"] == "single_target" else "")
            + "Diagnostics: " + (", ".join(model["training"]["diagnostics"]) or "none")
            + ". Estimate thickness to use these parameters.")

    def export_model(self):
        model = self.store.get_model(self.profile_id) if self.profile_id is not None else None
        if model is None:
            self.error("Train this profile before exporting parameters")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export trained parameters", f"flake-model-{model['model_id']}.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            except OSError as exc:
                self.error(exc)

    def new_profile(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("New photo profile")
        form = QFormLayout(dialog)
        material, substrate, optics = QLineEdit(), QLineEdit("285 nm SiO2/Si"), QLineEdit()
        optics.setPlaceholderText("Microscope / objective / illumination / camera mode")
        for title, widget in [("Material", material), ("Substrate / oxide thickness", substrate), ("Optical setup", optics)]:
            form.addRow(title, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            try:
                new_id = self.store.ensure_profile(material.text(), substrate.text(), optics.text())
                # Preserve imported data; selecting a profile should not silently relabel saved samples.
                if self.discard_changes():
                    self.refresh_profiles(new_id)
                    self.photo_id = self.revision = None
                    self.annotations = {}
                    self.history = []
                    self._changed()
                    self.refresh_table()
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.error(exc)

    def open_image(self):
        if not self.discard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import microscope photo", "", "Photos (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if path:
            self.run_task("Loading photo for manual selection…", lambda: self.prepare_image(path), self._load_prepared)

    def load_image(self, path):
        self._load_prepared(self.prepare_image(path))

    @staticmethod
    def prepare_image(path):
        from .flake_imaging import MAX_IMAGE_BYTES
        if Path(path).stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds 32 MiB")
        data = Path(path).read_bytes()
        rgb = decode_image(data)
        return data, rgb, Path(path).name

    def _load_prepared(self, prepared):
        data, rgb, source_name = prepared
        self.original, self.rgb, self.source_name = data, rgb, source_name
        self.photo_metadata = {}
        self.training_group.clear()
        self.photo_id = self.revision = None
        self.annotations, self.predictions, self.history = {}, {}, []
        self.labels = np.zeros(rgb.shape[:2], dtype=np.int32)
        self.substrate_mask = np.zeros(rgb.shape[:2], dtype=bool)
        self.selected_region = None
        self.dirty = False
        self.mode.setCurrentText("Draw substrate polygon")
        self.canvas.cancel_polygon()
        self._refresh_background_info()
        self.refresh_table()
        self.redraw(fit=True)
        self.status.setText("Draw clean silicon/oxide substrate, then each flake region. Double-click or Enter finishes a polygon.")

    def open_saved(self, item):
        if not self.discard_changes():
            return
        try:
            photo = self.store.load_photo(item.data(Qt.UserRole))
            self.original, self.rgb = photo["original"], photo["rgb"]
            self.source_name = photo["source_name"]
            self.photo_id, self.revision = photo["photo_id"], photo["revision"]
            self.labels, self.substrate_mask = photo["labels"], photo["substrate_mask"]
            self.annotations, self.predictions, self.history = photo["annotations"], {}, []
            self.photo_metadata = dict(photo["metadata"])
            self.training_group.setText(self.photo_metadata.get("training_group", ""))
            self.selected_region = None
            self.mode.setCurrentIndex(0)
            self.canvas.cancel_polygon()
            self._refresh_background_info()
            self.refresh_table()
            self.redraw(fit=True)
            self.dirty = False
            self.status.setText(f"Loaded photo #{self.photo_id}, revision {self.revision}. Masks and thickness annotations are editable.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error(exc)

    def export_original(self):
        if self.original is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export original photo", self.source_name)
        if path:
            try:
                Path(path).write_bytes(self.original)
            except OSError as exc:
                self.error(exc)

    def remember(self):
        if self.labels is not None:
            self.history.append((self.labels.copy(), self.substrate_mask.copy(), {k: dict(v) for k, v in self.annotations.items()}))
            # At least one full-resolution undo; bound further history memory.
            while len(self.history) > 1 and (len(self.history) > 8 or sum(h[0].nbytes + h[1].nbytes for h in self.history) > 64 * 1024 * 1024):
                self.history.pop(0)

    def undo(self):
        if self.history:
            self.labels, self.substrate_mask, self.annotations = self.history.pop()
            self.canvas.cancel_polygon()
            self._refresh_background_info()
            self._changed()
            self.refresh_table()
            self.redraw()

    def add_polygon(self, points):
        if self.rgb is None:
            return
        try:
            mask = polygon_mask(self.labels.shape, points)
            if mask.sum() < 9:
                raise ValueError("Select at least 9 pixels in each polygon")
            mode = self.mode.currentText()
            if mode == "Draw substrate polygon":
                if np.any(mask & (self.labels > 0)):
                    raise ValueError("The substrate polygon overlaps a flake region")
                background = self.substrate_mask | mask
                substrate_reference(self.rgb, background)
                self.remember()
                self.substrate_mask = background
                self.labels[mask] = 0
                self._refresh_background_info()
            elif mode in ("Draw flake polygon", "Redraw selected flake"):
                ids = set(int(i) for i in np.unique(self.labels[self.labels > 0]))
                if mode == "Redraw selected flake":
                    if self.selected_region not in ids:
                        raise ValueError("Select a flake in the image or table before redrawing it")
                    region_id = self.selected_region
                else:
                    if len(ids) >= MAX_REGIONS:
                        raise ValueError(f"At most {MAX_REGIONS} regions are supported")
                    region_id = next(i for i in range(1, MAX_REGIONS + 1) if i not in ids)
                if np.any(mask & self.substrate_mask):
                    raise ValueError("The flake polygon overlaps the selected substrate")
                if np.any(mask & (self.labels > 0) & (self.labels != region_id)):
                    raise ValueError("The flake polygon overlaps another region; redraw each thickness separately")
                self.remember()
                self.labels[self.labels == region_id] = 0
                self.labels[mask] = region_id
                # A redrawn region may represent a different thickness.
                self.annotations.pop(region_id, None)
                self.selected_region = region_id
            else:
                return
            self._changed()
            self.refresh_table()
            self.redraw()
            self.mode.setCurrentIndex(0)
        except ValueError as exc:
            self.error(exc)

    def clear_background(self):
        if self.substrate_mask is not None and self.substrate_mask.any():
            self.canvas.cancel_polygon()
            self.remember()
            self.substrate_mask[:] = False
            self._refresh_background_info()
            self._changed()
            self.redraw()

    def selected_ids(self):
        return [int(self.table.item(index.row(), 0).text()) for index in self.table.selectionModel().selectedRows()]

    def remove_regions(self):
        ids = self.selected_ids()
        if ids:
            self.canvas.cancel_polygon()
            self.remember()
            self.labels[np.isin(self.labels, ids)] = 0
            self._changed()
            self.refresh_table()
            self.redraw()

    def pick_region(self, region_id):
        for row in range(self.table.rowCount()):
            if int(self.table.item(row, 0).text()) == region_id:
                self.table.selectRow(row)
                break

    def _table_selection(self):
        ids = self.selected_ids()
        self.selected_region = ids[0] if ids else None
        self.redraw()

    def _table_changed(self, row, column):
        if column not in (2, 3, 4):
            return
        region_id = int(self.table.item(row, 0).text())
        self.annotations[region_id] = {"layers": self.table.item(row, 2).text(),
                                       "thickness_nm": self.table.item(row, 3).text(),
                                       "provenance": self.table.item(row, 4).text()}
        self._changed()

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.clearSelection()
        selected = self.selected_region
        ids, counts = (np.unique(self.labels[self.labels > 0], return_counts=True) if self.labels is not None else ([], []))
        self.table.setRowCount(len(ids))
        for row, (region_id, area) in enumerate(zip(ids, counts)):
            annotation = self.annotations.get(int(region_id), {})
            prediction = self.predictions.get(int(region_id), {})
            estimate = self.prediction_text(prediction)
            values = [region_id, area, annotation.get("layers"), annotation.get("thickness_nm"), annotation.get("provenance", ""),
                      estimate, ", ".join(f"{v:.2f}" for v in prediction["normalized_rgb"]) if prediction else ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                if column in (0, 1, 5, 6):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if prediction and column == 5:
                    item.setToolTip(f"{self.prediction_reason(prediction['status'])}\n"
                        f"Reference photos: {prediction['reference_photos']}\n"
                        f"Distance: {prediction['distance']}; margin: {prediction['margin']}\n"
                        f"Region quality: {', '.join(prediction['warnings']) or 'no flags'}")
                self.table.setItem(row, column, item)
            if int(region_id) == selected:
                self.table.selectRow(row)
        self.annotations = {int(i): self.annotations[int(i)] for i in ids if int(i) in self.annotations}
        self.selected_region = selected if selected in ids else None
        self.table.blockSignals(False)

    @staticmethod
    def prediction_reason(status):
        return {"match": "Within calibrated range (heuristic match)",
                "no_reference": "No usable reference photos for this profile",
                "insufficient_reference": "Need at least 3 other photos per candidate and 2 calibrated thickness classes",
                "ambiguous": "Several calibrated thicknesses have similar colors",
                "out_of_distribution": "Color is outside the calibrated range",
                "needs_review": "Image quality or model training diagnostics need review",
                "target_like": "Similar to the trained target thickness; other thicknesses have not been ruled out",
                "training_sample": "This photo/group contributed to training; this is not an independent prediction"}[status]

    @staticmethod
    def prediction_text(prediction):
        if not prediction:
            return ""
        if prediction["status"] == "no_reference":
            return "No reference data"
        values = []
        if prediction["candidate_layers"] is not None:
            values.append(f"{prediction['candidate_layers']} L")
        if prediction["candidate_thickness_nm"] is not None:
            values.append(f"{prediction['candidate_thickness_nm']:g} nm")
        estimate = " / ".join(values)
        if prediction["status"] == "target_like":
            return f"Target-like {estimate} — unconfirmed"
        return f"{estimate} — match" if prediction["status"] == "match" else f"Candidate {estimate} — {prediction['status']}"

    def redraw(self, *args, fit=False):
        if self.rgb is None:
            return
        stride = max(1, int(np.ceil(max(self.labels.shape) / 1600)))
        view_labels = self.labels[::stride, ::stride]
        view_substrate = self.substrate_mask[::stride, ::stride]
        image = self.rgb[::stride, ::stride].astype(np.float32)
        if self.display_mode.currentIndex() == 1 and self._reference is not None:
            image = np.clip(image * (160.0 / self._reference), 0, 255)
        if self.display_mode.currentIndex() != 2:
            mask = view_labels > 0
            ids = view_labels[mask]
            colors = np.column_stack(((ids * 71) % 190 + 50, (ids * 113) % 190 + 50, (ids * 151) % 190 + 50))
            image[mask] = image[mask] * 0.7 + colors * 0.3
            image[view_substrate] = image[view_substrate] * 0.65 + np.array([0, 220, 230]) * 0.35
            image[region_boundaries(view_labels)] = (255, 240, 70)
            if self.selected_region is not None:
                image[region_boundaries((view_labels == self.selected_region).astype(np.int32))] = (255, 255, 255)
        self.canvas.display(image.astype(np.uint8), self.labels, fit=fit, scale=stride)

    def predict(self):
        if self.rgb is None or self.profile_id is None:
            return
        if self.canvas.points:
            self.error("Finish the polygon with Enter, or cancel it with Esc, before estimating")
            return
        if self._reference is None or not np.any(self.labels > 0):
            self.error("Draw clean substrate and at least one flake region first")
            return
        group = self.training_group.text()
        self.run_task("Comparing with reference photos…", lambda: self.service.analyze(
            self.rgb, self.profile_id, labels=self.labels, substrate_mask=self.substrate_mask,
            training_group_id=group), self._show_predictions)

    def _show_predictions(self, result):
        self.predictions = {r["region_id"]: r for r in result["regions"]}
        self.refresh_table()
        states = sorted({r["status"] for r in result["regions"]})
        self.status.setText(" ".join(self.prediction_reason(s) + "." for s in states)
            + (f" Model #{result['model_id']} / rev {result['model_revision']} · {''.join(result['channels'])}."
               if result.get("model_id") else " Reference baseline; current photo excluded from references.")
            + (" Background quality: " + ", ".join(result["warnings"]) if result["warnings"] else ""))

    def save(self):
        if self.rgb is None or self.profile_id is None:
            self.error("Create/select a profile and import a photo first")
            return
        if self.canvas.points:
            self.error("Finish or cancel the unfinished polygon before saving")
            return
        if self._reference is None:
            self.error("Draw a clean substrate reference before saving")
            return
        try:
            annotations = {}
            for region_id in np.unique(self.labels[self.labels > 0]):
                source = self.annotations.get(int(region_id), {})
                layers = str(source.get("layers") or "").strip()
                thickness = str(source.get("thickness_nm") or "").strip()
                annotations[int(region_id)] = {"layers": int(layers) if layers else None,
                    "thickness_nm": float(thickness) if thickness else None, "provenance": source.get("provenance", "")}
            metadata = {**self.photo_metadata, "selection_method": "manual_polygons", "substrate_confirmed": True,
                        "training_group": self.training_group.text().strip()}
            self.photo_id = self.store.save_photo(self.profile_id, self.original, self.source_name,
                self.labels, self.substrate_mask, annotations, metadata=metadata,
                photo_id=self.photo_id, expected_revision=self.revision)
            self.photo_metadata = metadata
            self.revision = 1 if self.revision is None else self.revision + 1
            self.dirty = False
            self.refresh_photos()
            self.status.setText(f"Saved photo #{self.photo_id}, revision {self.revision}. Original, masks, annotations and normalized pixels are stored together.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error(exc)
