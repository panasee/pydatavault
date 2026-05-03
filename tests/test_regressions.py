import importlib
import json
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

            def load_grid(self):
                refreshed.append(("grid",))

        self.wafer_widget.WaferWidget._save_wafer_metadata(
            DummyWidget(),
            "graphene-rich",
            "Graphene",
            "good contrast near center",
        )

        updated = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(updated["label"], "graphene-rich")
        self.assertEqual(updated["material"], "Graphene")
        self.assertEqual(updated["notes"], "good contrast near center")
        self.assertEqual(refreshed[0], ("flakes", updated))
        self.assertEqual(refreshed[1], ("refs", updated))
        self.assertEqual(refreshed[2], ("grid",))

    def test_wafer_material_updates_attached_flakes(self):
        box_id = self.db.create_box("Box Material")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        attached_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="")
        used_uid = self.db.create_flake("bf2", wafer["wafer_id"], material="Graphene")
        self.db.create_project("proj-material", "Project Material")
        self.db.create_device_with_layers(
            "device-material",
            "proj-material",
            [{"layer_name": "channel", "flake_uid": used_uid}],
        )

        self.db.update_wafer(wafer["wafer_id"], material="hBN")

        updated_wafer = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(updated_wafer["material"], "hBN")
        self.assertEqual(self.db.get_flake(attached_uid)["material"], "hBN")
        self.assertEqual(self.db.get_flake(used_uid)["material"], "Graphene")

    def test_wafer_grid_summary_includes_material(self):
        box_id = self.db.create_box("Box Grid Material")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.update_wafer(wafer["wafer_id"], material="Graphene")
        self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")

        summary = self.db.get_wafer_grid_summary(box_id)

        self.assertEqual(summary[(0, 0)]["count"], 1)
        self.assertEqual(summary[(0, 0)]["material"], "Graphene")

    def test_wafer_grid_summary_keeps_material_without_flakes(self):
        box_id = self.db.create_box("Box Grid Material Only")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.update_wafer(wafer["wafer_id"], material="Graphene")

        summary = self.db.get_wafer_grid_summary(box_id)

        self.assertEqual(summary[(0, 0)]["count"], 0)
        self.assertEqual(summary[(0, 0)]["material"], "Graphene")

    def test_wafer_grid_display_info_keeps_material_without_flakes(self):
        grid = self.wafer_widget.WaferGridView()
        try:
            grid.set_grid(
                1,
                2,
                {
                    (0, 0): {"count": 2, "material": "Graphene"},
                    (0, 1): {"count": 0, "material": "hBN"},
                },
            )

            self.assertEqual(grid._cell_display_info(0, 0), (2, "Graphene"))
            self.assertEqual(grid._cell_display_info(0, 1), (0, "hBN"))
        finally:
            grid.close()

    def test_wafer_grid_display_info_omits_blank_material(self):
        grid = self.wafer_widget.WaferGridView()
        try:
            grid.set_grid(1, 1, {(0, 0): {"count": 0, "material": ""}})

            self.assertEqual(grid._cell_display_info(0, 0), (0, ""))
        finally:
            grid.close()

    def test_add_flake_inherits_current_wafer_material(self):
        box_id = self.db.create_box("Box Add Material")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.update_wafer(wafer["wafer_id"], material="Graphene")

        class DummyDialog:
            def __init__(self, wafer_id, parent=None):
                self.wafer_id = wafer_id

            def exec(self):
                return self.wafer_widget.QDialog.Accepted

            def get_data(self):
                return {
                    "flake_id": "bf1",
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
                pass

            def load_grid(self):
                pass

        DummyDialog.wafer_widget = self.wafer_widget
        with mock.patch.object(self.wafer_widget, "AddFlakeDialog", DummyDialog):
            self.wafer_widget.WaferWidget.add_flake(DummyWidget())

        flakes = self.db.get_flakes_for_wafer(wafer["wafer_id"])
        self.assertEqual(flakes[0]["material"], "Graphene")

    def test_create_flake_persists_extra_photos_json(self):
        box_id = self.db.create_box("Box Extra Photos DB")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)

        flake_uid = self.db.create_flake(
            "bf1",
            wafer["wafer_id"],
            material="Graphene",
            extra_photos='["extra-a.png", "extra-b.png"]',
        )

        self.assertEqual(
            self.db.get_flake(flake_uid)["extra_photos"],
            '["extra-a.png", "extra-b.png"]',
        )

    def test_add_flake_copies_extra_photos_to_managed_directory(self):
        box_id = self.db.create_box("Box Add Extra Photos")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        source_a = self.root_path / "source extra a.png"
        source_b = self.root_path / "source extra b.png"
        source_a.write_bytes(b"extra a")
        source_b.write_bytes(b"extra b")

        class DummyDialog:
            def __init__(self, wafer_id, parent=None):
                self.wafer_id = wafer_id

            def exec(self):
                return self.wafer_widget.QDialog.Accepted

            def get_data(self):
                return {
                    "flake_id": "bf1",
                    "thickness": "",
                    "magnification": "",
                    "photo_path": None,
                    "extra_photo_paths": [str(source_a), str(source_b)],
                    "coord_x": 0.0,
                    "coord_y": 0.0,
                    "notes": "",
                }

        class DummyWidget:
            current_box_id = box_id
            current_wafer_id = wafer["wafer_id"]
            grid_view = mock.Mock(selected_cell=None)

            def load_flakes_for_wafer(self, wafer_dict):
                pass

            def load_grid(self):
                pass

        DummyDialog.wafer_widget = self.wafer_widget
        with mock.patch.object(self.wafer_widget, "AddFlakeDialog", DummyDialog):
            self.wafer_widget.WaferWidget.add_flake(DummyWidget())

        flake = self.db.get_flakes_for_wafer(wafer["wafer_id"])[0]
        extra_photos = json.loads(flake["extra_photos"])
        self.assertEqual(len(extra_photos), 2)
        for path in extra_photos:
            copied = Path(path)
            self.assertTrue(copied.exists())
            self.assertEqual(copied.parents[1], self.config.FLAKES_DIR / str(flake["flake_uid"]))

    def test_flake_table_uses_extra_photo_column(self):
        box_id = self.db.create_box("Box Extra Photos Table")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        extra_photo = self.root_path / "table-extra.png"
        extra_photo.write_bytes(b"extra")
        empty_uid = self.db.create_flake("empty", wafer["wafer_id"], material="Graphene")
        extra_uid = self.db.create_flake(
            "extra",
            wafer["wafer_id"],
            material="Graphene",
            extra_photos=json.dumps([str(extra_photo)]),
        )

        widget = self.wafer_widget.WaferWidget()
        try:
            widget.load_flakes_for_wafer(wafer)

            headers = [
                widget.flake_table.horizontalHeaderItem(i).text()
                for i in range(widget.flake_table.columnCount())
            ]
            self.assertEqual(headers[4], "Extra Photos")
            rows = {
                widget.flake_table.item(row, 0).data(self.wafer_widget.Qt.UserRole): row
                for row in range(widget.flake_table.rowCount())
            }
            self.assertEqual(widget.flake_table.item(rows[empty_uid], 4).text(), "EMPTY")
            self.assertEqual(widget.flake_table.item(rows[extra_uid], 4).text(), "")
            show_cell = widget.flake_table.cellWidget(rows[extra_uid], 4)
            self.assertIsNotNone(show_cell)
            show_button = show_cell.findChild(
                self.wafer_widget.QPushButton, "extraPhotoShowButton"
            )
            self.assertIsNotNone(show_button)
            self.assertEqual(show_button.minimumHeight(), 14)
            self.assertEqual(show_button.maximumHeight(), 14)
            self.assertIn("max-height: 14px", show_button.styleSheet())
        finally:
            widget.close()

    def test_extra_photo_thumbnail_double_click_opens_file(self):
        extra_photo = self.root_path / "thumbnail-extra.png"
        extra_photo.write_bytes(b"extra")
        thumbnail = self.wafer_widget.ExtraPhotoThumbnail(str(extra_photo))
        try:
            with mock.patch.object(
                self.wafer_widget.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as open_url:
                thumbnail.mouseDoubleClickEvent(None)

            open_url.assert_called_once()
            self.assertEqual(
                Path(open_url.call_args.args[0].toLocalFile()),
                extra_photo,
            )
        finally:
            thumbnail.close()

    def test_coordinate_diagram_thumbnails_are_larger(self):
        self.assertEqual(self.wafer_widget.WaferDiagramWidget.THUMB_W, 108)
        self.assertEqual(self.wafer_widget.WaferDiagramWidget.THUMB_H, 81)

    def test_coordinate_diagram_reverses_x_only_for_drawing(self):
        diagram = self.wafer_widget.WaferDiagramWidget(
            [{"x": -1.0, "y": 0.0}, {"x": 1.0, "y": 0.0}],
            [],
        )
        try:
            diagram.resize(400, 300)
            diagram._compute_layout()
            left = diagram._to_screen(-1.0, 0.0)
            right = diagram._to_screen(1.0, 0.0)

            self.assertGreater(left.x(), right.x())
            self.assertEqual(diagram.ref_points[0]["x"], -1.0)
            self.assertEqual(diagram.ref_points[1]["x"], 1.0)
        finally:
            diagram.close()

    def test_add_flake_dialog_has_no_material_input(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            self.assertFalse(hasattr(dialog, "material_input"))
            self.assertNotIn("material", dialog.get_data())
        finally:
            dialog.close()

    def test_add_flake_dialog_collects_extra_photo_paths(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            paths = ["extra-1.png", "extra-2.png"]
            with mock.patch.object(
                self.wafer_widget.QFileDialog,
                "getOpenFileNames",
                return_value=(paths, ""),
            ):
                dialog.select_extra_photos()

            self.assertEqual(dialog.get_data()["extra_photo_paths"], paths)
            self.assertEqual(dialog.extra_photo_label.text(), "2 selected")
        finally:
            dialog.close()

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

    def test_flake_material_column_is_display_only(self):
        box_id = self.db.create_box("Box Display Material")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.update_wafer(wafer["wafer_id"], material="Graphene")
        self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")
        wafer = self.db.get_wafer_by_id(wafer["wafer_id"])

        widget = self.wafer_widget.WaferWidget()
        try:
            widget.load_flakes_for_wafer(wafer)
            material_item = widget.flake_table.item(0, 1)
            self.assertFalse(material_item.flags() & self.wafer_widget.Qt.ItemIsEditable)
        finally:
            widget.close()

    def test_flake_table_update_does_not_overwrite_material(self):
        box_id = self.db.create_box("Box Preserve Material")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")

        widget = self.wafer_widget.WaferWidget()
        try:
            widget.current_wafer_id = wafer["wafer_id"]
            widget.load_flakes_for_wafer(wafer)
            widget.flake_table.item(0, 1).setText("hBN")
            widget.flake_table.item(0, 2).setText("12 nm")
            widget.on_flake_cell_changed(widget.flake_table.item(0, 2))

            flake = self.db.get_flake(flake_uid)
            self.assertEqual(flake["material"], "Graphene")
            self.assertEqual(flake["thickness"], "12 nm")
        finally:
            widget.close()

    def test_view_photo_uses_qt_desktop_services_for_local_file(self):
        box_id = self.db.create_box("Box View Photo")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        photo_path = self.root_path / "photo-view.png"
        photo_path.write_bytes(b"fake image")
        flake_uid = self.db.create_flake(
            "bf1",
            wafer["wafer_id"],
            material="Graphene",
            photo_path=str(photo_path),
        )

        widget = self.wafer_widget.WaferWidget()
        try:
            widget.current_wafer_id = wafer["wafer_id"]
            widget.flake_table.setRowCount(1)
            item = self.wafer_widget.QTableWidgetItem("bf1")
            item.setData(self.wafer_widget.Qt.UserRole, flake_uid)
            widget.flake_table.setItem(0, 0, item)
            widget.flake_table.selectRow(0)

            with mock.patch.object(
                self.wafer_widget.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as open_url:
                widget.view_photo()

            open_url.assert_called_once()
            self.assertEqual(
                Path(open_url.call_args.args[0].toLocalFile()),
                photo_path,
            )
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

    def test_delete_flake_removes_managed_photo_directory(self):
        box_id = self.db.create_box("Box Delete Photo")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")
        flake_dir = self.config.FLAKES_DIR / str(flake_uid)
        flake_dir.mkdir(parents=True, exist_ok=True)
        photo_path = flake_dir / "flake.png"
        photo_path.write_bytes(b"fake image")
        self.db.update_flake(flake_uid, photo_path=str(photo_path))

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
                pass

            def load_grid(self):
                pass

        with mock.patch.object(
            self.wafer_widget.QMessageBox,
            "question",
            return_value=self.wafer_widget.QMessageBox.Yes,
        ):
            self.wafer_widget.WaferWidget.delete_flake(DummyWidget())

        self.assertIsNone(self.db.get_flake(flake_uid))
        self.assertFalse(flake_dir.exists())

    def test_delete_flake_does_not_remove_external_photo_path(self):
        external_path = self.root_path / "external-original.png"
        external_path.write_bytes(b"original image")

        self.wafer_widget.WaferWidget._delete_managed_flake_files(
            999,
            {"photo_path": str(external_path)},
        )

        self.assertTrue(external_path.exists())

    def test_delete_flake_ignores_managed_directory_access_denied(self):
        box_id = self.db.create_box("Box Delete Locked Photo")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")
        flake_dir = self.config.FLAKES_DIR / str(flake_uid)
        flake_dir.mkdir(parents=True, exist_ok=True)
        photo_path = flake_dir / "flake.png"
        photo_path.write_bytes(b"fake image")
        self.db.update_flake(flake_uid, photo_path=str(photo_path))

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
                pass

            def load_grid(self):
                pass

        with mock.patch.object(
            self.wafer_widget.QMessageBox,
            "question",
            return_value=self.wafer_widget.QMessageBox.Yes,
        ), mock.patch.object(
            self.wafer_widget.QMessageBox,
            "critical",
        ) as critical, mock.patch.object(
            self.wafer_widget.shutil,
            "rmtree",
            side_effect=PermissionError(5, "Access is denied", str(flake_dir)),
        ):
            self.wafer_widget.WaferWidget.delete_flake(DummyWidget())

        self.assertIsNone(self.db.get_flake(flake_uid))
        critical.assert_not_called()

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
        self.assertEqual(flake["extra_photos"], "[]")
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

    def test_delete_device_ignores_fabrication_directory_access_denied(self):
        self.db.create_project("proj-delete", "Project Delete")
        self.db.create_device("device-denied", "proj-delete")
        fab_dir = (
            self.config.PROJECTS_DIR
            / "proj-delete"
            / "fabrication"
            / "device-denied"
        )
        fab_dir.mkdir(parents=True, exist_ok=True)

        class DummyItem:
            def text(self):
                return "device-denied"

        class DummyTable:
            def currentRow(self):
                return 0

            def item(self, row, col):
                return DummyItem()

        class DummyWidget:
            current_project_id = "proj-delete"
            device_table = DummyTable()

            def load_devices(self, project_id):
                pass

        with mock.patch.object(
            self.project_widget.QMessageBox,
            "question",
            return_value=self.project_widget.QMessageBox.Yes,
        ), mock.patch.object(
            self.project_widget.QMessageBox,
            "critical",
        ) as critical, mock.patch.object(
            self.project_widget.QMessageBox,
            "information",
        ), mock.patch.object(
            self.project_widget.shutil,
            "rmtree",
            side_effect=PermissionError(5, "Access is denied", str(fab_dir)),
        ):
            self.project_widget.ProjectWidget.on_delete_device(DummyWidget())

        self.assertIsNone(self.db.get_device("device-denied"))
        critical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
