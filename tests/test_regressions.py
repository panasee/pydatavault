import importlib
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class PyDataVaultRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.root_path = REPO_ROOT / ".test_tmp" / f"regressions_{uuid4().hex}"
        cls.root_path.mkdir(parents=True, exist_ok=True)
        os.environ["VAULT_DB_PATH"] = str(cls.root_path)
        cls.config = importlib.import_module("pydatavault.config")
        cls.db = importlib.import_module("pydatavault.database")
        cls.wafer_widget = importlib.import_module("pydatavault.wafer_widget")
        cls.project_widget = importlib.import_module("pydatavault.project_widget")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.root_path.mkdir(parents=True, exist_ok=True)
        if self.config.DB_FILE.exists():
            self.config.DB_FILE.unlink()
        self.db.init_db()

    def test_database_exposes_summary_counts_for_main_window(self):
        box_id = self.db.create_box("Box A")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_flake("flake-1", wafer["wafer_id"], material="Graphene")
        self.db.create_project("proj", "Project")
        self.db.create_device("device-1", "proj")

        self.assertEqual(self.db.count_flakes(), 1)
        self.assertEqual(self.db.count_devices(), 1)

    def test_wafer_widget_refresh_reloads_current_selection(self):
        wafer = {"wafer_id": 1, "row": 0, "col": 0, "label": "", "ref_points": "[]"}
        calls = []

        class DummyWidget:
            current_box_id = 1
            current_wafer_id = 1

            def load_boxes(self):
                calls.append(("load_boxes",))

            def load_grid(self):
                calls.append(("load_grid",))

            def load_flakes_for_wafer(self, wafer_dict):
                calls.append(("load_flakes_for_wafer", wafer_dict))

            def load_ref_points(self, wafer_dict):
                calls.append(("load_ref_points", wafer_dict))

        with mock.patch.object(self.wafer_widget.db, "get_wafer_by_id", return_value=wafer):
            self.wafer_widget.WaferWidget.refresh(DummyWidget())

        self.assertEqual(
            calls,
            [
                ("load_boxes",),
                ("load_grid",),
                ("load_flakes_for_wafer", wafer),
                ("load_ref_points", wafer),
            ],
        )

    def test_migration_repairs_flake_wafer_foreign_key_only(self):
        if self.config.DB_FILE.exists():
            self.config.DB_FILE.unlink()
        self.config.ensure_dirs()
        conn = sqlite3.connect(self.config.DB_FILE)
        try:
            conn.executescript("""
                CREATE TABLE wafer_boxes (
                    box_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    rows INTEGER NOT NULL DEFAULT 5,
                    cols INTEGER NOT NULL DEFAULT 5,
                    notes TEXT DEFAULT ''
                );
                CREATE TABLE wafers (
                    wafer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    box_id INTEGER NOT NULL REFERENCES wafer_boxes(box_id) ON DELETE CASCADE,
                    row INTEGER NOT NULL,
                    col INTEGER NOT NULL,
                    label TEXT DEFAULT '',
                    ref_points TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    UNIQUE(box_id, row, col)
                );
                CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE devices (
                    device_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    description TEXT DEFAULT '',
                    fab_date TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    fab_path TEXT DEFAULT '',
                    meas_path TEXT DEFAULT '',
                    meas_date TEXT DEFAULT '',
                    meas_notes TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE flakes (
                    flake_id TEXT PRIMARY KEY,
                    wafer_id INTEGER REFERENCES wafers(wafer_id) ON DELETE CASCADE,
                    material TEXT NOT NULL DEFAULT '',
                    thickness TEXT DEFAULT '',
                    magnification TEXT DEFAULT '',
                    photo_path TEXT DEFAULT '',
                    coord_x REAL DEFAULT 0.0,
                    coord_y REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'available',
                    used_in_device TEXT DEFAULT NULL REFERENCES devices(device_id) ON DELETE SET NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE device_layers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                    layer_name TEXT NOT NULL,
                    flake_id TEXT REFERENCES flakes(flake_id) ON DELETE SET NULL,
                    order_index INTEGER DEFAULT 0
                );
            """)
        finally:
            conn.close()

        self.db.init_db()

        conn = sqlite3.connect(self.config.DB_FILE)
        try:
            rows = conn.execute("PRAGMA foreign_key_list(flakes)").fetchall()
        finally:
            conn.close()
        wafer_fk = [row for row in rows if row[3] == "wafer_id"][0]
        self.assertEqual(wafer_fk[6], "SET NULL")

    def test_delete_box_deletes_available_flakes_and_preserves_used_flakes(self):
        box_id = self.db.create_box("Box B")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_project("proj", "Project")
        self.db.create_device("device-1", "proj")
        self.db.create_flake("available", wafer["wafer_id"], material="Graphene")
        self.db.create_flake("used", wafer["wafer_id"], material="hBN")
        self.db.update_flake("used", status="used", used_in_device="device-1")

        self.db.delete_box(box_id)

        self.assertIsNone(self.db.get_flake("available"))
        used = self.db.get_flake("used")
        self.assertIsNotNone(used)
        self.assertIsNone(used["wafer_id"])

    def test_edit_device_dialog_persists_added_layers(self):
        box_id = self.db.create_box("Box C")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_flake("flake-edit", wafer["wafer_id"], material="Graphene")
        self.db.create_project("proj", "Project")
        self.db.create_device("device-edit", "proj")
        device = self.db.get_device("device-edit")
        dialog = self.project_widget.EditDeviceDialog(device, "proj")
        dialog.layers.append({
            "layer_name": "channel",
            "flake_id": "flake-edit",
            "material": "Graphene",
        })

        dialog.accept()

        layers = self.db.get_device_layers("device-edit")
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["flake_id"], "flake-edit")
        flake = self.db.get_flake("flake-edit")
        self.assertEqual(flake["status"], "used")
        self.assertEqual(flake["used_in_device"], "device-edit")

    def test_new_device_dialog_rolls_back_database_when_measurement_setup_fails(self):
        self.db.create_project("proj", "Project")
        dialog = self.project_widget.NewDeviceDialog("proj")
        dialog.device_id_edit.setText("device-fail")

        with mock.patch("pyflexlab.file_organizer.FileOrganizer",
                        side_effect=RuntimeError("boom")), \
             mock.patch.object(self.project_widget.QMessageBox, "critical"):
            dialog.accept()

        self.assertIsNone(self.db.get_device("device-fail"))


if __name__ == "__main__":
    unittest.main()
