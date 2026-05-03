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
        cls.main_window = importlib.import_module("pydatavault.main_window")
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

    def test_flake_ids_are_unique_per_wafer_with_internal_uids(self):
        box_id = self.db.create_box("Box Local IDs")
        wafer_a = self.db.get_or_create_wafer(box_id, 0, 0)
        wafer_b = self.db.get_or_create_wafer(box_id, 0, 1)

        uid_a = self.db.create_flake("bf1", wafer_a["wafer_id"], material="Graphene")
        uid_b = self.db.create_flake("bf1", wafer_b["wafer_id"], material="hBN")

        self.assertNotEqual(uid_a, uid_b)
        flake_a = self.db.get_flake(uid_a)
        flake_b = self.db.get_flake(uid_b)
        self.assertEqual(flake_a["flake_id"], "bf1")
        self.assertEqual(flake_b["flake_id"], "bf1")
        self.assertEqual(flake_a["wafer_id"], wafer_a["wafer_id"])
        self.assertEqual(flake_b["wafer_id"], wafer_b["wafer_id"])

    def test_device_layers_reference_internal_flake_uid(self):
        box_id = self.db.create_box("Box Layer Local IDs")
        wafer_a = self.db.get_or_create_wafer(box_id, 0, 0)
        wafer_b = self.db.get_or_create_wafer(box_id, 0, 1)
        uid_a = self.db.create_flake("bf1", wafer_a["wafer_id"], material="Graphene")
        uid_b = self.db.create_flake("bf1", wafer_b["wafer_id"], material="hBN")
        self.db.create_project("proj", "Project")

        self.db.create_device_with_layers(
            "device-local-id",
            "proj",
            [{"layer_name": "channel", "flake_uid": uid_b}],
        )

        layer = self.db.get_device_layers("device-local-id")[0]
        self.assertEqual(layer["flake_uid"], uid_b)
        self.assertEqual(layer["flake_id"], "bf1")
        self.assertEqual(layer["material"], "hBN")
        self.assertEqual(self.db.get_flake(uid_a)["status"], "available")
        used_flake = self.db.get_flake(uid_b)
        self.assertEqual(used_flake["status"], "used")
        self.assertIsNone(used_flake["wafer_id"])
        replacement_uid = self.db.create_flake(
            "bf1",
            wafer_b["wafer_id"],
            material="hBN replacement",
        )
        self.assertNotEqual(replacement_uid, uid_b)

    def test_about_dialog_formats_database_path_as_text(self):
        parent = object()

        with mock.patch.object(self.main_window.QMessageBox, "about") as about:
            self.main_window.MainWindow._show_about(parent)

        about.assert_called_once()
        self.assertEqual(about.call_args.args[0], parent)
        self.assertEqual(about.call_args.args[1], "About PyDataVault")
        self.assertIn(
            f"Database location: {self.config.ROOT_PATH}",
            about.call_args.args[2],
        )

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

    def test_wafer_widget_saves_label_and_notes(self):
        box_id = self.db.create_box("Box Label")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        refreshed = []

        class DummyWidget:
            current_wafer_id = wafer["wafer_id"]

            def load_flakes_for_wafer(self, wafer_dict):
                refreshed.append(("flakes", wafer_dict))

            def load_ref_points(self, wafer_dict):
                refreshed.append(("refs", wafer_dict))

        self.wafer_widget.WaferWidget._save_wafer_metadata(
            DummyWidget(),
            "graphene-rich",
            "good contrast near center",
        )

        updated = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(updated["label"], "graphene-rich")
        self.assertEqual(updated["notes"], "good contrast near center")
        self.assertEqual(refreshed[0], ("flakes", updated))
        self.assertEqual(refreshed[1], ("refs", updated))

    def test_loading_flakes_does_not_trigger_partial_row_update(self):
        box_id = self.db.create_box("Box Load Flakes")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")

        widget = self.wafer_widget.WaferWidget()
        try:
            with mock.patch.object(self.db, "update_flake") as update_flake:
                widget.load_flakes_for_wafer(wafer)

            update_flake.assert_not_called()
        finally:
            widget.close()

    def test_flake_update_ignores_incomplete_table_rows(self):
        widget = self.wafer_widget.WaferWidget()
        try:
            widget.current_wafer_id = 1
            widget.flake_table.setRowCount(1)
            widget.flake_table.setItem(0, 0, self.wafer_widget.QTableWidgetItem("bf1"))

            with mock.patch.object(self.db, "update_flake") as update_flake:
                widget.on_flake_cell_changed(widget.flake_table.item(0, 0))

            update_flake.assert_not_called()
        finally:
            widget.close()

    def test_add_flake_refreshes_by_current_wafer_without_selected_grid_cell(self):
        box_id = self.db.create_box("Box Add Flake")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        refreshed = []

        class DummyDialog:
            def __init__(self, wafer_id, parent=None):
                self.wafer_id = wafer_id

            def exec(self):
                return self.wafer_widget.QDialog.Accepted

            def get_data(self):
                return {
                    "flake_id": "bf1",
                    "material": "Graphene",
                    "thickness": "",
                    "magnification": "",
                    "photo_path": None,
                    "coord_x": 0.0,
                    "coord_y": 0.0,
                    "notes": "",
                }

        class DummyWidget:
            current_box_id = box_id
            current_wafer_id = wafer["wafer_id"]
            grid_view = mock.Mock(selected_cell=None)

            def load_flakes_for_wafer(self, wafer_dict):
                refreshed.append(wafer_dict)

            def load_grid(self):
                pass

        DummyDialog.wafer_widget = self.wafer_widget
        with mock.patch.object(self.wafer_widget, "AddFlakeDialog", DummyDialog):
            self.wafer_widget.WaferWidget.add_flake(DummyWidget())

        self.assertEqual(refreshed, [self.db.get_wafer_by_id(wafer["wafer_id"])])
        flakes = self.db.get_flakes_for_wafer(wafer["wafer_id"])
        self.assertEqual(len(flakes), 1)
        self.assertEqual(flakes[0]["flake_id"], "bf1")

    def test_delete_flake_refreshes_by_current_wafer_without_selected_grid_cell(self):
        box_id = self.db.create_box("Box Delete Flake")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")
        refreshed = []

        class DummyItem:
            def data(self, role):
                return flake_uid

            def text(self):
                return "bf1"

        class DummyTable:
            def currentRow(self):
                return 0

            def item(self, row, col):
                return DummyItem()

        class DummyWidget:
            current_box_id = box_id
            current_wafer_id = wafer["wafer_id"]
            grid_view = mock.Mock(selected_cell=None)
            flake_table = DummyTable()

            def load_flakes_for_wafer(self, wafer_dict):
                refreshed.append(wafer_dict)

            def load_grid(self):
                pass

        with mock.patch.object(
            self.wafer_widget.QMessageBox,
            "question",
            return_value=self.wafer_widget.QMessageBox.Yes,
        ):
            self.wafer_widget.WaferWidget.delete_flake(DummyWidget())

        self.assertEqual(refreshed, [self.db.get_wafer_by_id(wafer["wafer_id"])])
        self.assertEqual(self.db.get_flake(flake_uid), None)

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
                INSERT INTO wafer_boxes (name) VALUES ('old-box');
                INSERT INTO wafers (box_id, row, col) VALUES (1, 0, 0);
                INSERT INTO projects (project_id, name) VALUES ('proj', 'Project');
                INSERT INTO devices (device_id, project_id) VALUES ('device-old', 'proj');
                INSERT INTO flakes (flake_id, wafer_id, material, status, used_in_device)
                    VALUES ('old-flake', 1, 'Graphene', 'used', 'device-old');
                INSERT INTO device_layers (device_id, layer_name, flake_id, order_index)
                    VALUES ('device-old', 'channel', 'old-flake', 0);
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
        layer = self.db.get_device_layers("device-old")[0]
        self.assertIsInstance(layer["flake_uid"], int)
        self.assertEqual(layer["flake_id"], "old-flake")
        self.assertEqual(layer["material"], "Graphene")
        flake = self.db.get_flake(layer["flake_uid"])
        self.assertEqual(flake["status"], "used")
        self.assertIsNone(flake["wafer_id"])

    def test_delete_box_deletes_available_flakes_and_preserves_used_flakes(self):
        box_id = self.db.create_box("Box B")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_project("proj", "Project")
        self.db.create_device("device-1", "proj")
        available_uid = self.db.create_flake("available", wafer["wafer_id"], material="Graphene")
        used_uid = self.db.create_flake("used", wafer["wafer_id"], material="hBN")
        self.db.update_flake(used_uid, status="used", used_in_device="device-1")

        self.db.delete_box(box_id)

        self.assertIsNone(self.db.get_flake(available_uid))
        used = self.db.get_flake(used_uid)
        self.assertIsNotNone(used)
        self.assertIsNone(used["wafer_id"])

    def test_edit_device_dialog_persists_added_layers(self):
        box_id = self.db.create_box("Box C")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("flake-edit", wafer["wafer_id"], material="Graphene")
        self.db.create_project("proj", "Project")
        self.db.create_device("device-edit", "proj")
        device = self.db.get_device("device-edit")
        dialog = self.project_widget.EditDeviceDialog(device, "proj")
        dialog.layers.append({
            "layer_name": "channel",
            "flake_uid": flake_uid,
            "flake_id": "flake-edit",
            "material": "Graphene",
        })

        dialog.accept()

        layers = self.db.get_device_layers("device-edit")
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["flake_uid"], flake_uid)
        self.assertEqual(layers[0]["flake_id"], "flake-edit")
        flake = self.db.get_flake(flake_uid)
        self.assertEqual(flake["status"], "used")
        self.assertEqual(flake["used_in_device"], "device-edit")
        self.assertIsNone(flake["wafer_id"])
        replacement_uid = self.db.create_flake(
            "flake-edit",
            wafer["wafer_id"],
            material="Replacement Graphene",
        )
        self.assertNotEqual(replacement_uid, flake_uid)

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
