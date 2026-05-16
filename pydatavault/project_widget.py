import logging
import json
import os
import shutil
import time
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QTableWidget, QTableWidgetItem, QSplitter, QDialog,
    QLabel, QLineEdit, QTextEdit, QComboBox, QSpinBox, QMessageBox,
    QDialogButtonBox, QFormLayout, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QFileDialog, QScrollArea, QGridLayout
)
from PySide6.QtCore import Qt, QDate, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtCore import QSize

from . import database as db
from . import config
from . import style

logger = logging.getLogger("PyOmnix")


class StatusDelegate(QStyledItemDelegate):
    """Delegate for editable Status combobox in device table."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(["planned", "fabricated", "measured", "retired"])
        return combo

    def setEditorData(self, editor, index):
        value = index.model().data(index, Qt.EditRole)
        editor.setCurrentText(value or "planned")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class DeviceTableWidget(QTableWidget):
    """Device table that emits row moves instead of mutating the model directly."""

    rows_reordered = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        source_row = self.currentRow()
        target_row = self._drop_target_row(event)
        if source_row < 0 or target_row < 0:
            event.ignore()
            return

        if target_row > source_row:
            target_row -= 1
        if source_row == target_row:
            event.accept()
            return

        self.rows_reordered.emit(source_row, target_row)
        event.accept()

    def _drop_target_row(self, event) -> int:
        if self.rowCount() == 0:
            return -1
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        row = self.rowAt(position.y())
        if row < 0:
            return self.rowCount()
        if self.dropIndicatorPosition() == QAbstractItemView.BelowItem:
            return row + 1
        if self.dropIndicatorPosition() == QAbstractItemView.OnViewport:
            return self.rowCount()
        return row


class DevicePhotoThumbnail(QLabel):
    """Thumbnail that opens a device assembly photo on double click."""

    def __init__(self, photo_path: str, parent=None):
        super().__init__(parent)
        self.photo_path = str(config.resolve_data_path(photo_path))
        self.setFixedSize(160, 120)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setToolTip(photo_path)

        pixmap = QPixmap(self.photo_path)
        if pixmap.isNull():
            self.setText(Path(photo_path).name)
        else:
            self.setPixmap(
                pixmap.scaled(152, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def mouseDoubleClickEvent(self, event):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.photo_path))


class DevicePhotoEditor(QWidget):
    """Editable note plus thumbnail for one device assembly photo."""

    def __init__(self, entry: dict, remove_callback, parent=None):
        super().__init__(parent)
        self.photo_path = entry.get("photo_path", "")
        self.note_edit = QLineEdit(entry.get("note", ""))
        self.note_edit.setPlaceholderText("Photo note")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addWidget(self.note_edit)
        layout.addWidget(DevicePhotoThumbnail(self.photo_path))

        remove_btn = QPushButton("Remove")
        style.decorate_button(remove_btn, "danger", "delete")
        remove_btn.clicked.connect(remove_callback)
        layout.addWidget(remove_btn)

    def get_entry(self) -> dict:
        return {
            "photo_path": self.photo_path,
            "note": self.note_edit.text().strip(),
        }


class DevicePhotosDialog(QDialog):
    """View and edit device assembly photos and per-photo notes."""

    def __init__(self, entries: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device Photos")
        self.setMinimumSize(560, 420)
        self._entries = [dict(entry) for entry in entries]
        self._photo_widgets: list[DevicePhotoEditor] = []

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        add_btn = QPushButton("Add Photos")
        style.decorate_button(add_btn, "utility", "photo")
        add_btn.clicked.connect(self.add_photos)
        controls.addWidget(add_btn)

        capture_btn = QPushButton("Screenshot")
        style.decorate_button(capture_btn, "utility", "photo")
        capture_btn.clicked.connect(self.capture_photo)
        controls.addWidget(capture_btn)
        controls.addStretch()
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_grid()

    def add_photos(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Device Photos", "", "Image Files (*.png *.jpg *.jpeg *.tiff)"
        )
        if file_paths:
            self._entries.extend({"photo_path": path, "note": ""} for path in file_paths)
            self._refresh_grid()

    def capture_photo(self):
        from .wafer_widget import capture_screen_region

        file_path = capture_screen_region(self)
        if file_path:
            self._entries.append({"photo_path": file_path, "note": ""})
            self._refresh_grid()

    def _refresh_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._photo_widgets = []
        for index, entry in enumerate(self._entries):
            editor = DevicePhotoEditor(
                entry,
                lambda checked=False, index=index: self._remove_entry(index),
            )
            self._photo_widgets.append(editor)
            self.grid.addWidget(editor, index // 3, index % 3)

    def _remove_entry(self, index: int):
        self._entries = self.get_photo_entries()
        if 0 <= index < len(self._entries):
            del self._entries[index]
        self._refresh_grid()

    def get_photo_entries(self) -> list[dict]:
        entries = []
        widgets = self._photo_widgets or []
        for widget in widgets:
            entry = widget.get_entry()
            if entry["photo_path"]:
                entries.append(entry)
        return entries


class ProjectWidget(QWidget):
    """Main widget for project and device management."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_project_id = None
        self.init_ui()
        self.load_projects()

    def init_ui(self):
        """Initialize the user interface."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Horizontal)

        left_panel = self.create_left_panel()
        right_panel = self.create_right_panel()

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter)
        self.setLayout(layout)

    def create_left_panel(self):
        """Create the left panel with project list."""
        panel = QWidget()
        style.decorate_panel(panel, "sidePanel")
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Projects")
        style.decorate_heading(title)
        layout.addWidget(title)

        self.project_list = QListWidget()
        style.decorate_list(self.project_list)
        self.project_list.itemSelectionChanged.connect(self.on_project_selected)
        layout.addWidget(self.project_list)

        btn_layout = QVBoxLayout()
        self.btn_new_project = QPushButton("New Project")
        style.decorate_button(self.btn_new_project, "primary", "plus")
        self.btn_new_project.clicked.connect(self.on_new_project)
        btn_layout.addWidget(self.btn_new_project)

        self.btn_edit_project = QPushButton("Edit Project")
        style.decorate_button(self.btn_edit_project, "neutral", "edit")
        self.btn_edit_project.clicked.connect(self.on_edit_project)
        btn_layout.addWidget(self.btn_edit_project)

        self.btn_delete_project = QPushButton("Delete Project")
        style.decorate_button(self.btn_delete_project, "danger", "delete")
        self.btn_delete_project.clicked.connect(self.on_delete_project)
        btn_layout.addWidget(self.btn_delete_project)

        layout.addLayout(btn_layout)
        panel.setLayout(layout)
        return panel

    def create_right_panel(self):
        """Create the right panel with device management."""
        panel = QWidget()
        style.decorate_panel(panel, "contentPanel")
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.project_header = QLabel("Select a project")
        style.decorate_heading(self.project_header)
        layout.addWidget(self.project_header)

        self.device_table = DeviceTableWidget()
        style.decorate_table(self.device_table)
        self.device_table.setColumnCount(8)
        self.device_table.setHorizontalHeaderLabels(
            ["Device ID", "Description", "Fab Date", "Status", "Layers", "Photos", "Meas Date", "Notes"]
        )
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.device_table.itemChanged.connect(self.on_device_cell_changed)
        self.device_table.cellDoubleClicked.connect(self.on_device_cell_double_clicked)
        self.device_table.rows_reordered.connect(self.move_device_display_row)

        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Stretch)

        layout.addWidget(self.device_table)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        self.btn_new_device = QPushButton("New Device")
        style.decorate_button(self.btn_new_device, "primary", "plus")
        self.btn_new_device.clicked.connect(self.on_new_device)
        btn_layout.addWidget(self.btn_new_device)

        self.btn_edit_device = QPushButton("Edit Device")
        style.decorate_button(self.btn_edit_device, "neutral", "edit")
        self.btn_edit_device.clicked.connect(self.on_edit_device)
        btn_layout.addWidget(self.btn_edit_device)

        self.btn_delete_device = QPushButton("Delete Device")
        style.decorate_button(self.btn_delete_device, "danger", "delete")
        self.btn_delete_device.clicked.connect(self.on_delete_device)
        btn_layout.addWidget(self.btn_delete_device)

        self.btn_fab_folder = QPushButton("Open Fab Folder")
        style.decorate_button(self.btn_fab_folder, "utility", "folder")
        self.btn_fab_folder.clicked.connect(self.on_open_fab_folder)
        btn_layout.addWidget(self.btn_fab_folder)

        self.btn_meas_folder = QPushButton("Open Meas Folder")
        style.decorate_button(self.btn_meas_folder, "utility", "folder")
        self.btn_meas_folder.clicked.connect(self.on_open_meas_folder)
        btn_layout.addWidget(self.btn_meas_folder)

        layout.addLayout(btn_layout)
        panel.setLayout(layout)
        return panel

    def load_projects(self):
        """Load all projects into the list."""
        self.project_list.clear()
        projects = db.get_all_projects()
        for project in projects:
            item = QListWidgetItem(project['name'])
            item.setData(Qt.UserRole, project['project_id'])
            self.project_list.addItem(item)

    def refresh(self):
        """Refresh the widget by reloading all projects and clearing selection."""
        self.load_projects()
        self.device_table.setRowCount(0)
        self.project_header.setText("Select a project")

    def on_project_selected(self):
        """Handle project selection."""
        item = self.project_list.currentItem()
        if not item:
            return

        project_id = item.data(Qt.UserRole)
        project = db.get_project(project_id)

        self.current_project_id = project_id
        self.project_header.setText(
            f"<b>{project['name']}</b> - {project['description']}"
        )

        self.load_devices(project_id)

    def load_devices(self, project_id):
        """Load devices for the selected project."""
        signals_blocked = self.device_table.blockSignals(True)
        try:
            self.device_table.setRowCount(0)

            devices = self._apply_device_display_order(
                project_id,
                db.get_project_device_summary(project_id),
            )

            for row, device in enumerate(devices):
                self.device_table.insertRow(row)

                id_item = QTableWidgetItem(device['device_id'])
                id_item.setData(Qt.UserRole, device['device_id'])
                self.device_table.setItem(row, 0, id_item)
                self.device_table.setItem(row, 1, QTableWidgetItem(device['description'] or ""))
                self.device_table.setItem(row, 2, QTableWidgetItem(device['fab_date'] or ""))

                status_item = QTableWidgetItem(device['status'] or "planned")
                style.decorate_status_item(status_item, device['status'])
                self.device_table.setItem(row, 3, status_item)

                layer_count = device.get('layer_count', 0)
                self.device_table.setItem(row, 4, QTableWidgetItem(str(layer_count)))

                photo_entries = self._device_photo_entries(device)
                photo_item = QTableWidgetItem(self._device_photo_summary(photo_entries))
                photo_item.setFlags(photo_item.flags() & ~Qt.ItemIsEditable)
                self.device_table.setItem(row, 5, photo_item)

                self.device_table.setItem(row, 6, QTableWidgetItem(device.get('meas_date') or ""))
                self.device_table.setItem(row, 7, QTableWidgetItem(device.get('notes') or ""))
        finally:
            self.device_table.blockSignals(signals_blocked)

    def _apply_device_display_order(self, project_id: str, devices: list[dict]) -> list[dict]:
        preferences = config.load_preferences()
        order_map = preferences.get("device_display_order")
        if not isinstance(order_map, dict):
            return devices

        stored_order = order_map.get(project_id)
        if not isinstance(stored_order, list):
            return devices

        remaining = {device["device_id"]: device for device in devices}
        ordered = []
        for device_id in stored_order:
            if isinstance(device_id, str) and device_id in remaining:
                ordered.append(remaining.pop(device_id))

        ordered.extend(device for device in devices if device["device_id"] in remaining)
        return ordered

    def move_device_display_row(self, source_row: int, target_row: int):
        """Move one displayed device row and persist only the GUI display order."""
        if not self.current_project_id:
            return
        row_count = self.device_table.rowCount()
        if source_row < 0 or source_row >= row_count or row_count == 0:
            return

        target_row = max(0, min(target_row, row_count - 1))
        if source_row == target_row:
            return

        order = self._current_device_display_order()
        moved_device_id = order.pop(source_row)
        order.insert(target_row, moved_device_id)
        self._save_device_display_order(self.current_project_id, order)
        self.load_devices(self.current_project_id)
        self.device_table.clearSelection()
        QTimer.singleShot(0, lambda: self._select_device_display_row(moved_device_id))

    def _select_device_display_row(self, device_id: str):
        for row in range(self.device_table.rowCount()):
            if ProjectWidget._device_id_from_table(self.device_table, row) == device_id:
                self.device_table.selectRow(row)
                return

    def _current_device_display_order(self) -> list[str]:
        return [
            ProjectWidget._device_id_from_table(self.device_table, row)
            for row in range(self.device_table.rowCount())
            if ProjectWidget._device_id_from_table(self.device_table, row)
        ]

    @staticmethod
    def _save_device_display_order(project_id: str, order: list[str]):
        preferences = config.load_preferences()
        order_map = preferences.get("device_display_order")
        if not isinstance(order_map, dict):
            order_map = {}
        order_map[project_id] = order
        preferences["device_display_order"] = order_map
        config.save_preferences(preferences)

    @staticmethod
    def _replace_device_in_display_order(project_id: str, old_device_id: str, new_device_id: str):
        preferences = config.load_preferences()
        order_map = preferences.get("device_display_order")
        if not isinstance(order_map, dict):
            return
        order = order_map.get(project_id)
        if not isinstance(order, list) or old_device_id not in order:
            return
        order_map[project_id] = [
            new_device_id if device_id == old_device_id else device_id
            for device_id in order
        ]
        preferences["device_display_order"] = order_map
        config.save_preferences(preferences)

    def _device_id_for_row(self, row: int) -> str | None:
        return ProjectWidget._device_id_from_table(self.device_table, row)

    @staticmethod
    def _device_id_from_table(table, row: int) -> str | None:
        item = table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.UserRole) if hasattr(item, "data") else None
        return data or item.text()

    def on_device_cell_changed(self, item):
        """Save device changes when cell is edited."""
        if not hasattr(self, 'current_project_id'):
            return

        row = item.row()
        device_id = ProjectWidget._device_id_from_table(self.device_table, row)
        if not device_id:
            return

        col = item.column()
        col_name = self.device_table.horizontalHeaderItem(col).text()

        if col_name == "Device ID":
            new_device_id = item.text().strip()
            if not new_device_id:
                QMessageBox.warning(self, "Validation", "Device ID is required")
                self.load_devices(self.current_project_id)
                return
            if new_device_id == device_id:
                return
            if db.get_device(new_device_id) is not None:
                QMessageBox.warning(
                    self,
                    "Validation",
                    f"Device ID '{new_device_id}' already exists",
                )
                self.load_devices(self.current_project_id)
                return
            try:
                self._rename_device_project_artifacts(
                    self.current_project_id,
                    device_id,
                    new_device_id,
                )
                db.rename_device(device_id, new_device_id)
                self._replace_device_in_display_order(
                    self.current_project_id,
                    device_id,
                    new_device_id,
                )
                ProjectWidget.write_used_flakes_index(
                    self.current_project_id,
                    new_device_id,
                    db.get_device_layers(new_device_id),
                )
                self.load_devices(self.current_project_id)
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "Device Folder Warning",
                    "The device ID was not updated because the project folders "
                    f"could not be renamed:\n{exc}",
                )
                self.load_devices(self.current_project_id)
            except ValueError as e:
                QMessageBox.warning(self, "Validation", str(e))
                self.load_devices(self.current_project_id)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update device ID: {str(e)}")
                self.load_devices(self.current_project_id)
            return

        col_to_field = {
            "Description": "description",
            "Fab Date": "fab_date",
            "Status": "status",
            "Meas Date": "meas_date",
            "Notes": "notes"
        }

        if col_name in col_to_field:
            field_name = col_to_field[col_name]
            value = item.text()

            try:
                db.update_device(device_id, **{field_name: value})
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update device: {str(e)}")
                self.load_devices(self.current_project_id)

    def on_device_cell_double_clicked(self, row: int, col: int):
        """Open the device photos editor when the Photos column is double-clicked."""
        if not hasattr(self, 'current_project_id'):
            return
        header_item = self.device_table.horizontalHeaderItem(col)
        if header_item is None or header_item.text() != "Photos":
            return

        device_id = ProjectWidget._device_id_from_table(self.device_table, row)
        if not device_id:
            return
        device = db.get_device(device_id)
        if device is None:
            QMessageBox.warning(self, "Warning", "Selected device no longer exists")
            self.load_devices(self.current_project_id)
            return

        entries = self._device_photo_entries(device)
        dialog = DevicePhotosDialog(entries, self)
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            stored_entries = self._copy_device_photo_entries(
                self.current_project_id,
                device_id,
                dialog.get_photo_entries(),
            )
            db.update_device(
                device_id,
                assembly_photos=json.dumps(stored_entries, ensure_ascii=False),
            )
            self.load_devices(self.current_project_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update device photos: {str(e)}")

    @staticmethod
    def _rename_device_project_artifacts(project_id: str, old_device_id: str, new_device_id: str):
        old_fab = config.PROJECTS_DIR / project_id / "fabrication" / old_device_id
        new_fab = config.PROJECTS_DIR / project_id / "fabrication" / new_device_id
        old_meas_target = config.PYFLEXLAB_OUT_PATH / old_device_id
        new_meas_target = config.PYFLEXLAB_OUT_PATH / new_device_id
        old_meas_link = config.PROJECTS_DIR / project_id / "measurements" / old_device_id
        new_meas_link = config.PROJECTS_DIR / project_id / "measurements" / new_device_id

        for old_path, new_path in (
            (old_fab, new_fab),
            (old_meas_target, new_meas_target),
            (old_meas_link, new_meas_link),
        ):
            if (old_path.exists() or old_path.is_symlink()) and (
                new_path.exists() or new_path.is_symlink()
            ):
                raise FileExistsError(f"Target path already exists: {new_path}")

        ProjectWidget._rename_path_if_present(old_fab, new_fab)
        ProjectWidget._rename_path_if_present(old_meas_target, new_meas_target)

        if old_meas_link.is_symlink():
            new_meas_link.parent.mkdir(parents=True, exist_ok=True)
            old_meas_link.unlink()
            os.symlink(new_meas_target, new_meas_link, target_is_directory=True)
        else:
            ProjectWidget._rename_path_if_present(old_meas_link, new_meas_link)

    @staticmethod
    def _rename_path_if_present(old_path: Path, new_path: Path):
        if not old_path.exists() and not old_path.is_symlink():
            return
        if new_path.exists() or new_path.is_symlink():
            raise FileExistsError(f"Target path already exists: {new_path}")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)

    def _device_photo_entries(self, device: dict) -> list[dict]:
        """Return device assembly photo entries stored as JSON."""
        raw_entries = device.get("assembly_photos")
        if raw_entries in (None, "", "[]"):
            return []
        try:
            entries = json.loads(raw_entries)
        except (TypeError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self,
                "Device Photos Error",
                "Stored device photos data is not valid JSON. "
                f"The device photo list cannot be shown.\n\n{exc}",
            )
            return []
        if not isinstance(entries, list):
            QMessageBox.warning(
                self,
                "Device Photos Error",
                "Stored device photos data is not a JSON list. "
                "The device photo list cannot be shown.",
            )
            return []

        normalized = []
        for entry in entries:
            if isinstance(entry, str):
                normalized.append({"photo_path": entry, "note": ""})
            elif isinstance(entry, dict) and entry.get("photo_path"):
                normalized.append({
                    "photo_path": entry.get("photo_path", ""),
                    "note": entry.get("note", ""),
                })
            else:
                QMessageBox.warning(
                    self,
                    "Device Photos Error",
                    "Stored device photos data contains an invalid entry. "
                    "The device photo list cannot be shown.",
                )
                return []
        return normalized

    @staticmethod
    def _device_photo_summary(entries: list[dict]) -> str:
        count = len(entries)
        if count == 0:
            return "EMPTY"
        if count == 1:
            return "1 photo"
        return f"{count} photos"

    @staticmethod
    def _copy_device_photo_entries(
        project_id: str,
        device_id: str,
        entries: list[dict],
    ) -> list[dict]:
        photos_dir = config.PROJECTS_DIR / project_id / "fabrication" / device_id / "photos"
        copied_entries = []
        for entry in entries:
            raw_path = entry.get("photo_path", "")
            if not raw_path:
                continue
            source = config.resolve_data_path(raw_path)
            stored_path = raw_path
            if source.exists():
                photos_dir.mkdir(parents=True, exist_ok=True)
                target_root = photos_dir.resolve()
                source_resolved = source.resolve()
                if target_root == source_resolved or target_root in source_resolved.parents:
                    stored_path = config.to_data_path(source_resolved)
                else:
                    stored_path = config.to_data_path(
                        ProjectWidget._copy_photo_to_dir(source_resolved, photos_dir)
                    )
            copied_entries.append({
                "photo_path": stored_path,
                "note": entry.get("note", ""),
            })
        return copied_entries

    @staticmethod
    def _copy_photo_to_dir(source_path: Path, target_dir: Path) -> Path:
        """Copy a photo into target_dir without overwriting an existing file."""
        target_dir.mkdir(parents=True, exist_ok=True)
        base = source_path.stem
        suffix = source_path.suffix
        dest = target_dir / source_path.name
        counter = 1
        while dest.exists():
            dest = target_dir / f"{base}_{counter}{suffix}"
            counter += 1
        shutil.copy2(source_path, dest)
        return dest

    def on_new_project(self):
        """Open new project dialog."""
        dialog = NewProjectDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.load_projects()

    def on_edit_project(self):
        """Open edit project dialog."""
        item = self.project_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select a project to edit")
            return

        project_id = item.data(Qt.UserRole)
        project = db.get_project(project_id)

        dialog = EditProjectDialog(project, self)
        if dialog.exec() == QDialog.Accepted:
            self.load_projects()
            self.on_project_selected()

    def on_delete_project(self):
        """Delete the selected project."""
        item = self.project_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select a project to delete")
            return

        project_id = item.data(Qt.UserRole)
        project = db.get_project(project_id)

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete project '{project['name']}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                project_dir = config.PROJECTS_DIR / project_id
                if project_dir.exists():
                    shutil.rmtree(project_dir)

                db.delete_project(project_id)
                self.load_projects()
                self.device_table.setRowCount(0)
                self.project_header.setText("Select a project")
                QMessageBox.information(self, "Success", "Project deleted")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete project: {str(e)}")

    def on_new_device(self):
        """Open new device dialog."""
        if not hasattr(self, 'current_project_id'):
            QMessageBox.warning(self, "Warning", "Select a project first")
            return

        dialog = NewDeviceDialog(self.current_project_id, self)
        if dialog.exec() == QDialog.Accepted:
            self.load_devices(self.current_project_id)

    def on_edit_device(self):
        """Open edit device dialog."""
        row = self.device_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Warning", "Select a device to edit")
            return

        device_id = ProjectWidget._device_id_from_table(self.device_table, row)
        if not device_id:
            return
        device = db.get_device(device_id)

        dialog = EditDeviceDialog(device, self.current_project_id, self)
        if dialog.exec() == QDialog.Accepted:
            self.load_devices(self.current_project_id)

    def on_delete_device(self):
        """Delete the selected device."""
        row = self.device_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Warning", "Select a device to delete")
            return

        device_id = ProjectWidget._device_id_from_table(self.device_table, row)
        if not device_id:
            return
        device = db.get_device(device_id)
        device_layers = db.get_device_layers(device_id)
        is_retired = device and device.get("status") == "retired"
        if device_layers and is_retired:
            title = "Confirm Permanent Delete"
            message = (
                f"Permanently delete retired device '{device_id}'? "
                "This will remove the device record and its layer links. "
                "Consumed flakes will remain in the database."
            )
        else:
            title = "Confirm Delete"
            message = f"Delete device '{device_id}'? This cannot be undone."

        reply = QMessageBox.question(
            self, title,
            message,
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                if device_layers and not is_retired:
                    db.update_device(device_id, status="retired")
                    self.load_devices(self.current_project_id)
                    QMessageBox.information(
                        self,
                        "Success",
                        "Device has consumed flakes and was marked retired instead of deleted.",
                    )
                    return

                fab_dir = config.PROJECTS_DIR / self.current_project_id / "fabrication" / device_id
                if fab_dir.exists():
                    ProjectWidget._remove_directory_best_effort(fab_dir)

                meas_link = config.PROJECTS_DIR / self.current_project_id / "measurements" / device_id
                if meas_link.exists() or meas_link.is_symlink():
                    meas_link.unlink()

                db.delete_device(device_id)
                self.load_devices(self.current_project_id)
                QMessageBox.information(self, "Success", "Device deleted")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete device: {str(e)}")

    @staticmethod
    def _remove_directory_best_effort(path: Path):
        """Remove an app-managed directory without blocking database cleanup."""
        for attempt in range(3):
            try:
                shutil.rmtree(path, onexc=ProjectWidget._make_writable_and_retry)
                return
            except PermissionError:
                if attempt == 2:
                    logger.warning("Could not remove directory: %s", path, exc_info=True)
                    return
                time.sleep(0.1)
            except OSError:
                logger.warning("Could not remove directory: %s", path, exc_info=True)
                return

    @staticmethod
    def _make_writable_and_retry(function, path, excinfo):
        os.chmod(path, 0o700)
        function(path)

    def on_open_fab_folder(self):
        """Open fabrication folder for selected device."""
        row = self.device_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Warning", "Select a device")
            return

        device_id = self._device_id_for_row(row)
        if not device_id:
            return
        fab_path = config.PROJECTS_DIR / self.current_project_id / "fabrication" / device_id

        if fab_path.exists():
            os.startfile(str(fab_path)) if os.name == 'nt' else os.system(f'open "{fab_path}"')
        else:
            QMessageBox.warning(self, "Warning", "Fabrication folder does not exist")

    def on_open_meas_folder(self):
        """Open measurement folder for selected device."""
        row = self.device_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Warning", "Select a device")
            return

        device_id = self._device_id_for_row(row)
        if not device_id:
            return
        meas_path = config.PYFLEXLAB_OUT_PATH / device_id

        if meas_path.exists():
            os.startfile(str(meas_path)) if os.name == 'nt' else os.system(f'open "{meas_path}"')
        else:
            QMessageBox.warning(self, "Warning", "Measurement folder does not exist")

    @staticmethod
    def write_used_flakes_index(project_id: str, device_id: str, layers: list[dict]):
        """Write a small device-local index of consumed flake identifiers."""
        fab_dir = config.PROJECTS_DIR / project_id / "fabrication" / device_id
        fab_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "device_id": device_id,
            "layers": [
                {
                    "order_index": order_index,
                    "layer_name": layer.get("layer_name", ""),
                    "flake_uid": layer.get("flake_uid"),
                    "flake_id": layer.get("flake_id"),
                    "material": layer.get("material", ""),
                }
                for order_index, layer in enumerate(layers)
            ],
        }
        index_path = fab_dir / "used_flakes.json"
        tmp_path = index_path.with_name(f"{index_path.name}.tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(index_path)


class NewProjectDialog(QDialog):
    """Dialog for creating a new project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self.update_project_id)
        layout.addRow("Display Name:", self.name_edit)

        self.project_id_edit = QLineEdit()
        layout.addRow("Project ID:", self.project_id_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(100)
        layout.addRow("Description:", self.desc_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.setLayout(layout)

    def update_project_id(self):
        """Auto-generate project ID from name."""
        name = self.name_edit.text().strip()
        if name:
            project_id = name.lower().replace(" ", "_")
            self.project_id_edit.setText(project_id)

    def accept(self):
        """Create the project."""
        project_id = self.project_id_edit.text().strip()
        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText().strip()

        if not project_id or not name:
            QMessageBox.warning(self, "Validation", "Project ID and Name are required")
            return

        try:
            db.create_project(project_id, name, description)

            project_dir = config.PROJECTS_DIR / project_id
            project_dir.mkdir(parents=True, exist_ok=True)

            for subdir in ["fabrication", "measurements", "analysis", "reports", "cad"]:
                (project_dir / subdir).mkdir(parents=True, exist_ok=True)

            super().accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create project: {str(e)}")


class EditProjectDialog(QDialog):
    """Dialog for editing an existing project."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Edit Project")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QFormLayout()

        self.project_id_label = QLabel(self.project['project_id'])
        layout.addRow("Project ID:", self.project_id_label)

        self.name_edit = QLineEdit()
        self.name_edit.setText(self.project['name'])
        layout.addRow("Display Name:", self.name_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setPlainText(self.project['description'] or "")
        self.desc_edit.setMaximumHeight(100)
        layout.addRow("Description:", self.desc_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.setLayout(layout)

    def accept(self):
        """Save the project changes."""
        try:
            db.update_project(
                self.project['project_id'],
                name=self.name_edit.text(),
                description=self.desc_edit.toPlainText()
            )
            super().accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update project: {str(e)}")


class NewDeviceDialog(QDialog):
    """Dialog for creating a new device."""

    def __init__(self, project_id, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.layers = []
        self.device_photo_entries = []
        self.setWindowTitle("New Device")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout()

        form_layout = QFormLayout()

        self.device_id_edit = QLineEdit()
        self.device_id_edit.setPlaceholderText(f"{self.project_id}-YYYYMM")
        form_layout.addRow("Device ID:", self.device_id_edit)

        self.desc_edit = QLineEdit()
        form_layout.addRow("Description:", self.desc_edit)

        self.fab_date_edit = QLineEdit()
        form_layout.addRow("Fab Date:", self.fab_date_edit)

        layout.addLayout(form_layout)

        layout.addWidget(QLabel("Device Photos:"))
        photo_layout = QHBoxLayout()
        self.device_photo_label = QLabel("No device photos selected")
        photo_layout.addWidget(self.device_photo_label)
        photo_btn = QPushButton("Device Photos...")
        style.decorate_button(photo_btn, "utility", "photo")
        photo_btn.clicked.connect(self.edit_device_photos)
        photo_layout.addWidget(photo_btn)
        screenshot_btn = QPushButton("Screenshot...")
        style.decorate_button(screenshot_btn, "utility", "photo")
        screenshot_btn.clicked.connect(self.capture_device_photo)
        photo_layout.addWidget(screenshot_btn)
        layout.addLayout(photo_layout)

        layout.addWidget(QLabel("Layers:"))

        self.layers_table = QTableWidget()
        style.decorate_table(self.layers_table)
        self.layers_table.setColumnCount(4)
        self.layers_table.setHorizontalHeaderLabels(["Order", "Layer Name", "Flake ID", "Material"])
        self.layers_table.setMaximumHeight(150)
        layout.addWidget(self.layers_table)

        btn_add_layer = QPushButton("Add Layer")
        style.decorate_button(btn_add_layer, "utility", "plus")
        btn_add_layer.clicked.connect(self.on_add_layer)
        layout.addWidget(btn_add_layer)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def on_add_layer(self):
        """Add a layer to the device."""
        dialog = AddLayerDialog(self)
        if dialog.exec() == QDialog.Accepted:
            layer = dialog.get_layer_data()
            self.layers.append(layer)
            self.refresh_layers_table()

    def edit_device_photos(self):
        """Open the device photos editor for photos collected before creation."""
        dialog = DevicePhotosDialog(self.device_photo_entries, self)
        if dialog.exec() == QDialog.Accepted:
            self.device_photo_entries = dialog.get_photo_entries()
            self._update_device_photo_label()

    def capture_device_photo(self):
        """Capture a screen region and append it to the new device photos."""
        from .wafer_widget import capture_screen_region

        file_path = capture_screen_region(self)
        if file_path:
            self.device_photo_entries.append({"photo_path": file_path, "note": ""})
            self._update_device_photo_label()

    def _update_device_photo_label(self):
        count = len(self.device_photo_entries)
        if count:
            self.device_photo_label.setText(f"{count} selected")
        else:
            self.device_photo_label.setText("No device photos selected")

    def refresh_layers_table(self):
        """Refresh the layers table display."""
        self.layers_table.setRowCount(len(self.layers))
        for row, layer in enumerate(self.layers):
            self.layers_table.setItem(row, 0, QTableWidgetItem(str(row)))
            self.layers_table.setItem(row, 1, QTableWidgetItem(layer['layer_name']))
            self.layers_table.setItem(row, 2, QTableWidgetItem(layer.get('flake_id') or ''))
            self.layers_table.setItem(row, 3, QTableWidgetItem(layer.get('material', '')))

    def accept(self):
        """Create the device."""
        device_id = self.device_id_edit.text().strip()
        description = self.desc_edit.text().strip()
        fab_date = self.fab_date_edit.text().strip()

        if not device_id:
            QMessageBox.warning(self, "Validation", "Device ID is required")
            return

        try:
            fab_dir = config.PROJECTS_DIR / self.project_id / "fabrication" / device_id
            fab_path = config.to_data_path(fab_dir)

            meas_target = config.PYFLEXLAB_OUT_PATH / device_id

            # Initialise measurement folder before writing DB state so failures
            # do not leave a device record without backing measurement setup.
            try:
                from pyflexlab.file_organizer import FileOrganizer
                FileOrganizer(device_id)
            except Exception as e:
                raise RuntimeError(f"Failed to initialise measurement folder via pyflexlab: {e}")

            fab_dir.mkdir(parents=True, exist_ok=True)
            assembly_photos = ProjectWidget._copy_device_photo_entries(
                self.project_id,
                device_id,
                self.device_photo_entries,
            )

            db.create_device_with_layers(
                device_id,
                self.project_id,
                self.layers,
                description=description,
                fab_date=fab_date,
                status="planned",
                fab_path=fab_path,
                meas_path=str(meas_target),
                notes="",
                assembly_photos=json.dumps(assembly_photos, ensure_ascii=False),
            )
            ProjectWidget.write_used_flakes_index(self.project_id, device_id, self.layers)

            # Symlink from project tree → pyflexlab data directory
            meas_link = config.PROJECTS_DIR / self.project_id / "measurements" / device_id
            try:
                os.symlink(meas_target, meas_link, target_is_directory=True)
            except OSError as e:
                QMessageBox.warning(self, "Symlink Warning",
                    f"Could not create symlink to measurement folder:\n{e}\n\n"
                    "The device was created and the measurement folder exists, "
                    "but you will need to navigate to it manually."
                )

            super().accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create device: {str(e)}")


class EditDeviceDialog(QDialog):
    """Dialog for editing an existing device."""

    def __init__(self, device, project_id, parent=None):
        super().__init__(parent)
        self.device = device
        self.project_id = project_id
        self.layers = []
        self.setWindowTitle("Edit Device")
        self.setModal(True)
        self.init_ui()
        self.load_layers()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout()

        form_layout = QFormLayout()

        self.device_id_label = QLabel(self.device['device_id'])
        form_layout.addRow("Device ID:", self.device_id_label)

        self.desc_edit = QLineEdit()
        self.desc_edit.setText(self.device['description'] or "")
        form_layout.addRow("Description:", self.desc_edit)

        self.fab_date_edit = QLineEdit()
        self.fab_date_edit.setText(self.device['fab_date'] or "")
        form_layout.addRow("Fab Date:", self.fab_date_edit)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["planned", "fabricated", "measured", "retired"])
        self.status_combo.setCurrentText(self.device['status'] or "planned")
        form_layout.addRow("Status:", self.status_combo)

        layout.addLayout(form_layout)

        layout.addWidget(QLabel("Layers:"))

        self.layers_table = QTableWidget()
        style.decorate_table(self.layers_table)
        self.layers_table.setColumnCount(4)
        self.layers_table.setHorizontalHeaderLabels(["Order", "Layer Name", "Flake ID", "Material"])
        self.layers_table.setMaximumHeight(150)
        layout.addWidget(self.layers_table)

        btn_add_layer = QPushButton("Add Layer")
        style.decorate_button(btn_add_layer, "utility", "plus")
        btn_add_layer.clicked.connect(self.on_add_layer)
        layout.addWidget(btn_add_layer)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def load_layers(self):
        """Load existing layers for the device."""
        layers_data = db.get_device_layers(self.device['device_id'])
        self.layers = [
            {
                'id': layer['id'],
                'layer_name': layer['layer_name'],
                'flake_uid': layer['flake_uid'],
                'flake_id': layer['flake_id'],
                'material': layer.get('material', '')
            }
            for layer in layers_data
        ]
        self.refresh_layers_table()

    def refresh_layers_table(self):
        """Refresh the layers table display."""
        self.layers_table.setRowCount(len(self.layers))
        for row, layer in enumerate(self.layers):
            self.layers_table.setItem(row, 0, QTableWidgetItem(str(row)))
            self.layers_table.setItem(row, 1, QTableWidgetItem(layer['layer_name']))
            self.layers_table.setItem(row, 2, QTableWidgetItem(layer.get('flake_id') or ''))
            self.layers_table.setItem(row, 3, QTableWidgetItem(layer.get('material', '')))

    def on_add_layer(self):
        """Add a layer to the device."""
        dialog = AddLayerDialog(self)
        if dialog.exec() == QDialog.Accepted:
            layer = dialog.get_layer_data()
            self.layers.append(layer)
            self.refresh_layers_table()

    def accept(self):
        """Save device changes."""
        try:
            db.update_device(
                self.device['device_id'],
                description=self.desc_edit.text(),
                fab_date=self.fab_date_edit.text(),
                status=self.status_combo.currentText()
            )
            new_layers = [layer for layer in self.layers if not layer.get('id')]
            existing_count = len(self.layers) - len(new_layers)
            db.add_device_layers_and_mark_flakes(
                self.device['device_id'],
                new_layers,
                start_index=existing_count,
            )
            ProjectWidget.write_used_flakes_index(
                self.project_id,
                self.device['device_id'],
                self.layers,
            )

            super().accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update device: {str(e)}")


class AddLayerDialog(QDialog):
    """Dialog for adding a layer to a device."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Layer")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QFormLayout()

        self.layer_name_edit = QLineEdit()
        self.layer_name_edit.setPlaceholderText("e.g., top_bn, graphene, channel")
        layout.addRow("Layer Name:", self.layer_name_edit)

        self.material_combo = QComboBox()
        self.material_combo.currentTextChanged.connect(self.on_material_changed)
        layout.addRow("Material Filter:", self.material_combo)

        self.flake_combo = QComboBox()
        layout.addRow("Select Flake:", self.flake_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.setLayout(layout)
        self.load_materials()

    def load_materials(self):
        """Load available materials."""
        materials = set()
        flakes = db.get_available_flakes()
        for flake in flakes:
            material = flake.get('material', 'Unknown')
            if material:
                materials.add(material)

        self.material_combo.addItems(sorted(materials))
        self.on_material_changed()

    def on_material_changed(self):
        """Update flake list based on selected material."""
        material = self.material_combo.currentText()
        self.flake_combo.clear()

        flakes = db.get_available_flakes(material_filter=material)
        for flake in flakes:
            wafer_label = ""
            if flake.get("box_name") is not None and flake.get("wafer_row") is not None:
                row_label = chr(ord("A") + flake["wafer_row"])
                col_label = flake["wafer_col"] + 1
                wafer_label = f" [{flake['box_name']} {row_label}{col_label}]"
            self.flake_combo.addItem(
                f"{flake['flake_id']}{wafer_label} ({flake.get('material', 'Unknown')})",
                flake
            )

    def get_layer_data(self):
        """Return the layer data."""
        flake = self.flake_combo.currentData()
        return {
            'layer_name': self.layer_name_edit.text(),
            'flake_uid': flake['flake_uid'],
            'flake_id': flake['flake_id'],
            'material': flake.get('material', '')
        }

    def accept(self):
        """Validate and accept."""
        if not self.layer_name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Layer name is required")
            return

        if not self.flake_combo.currentData():
            QMessageBox.warning(self, "Validation", "Select a flake")
            return

        super().accept()
