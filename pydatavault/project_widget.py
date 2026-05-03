import os
import shutil
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QTableWidget, QTableWidgetItem, QSplitter, QDialog,
    QLabel, QLineEdit, QTextEdit, QComboBox, QSpinBox, QMessageBox,
    QDialogButtonBox, QFormLayout, QHeaderView, QAbstractItemView,
    QStyledItemDelegate
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor
from PySide6.QtCore import QSize

from . import database as db
from . import config
from . import style


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


class ProjectWidget(QWidget):
    """Main widget for project and device management."""

    def __init__(self, parent=None):
        super().__init__(parent)
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

        self.device_table = QTableWidget()
        style.decorate_table(self.device_table)
        self.device_table.setColumnCount(7)
        self.device_table.setHorizontalHeaderLabels(
            ["Device ID", "Description", "Fab Date", "Status", "Layers", "Meas Date", "Notes"]
        )
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.device_table.itemChanged.connect(self.on_device_cell_changed)

        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)

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
        self.device_table.setRowCount(0)

        devices = db.get_project_device_summary(project_id)

        for row, device in enumerate(devices):
            self.device_table.insertRow(row)

            self.device_table.setItem(row, 0, QTableWidgetItem(device['device_id']))
            self.device_table.setItem(row, 1, QTableWidgetItem(device['description'] or ""))
            self.device_table.setItem(row, 2, QTableWidgetItem(device['fab_date'] or ""))

            status_item = QTableWidgetItem(device['status'] or "planned")
            style.decorate_status_item(status_item, device['status'])
            self.device_table.setItem(row, 3, status_item)

            layer_count = device.get('layer_count', 0)
            self.device_table.setItem(row, 4, QTableWidgetItem(str(layer_count)))

            self.device_table.setItem(row, 5, QTableWidgetItem(device.get('meas_date') or ""))
            self.device_table.setItem(row, 6, QTableWidgetItem(device.get('notes') or ""))

    def on_device_cell_changed(self, item):
        """Save device changes when cell is edited."""
        if not hasattr(self, 'current_project_id'):
            return

        row = item.row()
        device_id = self.device_table.item(row, 0).text()

        col = item.column()
        col_name = self.device_table.horizontalHeaderItem(col).text()

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

        device_id = self.device_table.item(row, 0).text()
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

        device_id = self.device_table.item(row, 0).text()
        device = db.get_device(device_id)

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete device '{device_id}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                fab_dir = config.PROJECTS_DIR / self.current_project_id / "fabrication" / device_id
                if fab_dir.exists():
                    shutil.rmtree(fab_dir)

                meas_link = config.PROJECTS_DIR / self.current_project_id / "measurements" / device_id
                if meas_link.exists() or meas_link.is_symlink():
                    meas_link.unlink()

                db.delete_device(device_id)
                self.load_devices(self.current_project_id)
                QMessageBox.information(self, "Success", "Device deleted")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete device: {str(e)}")

    def on_open_fab_folder(self):
        """Open fabrication folder for selected device."""
        row = self.device_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Warning", "Select a device")
            return

        device_id = self.device_table.item(row, 0).text()
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

        device_id = self.device_table.item(row, 0).text()
        meas_path = config.PYFLEXLAB_OUT_PATH / device_id

        if meas_path.exists():
            os.startfile(str(meas_path)) if os.name == 'nt' else os.system(f'open "{meas_path}"')
        else:
            QMessageBox.warning(self, "Warning", "Measurement folder does not exist")


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
            fab_path = str(fab_dir)

            meas_target = config.PYFLEXLAB_OUT_PATH / device_id

            # Initialise measurement folder before writing DB state so failures
            # do not leave a device record without backing measurement setup.
            try:
                from pyflexlab.file_organizer import FileOrganizer
                FileOrganizer(device_id)
            except Exception as e:
                raise RuntimeError(f"Failed to initialise measurement folder via pyflexlab: {e}")

            fab_dir.mkdir(parents=True, exist_ok=True)

            db.create_device_with_layers(
                device_id,
                self.project_id,
                self.layers,
                description=description,
                fab_date=fab_date,
                status="planned",
                fab_path=fab_path,
                meas_path=str(meas_target),
                notes=""
            )

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
