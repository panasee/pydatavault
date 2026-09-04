import importlib
import json
import math
import os
import sqlite3
import sys
import tarfile
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
        cls.flake_layer_tool = importlib.import_module("pydatavault.flake_layer_tool")
        cls.image_processor = importlib.import_module(
            "pydatavault.microscope_image_processor"
        )
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
        calibration_db_file = getattr(self.config, "FLAKE_CALIBRATION_DB_FILE", None)
        if calibration_db_file and calibration_db_file.exists():
            calibration_db_file.unlink()
        preferences_file = getattr(self.config, "PREFERENCES_FILE", None)
        if preferences_file and preferences_file.exists():
            preferences_file.unlink()
        self.db.init_db()

    def test_database_exposes_summary_counts_for_main_window(self):
        box_id = self.db.create_box("Box A")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        self.db.create_flake("flake-1", wafer["wafer_id"], material="Graphene")
        self.db.create_project("proj", "Project")
        self.db.create_device("device-1", "proj")

        self.assertEqual(self.db.count_flakes(), 1)
        self.assertEqual(self.db.count_devices(), 1)

    def test_missing_vault_db_path_raises_runtime_error(self):
        original = os.environ.pop("VAULT_DB_PATH", None)
        try:
            with self.assertRaisesRegex(RuntimeError, "VAULT_DB_PATH"):
                self.config.get_root_path()
        finally:
            if original is not None:
                os.environ["VAULT_DB_PATH"] = original

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

    def test_move_wafer_to_empty_position_preserves_data_and_clears_source(self):
        source_box = self.db.create_box("Move Source", rows=2, cols=2)
        target_box = self.db.create_box("Move Target", rows=2, cols=2)
        wafer = self.db.get_or_create_wafer(source_box, 0, 0)
        self.db.update_wafer(
            wafer["wafer_id"],
            label="sample",
            material="Graphene",
            ref_points='[{"photo_path":"shared/wafer_refs/1/ref.png","x":1,"y":2}]',
            notes="keep me",
        )
        flake_uid = self.db.create_flake(
            "bf1",
            wafer["wafer_id"],
            material="Graphene",
            photo_path="shared/flakes/1/bf1.png",
            extra_photos='["shared/flakes/1/extra/a.png"]',
        )

        self.db.move_wafer(wafer["wafer_id"], target_box, 1, 1)

        moved = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(moved["box_id"], target_box)
        self.assertEqual((moved["row"], moved["col"]), (1, 1))
        self.assertEqual(moved["label"], "sample")
        self.assertEqual(moved["material"], "Graphene")
        self.assertEqual(moved["notes"], "keep me")
        self.assertIn("shared/wafer_refs/1/ref.png", moved["ref_points"])
        self.assertEqual(self.db.get_flake(flake_uid)["wafer_id"], wafer["wafer_id"])

        self.assertNotIn((0, 0), self.db.get_wafer_grid_summary(source_box))
        target_summary = self.db.get_wafer_grid_summary(target_box)
        self.assertEqual(target_summary[(1, 1)]["count"], 1)
        self.assertEqual(target_summary[(1, 1)]["material"], "Graphene")

    def test_move_wafer_rejects_occupied_target_and_keeps_original_position(self):
        source_box = self.db.create_box("Occupied Source", rows=2, cols=2)
        target_box = self.db.create_box("Occupied Target", rows=2, cols=2)
        wafer = self.db.get_or_create_wafer(source_box, 0, 0)
        occupied = self.db.get_or_create_wafer(target_box, 1, 1)
        self.db.update_wafer(occupied["wafer_id"], label="occupied")
        flake_uid = self.db.create_flake("bf1", wafer["wafer_id"], material="Graphene")

        with self.assertRaisesRegex(ValueError, "already contains"):
            self.db.move_wafer(wafer["wafer_id"], target_box, 1, 1)

        unchanged = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(unchanged["box_id"], source_box)
        self.assertEqual((unchanged["row"], unchanged["col"]), (0, 0))
        self.assertEqual(self.db.get_flake(flake_uid)["wafer_id"], wafer["wafer_id"])
        self.assertEqual(
            self.db.get_wafer_by_id(occupied["wafer_id"])["box_id"],
            target_box,
        )

    def test_move_wafer_allows_blank_placeholder_target(self):
        source_box = self.db.create_box("Placeholder Source", rows=2, cols=2)
        target_box = self.db.create_box("Placeholder Target", rows=2, cols=2)
        wafer = self.db.get_or_create_wafer(source_box, 0, 0)
        placeholder = self.db.get_or_create_wafer(target_box, 1, 1)

        self.db.move_wafer(wafer["wafer_id"], target_box, 1, 1)

        self.assertIsNone(self.db.get_wafer_by_id(placeholder["wafer_id"]))
        moved = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(moved["box_id"], target_box)
        self.assertEqual((moved["row"], moved["col"]), (1, 1))

    def test_occupied_wafer_positions_ignore_blank_placeholders(self):
        box_id = self.db.create_box("Move Occupancy", rows=2, cols=2)
        blank = self.db.get_or_create_wafer(box_id, 0, 0)
        occupied = self.db.get_or_create_wafer(box_id, 1, 1)
        self.db.update_wafer(occupied["wafer_id"], label="occupied")

        occupied_positions = self.db.get_occupied_wafer_positions(box_id)

        self.assertNotIn((blank["row"], blank["col"]), occupied_positions)
        self.assertIn((1, 1), occupied_positions)

    def test_move_wafer_allows_target_after_last_flake_is_deleted(self):
        source_box = self.db.create_box("Deleted Flake Source", rows=2, cols=2)
        target_box = self.db.create_box("Deleted Flake Target", rows=2, cols=2)
        wafer = self.db.get_or_create_wafer(source_box, 0, 0)
        target = self.db.get_or_create_wafer(target_box, 1, 1)
        flake_uid = self.db.create_flake("deleted", target["wafer_id"], material="Graphene")
        self.db.delete_flake(flake_uid)

        self.db.move_wafer(wafer["wafer_id"], target_box, 1, 1)

        self.assertIsNone(self.db.get_wafer_by_id(target["wafer_id"]))
        moved = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(moved["box_id"], target_box)
        self.assertEqual((moved["row"], moved["col"]), (1, 1))

    def test_move_wafer_allows_target_after_last_flake_is_used(self):
        source_box = self.db.create_box("Used Flake Source", rows=2, cols=2)
        target_box = self.db.create_box("Used Flake Target", rows=2, cols=2)
        wafer = self.db.get_or_create_wafer(source_box, 0, 0)
        target = self.db.get_or_create_wafer(target_box, 1, 1)
        flake_uid = self.db.create_flake("used", target["wafer_id"], material="Graphene")
        self.db.create_project("proj-used-release", "Project Used Release")
        self.db.create_device_with_layers(
            "device-used-release",
            "proj-used-release",
            [{"layer_name": "channel", "flake_uid": flake_uid}],
        )

        self.db.move_wafer(wafer["wafer_id"], target_box, 1, 1)

        self.assertIsNone(self.db.get_wafer_by_id(target["wafer_id"]))
        moved = self.db.get_wafer_by_id(wafer["wafer_id"])
        self.assertEqual(moved["box_id"], target_box)
        self.assertEqual((moved["row"], moved["col"]), (1, 1))
        used_flake = self.db.get_flake(flake_uid)
        self.assertEqual(used_flake["status"], "used")
        self.assertIsNone(used_flake["wafer_id"])

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

    def test_main_window_file_menu_exposes_preferences_and_backup_actions(self):
        window = self.main_window.MainWindow()
        try:
            action_texts = [action.text() for action in window.file_menu.actions()]

            self.assertIn("Preferences", action_texts)
            self.assertIn("Backup...", action_texts)
        finally:
            window.close()

    def test_main_window_tools_menu_exposes_flake_layer_analyzer(self):
        window = self.main_window.MainWindow()
        try:
            action_texts = [action.text() for action in window.tools_menu.actions()]

            self.assertIn("Flake Layer Analyzer...", action_texts)
            self.assertIn("Microscope Image Processor...", action_texts)
        finally:
            window.close()

    def test_image_processor_white_balance_neutralizes_reference_region(self):
        rgb = self.image_processor.np.array(
            [
                [[120, 60, 30], [120, 60, 30]],
                [[120, 60, 30], [120, 60, 30]],
            ],
            dtype=self.image_processor.np.uint8,
        )

        adjusted = self.image_processor.apply_image_adjustments(
            rgb,
            white_balance_rect=(0, 0, 2, 2),
        )

        self.assertLessEqual(int(adjusted[0, 0].max() - adjusted[0, 0].min()), 1)

    def test_image_processor_auto_brightness_normalizes_reference_to_130(self):
        rgb = self.image_processor.np.array(
            [
                [[120, 60, 30], [120, 60, 30]],
                [[20, 40, 80], [20, 40, 80]],
            ],
            dtype=self.image_processor.np.uint8,
        )

        adjusted = self.image_processor.apply_image_adjustments(
            rgb,
            white_balance_rect=(0, 0, 2, 1),
            auto_brightness_target=130,
        )

        self.assertLessEqual(abs(float(adjusted[0].mean()) - 130.0), 1.0)
        self.assertLessEqual(int(adjusted[0, 0].max() - adjusted[0, 0].min()), 1)

    def test_image_processor_rgb_profile_samples_selected_path(self):
        rgb = self.image_processor.np.array(
            [[[10, 20, 30], [40, 50, 60], [70, 80, 90]]],
            dtype=self.image_processor.np.uint8,
        )

        distances, values = self.image_processor.sample_rgb_profile(
            rgb,
            (0.0, 0.0),
            (2.0, 0.0),
        )

        self.assertEqual(distances.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(values.tolist(), rgb[0].astype(float).tolist())

    def test_image_processor_plotly_updates_use_independent_javascript_scope(self):
        figure = self.image_processor.go.Figure()

        script = self.image_processor.plotly_react_script(
            figure,
            {"responsive": True},
        )

        self.assertTrue(script.startswith("(() => {"))
        self.assertTrue(script.endswith("})();"))
        self.assertIn("Plotly.react('rgb-profile'", script)

    def test_image_processor_qimage_round_trip_preserves_rgb(self):
        source = self.flake_layer_tool.QImage(
            2,
            2,
            self.flake_layer_tool.QImage.Format_RGB32,
        )
        source.fill(self.flake_layer_tool.QColor(12, 34, 56))

        rgb = self.image_processor.qimage_to_rgb_array(source)
        restored = self.image_processor.rgb_array_to_qimage(rgb)

        self.assertEqual(restored.pixelColor(0, 0).getRgb()[:3], (12, 34, 56))

    def test_image_processor_dialog_reprocesses_and_updates_profile(self):
        dialog = self.image_processor.MicroscopeImageProcessorDialog()
        try:
            dialog.original_rgb = self.image_processor.np.full(
                (8, 12, 3),
                (20, 40, 60),
                dtype=self.image_processor.np.uint8,
            )
            dialog.white_balance_rect = (0, 0, 4, 4)
            dialog.profile_path = ((0.0, 0.0), (11.0, 0.0))

            dialog._reprocess(fit=True)

            self.assertFalse(dialog.processed_image.isNull())
            self.assertTrue(dialog.auto_brightness_check.isChecked())
            self.assertEqual(dialog.auto_brightness_target_spin.value(), 130)
            self.assertEqual(dialog.profile_plot.values.shape, (12, 3))
            figure = dialog.profile_plot._build_figure()
            self.assertEqual([trace.name for trace in figure.data], ["R", "G", "B"])
            self.assertEqual(tuple(figure.layout.yaxis.range), (0, 255))
            self.assertTrue(dialog.profile_plot.CONFIG["scrollZoom"])
            self.assertLessEqual(
                int(
                    dialog.processed_rgb[0, 0].max()
                    - dialog.processed_rgb[0, 0].min()
                ),
                1,
            )
        finally:
            dialog.close()

    def test_flake_layer_contrast_and_nearest_calibration(self):
        contrast = self.flake_layer_tool.rgb_optical_contrast(
            (200.0, 160.0, 100.0),
            (190.0, 144.0, 80.0),
        )
        self.assertEqual(contrast, (5.0, 10.0, 20.0))

        entries = [
            self.flake_layer_tool.CalibrationEntry(1, (4.0, 9.0, 19.0)),
            self.flake_layer_tool.CalibrationEntry(2, (9.0, 14.0, 24.0)),
        ]
        match, distance, margin = self.flake_layer_tool.nearest_calibration(
            contrast,
            entries,
            (1.0, 1.0, 1.0),
        )

        self.assertEqual(match.layers, 1)
        self.assertAlmostEqual(distance, math.sqrt(3))
        self.assertGreater(margin, 0)

    def test_flake_layer_learns_channel_weights_from_repeated_photos(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        samples = []
        layer_values = {
            1: [(80.0, 70.0, 78.0), (80.1, 80.0, 80.0), (79.9, 60.0, 82.0)],
            2: [(70.0, 60.0, 68.0), (70.1, 70.0, 70.0), (69.9, 50.0, 72.0)],
        }
        for layers, values in layer_values.items():
            for photo_index, normalized_rgb in enumerate(values):
                samples.append(
                    sample_type(
                        layers,
                        normalized_rgb,
                        f"layer-{layers}-photo-{photo_index}.png",
                        (1.0, 1.0, 1.0),
                        (1.0, 1.0, 1.0),
                    )
                )

        weights = self.flake_layer_tool.calibration_channel_weights(samples)

        self.assertAlmostEqual(sum(weights), 3.0)
        self.assertGreater(weights[0], weights[2])
        self.assertGreater(weights[2], weights[1])

    def test_flake_layer_allows_one_calibrated_layer_with_limited_confidence(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        samples = [
            sample_type(
                1,
                normalized_rgb,
                f"sha256:{photo_index:064x}",
                (1.0, 1.0, 1.0),
                (1.0, 1.0, 1.0),
            )
            for photo_index, normalized_rgb in enumerate(
                [(80.0, 70.0, 90.0), (80.1, 72.0, 89.0), (79.9, 68.0, 91.0)],
                start=1,
            )
        ]
        entries = self.flake_layer_tool.calibration_centroids(samples)
        weights = self.flake_layer_tool.calibration_channel_weights(samples)
        match, distance, margin = self.flake_layer_tool.nearest_calibration(
            entries[0].normalized_rgb,
            entries,
            weights,
        )

        confidence, reason = self.flake_layer_tool.calibration_confidence(
            match,
            distance,
            margin,
            weights,
            samples,
        )

        self.assertEqual(match.layers, 1)
        self.assertEqual(confidence, "Limited")
        self.assertIn("other layer counts cannot be excluded", reason)

    def test_flake_layer_marks_sample_outside_single_layer_spread_as_low_confidence(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        samples = [
            sample_type(
                1,
                normalized_rgb,
                f"sha256:{photo_index:064x}",
                (1.0, 1.0, 1.0),
                (1.0, 1.0, 1.0),
            )
            for photo_index, normalized_rgb in enumerate(
                [(80.0, 70.0, 90.0), (80.1, 70.1, 90.1), (79.9, 69.9, 89.9)],
                start=1,
            )
        ]
        entries = self.flake_layer_tool.calibration_centroids(samples)
        weights = self.flake_layer_tool.calibration_channel_weights(samples)
        match, distance, margin = self.flake_layer_tool.nearest_calibration(
            (60.0, 50.0, 70.0),
            entries,
            weights,
        )

        confidence, reason = self.flake_layer_tool.calibration_confidence(
            match,
            distance,
            margin,
            weights,
            samples,
        )

        self.assertEqual(confidence, "Low")
        self.assertIn("uncalibrated layer count", reason)

    def test_flake_layer_weighted_distance_uses_learned_channel_priority(self):
        entries = [
            self.flake_layer_tool.CalibrationEntry(1, (0.0, 10.0, 0.0)),
            self.flake_layer_tool.CalibrationEntry(2, (8.0, 0.0, 0.0)),
        ]

        match, _distance, _margin = self.flake_layer_tool.nearest_calibration(
            (0.0, 0.0, 0.0),
            entries,
            (2.8, 0.1, 0.1),
        )

        self.assertEqual(match.layers, 1)

    def test_flake_layer_weight_learning_requires_three_photos_per_layer(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        samples = [
            sample_type(
                layers,
                (80.0 - layers, 80.0, 80.0),
                f"layer-{layers}-photo-{photo}.png",
                (1.0, 1.0, 1.0),
                (1.0, 1.0, 1.0),
            )
            for layers in (1, 2)
            for photo in (1, 2)
        ]

        with self.assertRaisesRegex(ValueError, "at least 3 different photos"):
            self.flake_layer_tool.calibration_channel_weights(samples)

    def test_flake_layer_substrate_normalization_removes_channel_gain(self):
        normalized_a = self.flake_layer_tool.substrate_normalized_rgb(
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )
        normalized_b = self.flake_layer_tool.substrate_normalized_rgb(
            (150.0, 160.0, 200.0),
            (120.0, 112.0, 180.0),
        )

        self.assertEqual(normalized_a, (80.0, 70.0, 90.0))
        self.assertEqual(normalized_b, normalized_a)
        self.assertIn(
            "saturation",
            self.flake_layer_tool.substrate_quality_warning((251.0, 150.0, 120.0)),
        )

    def test_flake_layer_known_regions_create_layer_centroids(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        samples = [
            sample_type(1, (80.0, 82.0, 84.0), "a.png", (1, 1, 1), (1, 1, 1)),
            sample_type(1, (82.0, 84.0, 86.0), "b.png", (1, 1, 1), (1, 1, 1)),
            sample_type(2, (70.0, 72.0, 74.0), "a.png", (1, 1, 1), (1, 1, 1)),
        ]

        entries = self.flake_layer_tool.calibration_centroids(samples)

        self.assertEqual([entry.layers for entry in entries], [1, 2])
        self.assertEqual(entries[0].normalized_rgb, (81.0, 83.0, 85.0))
        self.assertEqual(entries[1].normalized_rgb, (70.0, 72.0, 74.0))

    def test_flake_layer_calibration_database_round_trip_preserves_samples(self):
        sample = self.flake_layer_tool.CalibrationSample(
            2,
            (80.0, 70.0, 90.0),
            "known.png",
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )

        store_path = self.root_path / ".labdb" / "calibration-round-trip.db"
        if store_path.exists():
            store_path.unlink()
        store = self.flake_layer_tool.FlakeCalibrationStore(store_path)

        material_id = store.save_calibration(
            "Graphene",
            "285 nm SiO2/Si",
            [sample],
        )
        materials = store.list_materials()
        restored = store.get_samples(material_id)

        self.assertEqual(materials[0]["material_name"], "Graphene")
        self.assertEqual(materials[0]["substrate"], "285 nm SiO2/Si")
        self.assertEqual(materials[0]["sample_count"], 1)
        self.assertEqual(restored, [sample])

        invalid = self.flake_layer_tool.CalibrationSample(
            2,
            (81.0, 70.0, 90.0),
            "known.png",
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            store.save_calibration(
                "Graphene",
                "285 nm SiO2/Si",
                [invalid],
                material_id,
            )

        self.assertEqual(store.get_samples(material_id), [sample])

    def test_flake_layer_database_separates_same_material_on_different_substrates(self):
        sample_type = self.flake_layer_tool.CalibrationSample
        sample_285 = sample_type(
            1,
            (80.0, 70.0, 90.0),
            "known-285.png",
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )
        sample_90 = sample_type(
            1,
            (75.0, 65.0, 85.0),
            "known-90.png",
            (100.0, 80.0, 100.0),
            (75.0, 52.0, 85.0),
        )
        store_path = self.root_path / ".labdb" / "calibration-substrates.db"
        if store_path.exists():
            store_path.unlink()
        store = self.flake_layer_tool.FlakeCalibrationStore(store_path)

        material_285_id = store.save_calibration(
            "Graphene", "285 nm SiO2/Si", [sample_285]
        )
        material_90_id = store.save_calibration(
            "Graphene", "90 nm SiO2/Si", [sample_90]
        )

        self.assertNotEqual(material_285_id, material_90_id)
        self.assertEqual(len(store.list_materials()), 2)
        self.assertEqual(store.get_samples(material_285_id), [sample_285])
        self.assertEqual(store.get_samples(material_90_id), [sample_90])

    def test_flake_layer_analyzer_selects_saved_material_from_dropdown(self):
        sample = self.flake_layer_tool.CalibrationSample(
            1,
            (80.0, 70.0, 90.0),
            "known.png",
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )
        store_path = self.root_path / ".labdb" / "calibration-dropdown.db"
        if store_path.exists():
            store_path.unlink()
        store = self.flake_layer_tool.FlakeCalibrationStore(store_path)
        material_id = store.save_calibration(
            "Graphene",
            "285 nm SiO2/Si",
            [sample],
        )

        dialog = self.flake_layer_tool.FlakeLayerAnalyzerDialog(store=store)
        try:
            self.assertEqual(dialog.material_combo.count(), 1)
            self.assertEqual(dialog.material_combo.currentData(), material_id)
            self.assertEqual(
                dialog.material_combo.currentText(),
                "Graphene — 285 nm SiO2/Si",
            )
            self.assertEqual(dialog.calibration_samples, [sample])
            self.assertTrue(dialog.calibration_group.isHidden())
        finally:
            dialog.close()

        manager = self.flake_layer_tool.CalibrationDatabaseDialog(store)
        try:
            self.assertEqual(manager.material_list.count(), 1)
            self.assertEqual(
                manager.material_list.item(0).data(self.flake_layer_tool.Qt.UserRole),
                material_id,
            )
            self.assertEqual(manager.add_material_button.text(), "+")
        finally:
            manager.close()

    def test_flake_layer_existing_material_opens_recalibration_editor(self):
        sample = self.flake_layer_tool.CalibrationSample(
            2,
            (80.0, 70.0, 90.0),
            "known.png",
            (100.0, 80.0, 50.0),
            (80.0, 56.0, 45.0),
        )
        store_path = self.root_path / ".labdb" / "calibration-editor.db"
        if store_path.exists():
            store_path.unlink()
        store = self.flake_layer_tool.FlakeCalibrationStore(store_path)
        material_id = store.save_calibration("hBN", "285 nm SiO2/Si", [sample])

        editor = self.flake_layer_tool.FlakeLayerAnalyzerDialog(
            store=store,
            calibration_mode=True,
            material_id=material_id,
        )
        try:
            self.assertEqual(editor.material_input.text(), "hBN")
            self.assertEqual(editor.substrate_input.text(), "285 nm SiO2/Si")
            self.assertTrue(editor.material_input.isReadOnly())
            self.assertTrue(editor.substrate_input.isReadOnly())
            self.assertEqual(editor.calibration_table.rowCount(), 1)
            self.assertTrue(editor.result_group.isHidden())
        finally:
            editor.close()

    def test_flake_layer_new_calibration_defaults_blank_substrate(self):
        editor = self.flake_layer_tool.FlakeLayerAnalyzerDialog(
            calibration_mode=True,
        )
        try:
            self.assertEqual(editor.substrate_input.text(), "")
            self.assertIn(
                "285 nm SiO2/Si",
                editor.substrate_input.placeholderText(),
            )
            self.assertEqual(editor._effective_substrate(), "285 nm SiO2/Si")
        finally:
            editor.close()

    def test_flake_layer_dialog_adds_current_region_without_manual_rgb_entry(self):
        dialog = self.flake_layer_tool.FlakeLayerAnalyzerDialog()
        try:
            dialog.image_path = Path("known-layers.png")
            dialog.image = self.flake_layer_tool.QImage(
                4,
                4,
                self.flake_layer_tool.QImage.Format_RGB32,
            )
            dialog.image.fill(self.flake_layer_tool.QColor(20, 30, 40))
            dialog.sample_values = {
                "substrate": (100.0, 80.0, 50.0),
                "flake": (80.0, 56.0, 45.0),
            }
            dialog.sample_points = {"substrate": (1.0, 1.0), "flake": (2.0, 2.0)}
            dialog.known_layers_spin.setValue(3)

            dialog.add_known_region()

            self.assertEqual(len(dialog.calibration_samples), 1)
            self.assertEqual(dialog.calibration_samples[0].layers, 3)
            self.assertEqual(
                dialog.calibration_samples[0].normalized_rgb,
                (80.0, 70.0, 90.0),
            )
            self.assertEqual(dialog.calibration_table.rowCount(), 1)
            self.assertNotIn("flake", dialog.sample_values)

            image = self.flake_layer_tool.QImage(
                10,
                10,
                self.flake_layer_tool.QImage.Format_RGB32,
            )
            dialog._set_working_image(image, is_cropped=False)

            self.assertEqual(dialog.sample_mode, "substrate")
            self.assertTrue(dialog.substrate_button.isChecked())
        finally:
            dialog.close()

    def test_flake_layer_photo_identifier_uses_pixels_not_filename_or_path(self):
        image_a = self.flake_layer_tool.QImage(
            4,
            4,
            self.flake_layer_tool.QImage.Format_RGB32,
        )
        image_b = self.flake_layer_tool.QImage(
            4,
            4,
            self.flake_layer_tool.QImage.Format_RGB32,
        )
        image_a.fill(self.flake_layer_tool.QColor(20, 30, 40))
        image_b.fill(self.flake_layer_tool.QColor(20, 30, 40))

        identifier_a = self.flake_layer_tool.image_content_identifier(image_a)
        identifier_b = self.flake_layer_tool.image_content_identifier(image_b)
        image_b.setPixelColor(0, 0, self.flake_layer_tool.QColor(21, 30, 40))
        identifier_changed = self.flake_layer_tool.image_content_identifier(image_b)

        self.assertEqual(identifier_a, identifier_b)
        self.assertNotEqual(identifier_a, identifier_changed)
        self.assertTrue(identifier_a.startswith("sha256:"))

    def test_flake_layer_image_sampling_uses_circular_roi(self):
        from PySide6.QtGui import QColor, QImage

        image = QImage(5, 5, QImage.Format_RGB32)
        image.fill(QColor(10, 20, 30))
        image.setPixelColor(2, 2, QColor(110, 120, 130))

        sampled = self.flake_layer_tool.mean_rgb_in_circle(image, (2, 2), 1)

        self.assertEqual(sampled, (30.0, 40.0, 50.0))

    def test_flake_layer_crop_returns_independent_bounded_image(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage

        image = QImage(10, 8, QImage.Format_RGB32)
        image.fill(QColor(10, 20, 30))
        image.setPixelColor(3, 2, QColor(100, 110, 120))

        cropped = self.flake_layer_tool.cropped_image(
            image,
            QRectF(3, 2, 4, 3),
        )
        cropped.setPixelColor(0, 0, QColor(1, 2, 3))

        self.assertEqual((cropped.width(), cropped.height()), (4, 3))
        self.assertEqual(image.pixelColor(3, 2), QColor(100, 110, 120))

    def test_flake_layer_view_supports_zoom_and_crop_controls(self):
        from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
        from PySide6.QtGui import QWheelEvent

        dialog = self.flake_layer_tool.FlakeLayerAnalyzerDialog()
        try:
            self.assertTrue(dialog.crop_button.isCheckable())
            self.assertFalse(dialog.apply_crop_button.isEnabled())

            dialog.image = self.flake_layer_tool.QImage(
                20,
                20,
                self.flake_layer_tool.QImage.Format_RGB32,
            )
            dialog.image_view.set_image(dialog.image)
            dialog.image_view.resetTransform()
            zoom_in = QWheelEvent(
                QPointF(10, 10),
                QPointF(10, 10),
                QPoint(),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.ScrollUpdate,
                False,
            )
            dialog.image_view.wheelEvent(zoom_in)

            self.assertAlmostEqual(dialog.image_view.transform().m11(), 1.25)

            scale_before_resize = dialog.image_view.transform().m11()
            dialog.image_view.resize(300, 240)
            self.app.processEvents()

            self.assertAlmostEqual(
                dialog.image_view.transform().m11(),
                scale_before_resize,
            )

            dialog.original_image = dialog.image.copy()
            dialog.image_view._crop_item = dialog.image_view.scene().addRect(
                QRectF(2, 3, 8, 6)
            )
            dialog.apply_crop()

            self.assertEqual((dialog.image.width(), dialog.image.height()), (8, 6))
            self.assertTrue(dialog.reset_image_button.isEnabled())

            dialog.reset_original_image()

            self.assertEqual((dialog.image.width(), dialog.image.height()), (20, 20))
            self.assertFalse(dialog.reset_image_button.isEnabled())
        finally:
            dialog.close()

    def test_flake_layer_wheel_zoom_in_survives_scrollbar_resize(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QImage, QWheelEvent

        dialog = self.flake_layer_tool.FlakeLayerAnalyzerDialog()
        try:
            dialog.show()
            self.app.processEvents()
            image = QImage(1200, 900, QImage.Format_RGB32)
            dialog.image_view.set_image(image)
            initial_scale = dialog.image_view.transform().m11()

            for _ in range(6):
                event = QWheelEvent(
                    QPointF(100, 100),
                    QPointF(100, 100),
                    QPoint(),
                    QPoint(0, 120),
                    Qt.NoButton,
                    Qt.NoModifier,
                    Qt.ScrollUpdate,
                    False,
                )
                dialog.image_view.wheelEvent(event)
                self.app.processEvents()

            self.assertAlmostEqual(
                dialog.image_view.transform().m11(),
                initial_scale * 1.25 ** 6,
            )
        finally:
            dialog.close()

    def test_backup_archive_uses_zstd_and_relative_roots(self):
        backup = importlib.import_module("pydatavault.backup")
        vault_project_file = self.config.PROJECTS_DIR / "proj" / "fabrication" / "dev" / "note.txt"
        vault_project_file.parent.mkdir(parents=True, exist_ok=True)
        vault_project_file.write_text("vault data", encoding="utf-8")
        self.db.create_project("proj", "Project")

        pyflexlab_out = self.root_path / "pyflexlab-out"
        measurement_file = pyflexlab_out / "dev" / "measurement.csv"
        measurement_file.parent.mkdir(parents=True, exist_ok=True)
        measurement_file.write_text("time,value\n0,1\n", encoding="utf-8")

        destination = self.root_path.parent / f"backups_{uuid4().hex}"
        destination.mkdir()

        with mock.patch.object(backup.config, "PYFLEXLAB_OUT_PATH", pyflexlab_out):
            archive_path = backup.create_backup(destination, timestamp="20260511-001500")

        self.assertEqual(archive_path.suffixes[-2:], [".tar", ".zst"])

        import zstandard

        with archive_path.open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                names = [member.name for member in tar]

        self.assertIn("manifest.json", names)
        self.assertIn("vault/.labdb/lab.db", names)
        self.assertIn("vault/projects/proj/fabrication/dev/note.txt", names)
        self.assertIn("pyflexlab_out/dev/measurement.csv", names)
        self.assertFalse(any(str(self.root_path) in name for name in names))

    def test_device_display_order_persists_in_preferences_without_db_column(self):
        self.db.create_project("proj-display-order", "Project Display Order")
        for device_id in ("device-a", "device-b", "device-c"):
            self.db.create_device(device_id, "proj-display-order")

        widget = self.project_widget.ProjectWidget()
        try:
            widget.current_project_id = "proj-display-order"
            widget.load_devices("proj-display-order")

            widget.move_device_display_row(2, 0)

            self.assertEqual(
                [
                    widget.device_table.item(row, 0).data(self.project_widget.Qt.UserRole)
                    for row in range(widget.device_table.rowCount())
                ],
                ["device-c", "device-a", "device-b"],
            )
        finally:
            widget.close()

        preferences = self.config.load_preferences()
        self.assertEqual(
            preferences["device_display_order"]["proj-display-order"],
            ["device-c", "device-a", "device-b"],
        )

        reloaded = self.project_widget.ProjectWidget()
        try:
            reloaded.current_project_id = "proj-display-order"
            reloaded.load_devices("proj-display-order")
            self.assertEqual(
                [
                    reloaded.device_table.item(row, 0).data(self.project_widget.Qt.UserRole)
                    for row in range(reloaded.device_table.rowCount())
                ],
                ["device-c", "device-a", "device-b"],
            )
        finally:
            reloaded.close()

        with self.db.get_conn() as conn:
            device_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(devices)").fetchall()
            }
        self.assertNotIn("display_order", device_columns)

    def test_open_project_button_opens_selected_project_folder(self):
        project_id = "proj-open-folder"
        self.db.create_project(project_id, "Project Open Folder")
        project_dir = self.config.PROJECTS_DIR / project_id
        project_dir.mkdir(parents=True)

        widget = self.project_widget.ProjectWidget()
        try:
            widget.project_list.setCurrentRow(0)

            self.assertEqual(widget.btn_open_project.text(), "Open Project")
            button_layout = widget.btn_open_project.parentWidget().layout().itemAt(2).layout()
            self.assertEqual(
                button_layout.indexOf(widget.btn_open_project),
                button_layout.indexOf(widget.btn_edit_project) + 1,
            )
            with mock.patch.object(
                self.project_widget.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as open_url:
                widget.btn_open_project.click()

            opened_url = open_url.call_args.args[0]
            self.assertEqual(Path(opened_url.toLocalFile()), project_dir)
        finally:
            widget.close()

    def test_device_display_order_survives_qt_move_action_source_cleanup(self):
        self.db.create_project("proj-drag-cleanup", "Project Drag Cleanup")
        for device_id in ("device-a", "device-b", "device-c"):
            self.db.create_device(device_id, "proj-drag-cleanup")

        widget = self.project_widget.ProjectWidget()
        try:
            widget.current_project_id = "proj-drag-cleanup"
            widget.load_devices("proj-drag-cleanup")
            widget.device_table.selectRow(2)

            widget.move_device_display_row(2, 0)

            selected_rows = sorted(
                {index.row() for index in widget.device_table.selectionModel().selectedRows()},
                reverse=True,
            )
            for row in selected_rows:
                widget.device_table.removeRow(row)
            self.app.processEvents()

            self.assertEqual(
                [
                    widget.device_table.item(row, 0).data(self.project_widget.Qt.UserRole)
                    for row in range(widget.device_table.rowCount())
                ],
                ["device-c", "device-a", "device-b"],
            )
        finally:
            widget.close()

    def test_device_table_ignores_drop_on_existing_item(self):
        from PySide6.QtCore import QPoint

        table = self.project_widget.DeviceTableWidget()
        emitted_moves = []
        table.rows_reordered.connect(lambda source, target: emitted_moves.append((source, target)))
        try:
            table.setColumnCount(1)
            for row, device_id in enumerate(("device-a", "device-b")):
                table.insertRow(row)
                table.setItem(row, 0, self.project_widget.QTableWidgetItem(device_id))
            table.selectRow(0)

            class DropOnItemEvent:
                def __init__(self):
                    self.accepted = False
                    self.ignored = False

                def pos(self):
                    return QPoint(1, table.rowHeight(0) + 1)

                def accept(self):
                    self.accepted = True

                def ignore(self):
                    self.ignored = True

            original_drop_indicator_position = table.dropIndicatorPosition
            table.dropIndicatorPosition = lambda: self.project_widget.QAbstractItemView.OnItem
            try:
                event = DropOnItemEvent()
                table.dropEvent(event)
            finally:
                table.dropIndicatorPosition = original_drop_indicator_position

            self.assertTrue(event.ignored)
            self.assertFalse(event.accepted)
            self.assertEqual(emitted_moves, [])
        finally:
            table.close()

    def test_device_display_order_updates_after_device_id_edit(self):
        self.db.create_project("proj-order-rename", "Project Order Rename")
        for device_id in ("device-a", "device-b", "device-c"):
            self.db.create_device(device_id, "proj-order-rename")

        widget = self.project_widget.ProjectWidget()
        try:
            widget.current_project_id = "proj-order-rename"
            widget.load_devices("proj-order-rename")
            widget.move_device_display_row(2, 0)

            widget.device_table.item(0, 0).setText("device-renamed")

            self.assertEqual(
                self.config.load_preferences()["device_display_order"]["proj-order-rename"],
                ["device-renamed", "device-a", "device-b"],
            )
            self.assertIsNone(self.db.get_device("device-c"))
            self.assertIsNotNone(self.db.get_device("device-renamed"))
        finally:
            widget.close()

    def test_preferences_dialog_saves_motion_position_url(self):
        dialog = self.main_window.PreferencesDialog()
        try:
            dialog.motion_position_url_input.setText("http://127.0.0.1:61235/position")
            dialog.accept()

            self.assertEqual(
                self.config.get_motion_position_url(),
                "http://127.0.0.1:61235/position",
            )
        finally:
            dialog.close()

    def test_motion_position_url_defaults_to_local_endpoint(self):
        self.assertEqual(
            self.config.get_motion_position_url(),
            "http://127.0.0.1:51235/position",
        )

    def test_motion_position_url_can_be_saved(self):
        self.config.set_motion_position_url("http://127.0.0.1:61235/position")

        self.assertEqual(
            self.config.get_motion_position_url(),
            "http://127.0.0.1:61235/position",
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

    def test_wafer_grid_can_hide_coordinate_labels_for_move_dialog(self):
        grid = self.wafer_widget.WaferGridView(show_labels=False)
        clicked = []

        class FakeMouseEvent:
            def pos(self):
                return self.wafer_widget.QPoint(10, 10)

        try:
            grid.resize(120, 120)
            grid.set_grid(2, 2, {})
            grid.cell_clicked.connect(lambda row, col: clicked.append((row, col)))

            FakeMouseEvent.wafer_widget = self.wafer_widget
            grid.mousePressEvent(FakeMouseEvent())

            self.assertEqual(clicked, [(0, 0)])
            self.assertEqual(grid.selected_cell, (0, 0))
        finally:
            grid.close()

    def test_wafer_grid_ignores_blocked_move_targets(self):
        grid = self.wafer_widget.WaferGridView(show_labels=False)
        clicked = []

        class FakeMouseEvent:
            def pos(self):
                return self.wafer_widget.QPoint(10, 10)

        try:
            grid.resize(120, 120)
            grid.set_grid(2, 2, {}, blocked_cells={(0, 0)})
            grid.cell_clicked.connect(lambda row, col: clicked.append((row, col)))

            FakeMouseEvent.wafer_widget = self.wafer_widget
            grid.mousePressEvent(FakeMouseEvent())

            self.assertEqual(clicked, [])
            self.assertIsNone(grid.selected_cell)
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
            self.assertFalse(Path(path).is_absolute())
            copied = self.config.resolve_data_path(path)
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

    def test_corrupt_extra_photos_json_warns_in_gui(self):
        widget = self.wafer_widget.WaferWidget()
        try:
            with mock.patch.object(self.wafer_widget.QMessageBox, "warning") as warning:
                paths = widget._extra_photo_paths({"extra_photos": "not json"})

            self.assertEqual(paths, [])
            warning.assert_called_once()
            self.assertIn("extra", warning.call_args.args[2].lower())
        finally:
            widget.close()

    def test_empty_extra_photos_json_does_not_warn(self):
        widget = self.wafer_widget.WaferWidget()
        try:
            with mock.patch.object(self.wafer_widget.QMessageBox, "warning") as warning:
                self.assertEqual(widget._extra_photo_paths({"extra_photos": ""}), [])
                self.assertEqual(widget._extra_photo_paths({"extra_photos": "[]"}), [])
                self.assertEqual(widget._extra_photo_paths({"extra_photos": None}), [])

            warning.assert_not_called()
        finally:
            widget.close()

    def test_coordinate_diagram_thumbnails_are_larger(self):
        self.assertEqual(self.wafer_widget.WaferDiagramWidget.THUMB_W, 108)
        self.assertEqual(self.wafer_widget.WaferDiagramWidget.THUMB_H, 81)

    def test_coordinate_diagram_reverses_both_axes_only_for_drawing(self):
        diagram = self.wafer_widget.WaferDiagramWidget(
            [{"x": -1.0, "y": -1.0}, {"x": 1.0, "y": 1.0}],
            [],
        )
        try:
            diagram.resize(400, 300)
            diagram._compute_layout()
            negative = diagram._to_screen(-1.0, -1.0)
            positive = diagram._to_screen(1.0, 1.0)

            self.assertGreater(negative.x(), positive.x())
            self.assertGreater(negative.y(), positive.y())
            self.assertEqual(diagram.ref_points[0]["x"], -1.0)
            self.assertEqual(diagram.ref_points[0]["y"], -1.0)
            self.assertEqual(diagram.ref_points[1]["x"], 1.0)
            self.assertEqual(diagram.ref_points[1]["y"], 1.0)
        finally:
            diagram.close()

    def test_coordinate_diagram_uses_longest_ref_pair_as_diagonal(self):
        diagram = self.wafer_widget.WaferDiagramWidget(
            [
                {"x": 0.0, "y": 0.0},
                {"x": 10000.0, "y": 0.0},
                {"x": 0.0, "y": 10000.0},
            ],
            [],
        )
        try:
            self.assertEqual(
                diagram._para_vertices(),
                [
                    (0.0, 0.0),
                    (10000.0, 0.0),
                    (10000.0, 10000.0),
                    (0.0, 10000.0),
                ],
            )
        finally:
            diagram.close()

    def test_coordinate_diagram_fits_new_coordinate_unit_scale(self):
        diagram = self.wafer_widget.WaferDiagramWidget(
            [
                {"x": 0.0, "y": 0.0},
                {"x": 10000.0, "y": 0.0},
                {"x": 0.0, "y": 10000.0},
            ],
            [{"flake_id": "Si-point", "coord_x": 4500.0, "coord_y": 6200.0}],
        )
        try:
            diagram.set_new_transform([
                (0, (100.0, 200.0)),
                (1, (110.0, 200.0)),
                (2, (100.0, 190.0)),
            ])
            diagram._compute_layout()

            old_span = math.dist(
                diagram._to_screen(0.0, 0.0).toTuple(),
                diagram._to_screen(10000.0, 0.0).toTuple(),
            )
            new_span = math.dist(
                diagram._to_screen_new(100.0, 200.0).toTuple(),
                diagram._to_screen_new(110.0, 200.0).toTuple(),
            )

            self.assertAlmostEqual(old_span, diagram.TARGET_LONG_EDGE_PX)
            self.assertAlmostEqual(new_span, diagram.TARGET_LONG_EDGE_PX)
            self.assertEqual(
                diagram._to_screen(diagram._cx, diagram._cy),
                diagram._to_screen_new(*diagram._new_center()),
            )
        finally:
            diagram.close()

    def test_rigid_transform_maps_wafer_coordinate_to_target_stage(self):
        result = self.wafer_widget.coord_utils.rigid_transition(
            refs_wafer=[(0.0, 10.0), (0.0, 0.0), (10.0, 0.0)],
            refs_stage=[(30.0, 40.0), (40.0, 40.0), (40.0, 50.0)],
            target_wafer=(1.0, 7.0),
        )

        self.assertAlmostEqual(result[0], 33.0)
        self.assertAlmostEqual(result[1], 41.0)

    def test_affine_transform_handles_reflected_y_axis_and_nonuniform_scale(self):
        result = self.wafer_widget.coord_utils.affine_transition(
            refs_old=[(0.0, 0.0), (10.0, 0.0), (0.0, 5.0)],
            refs_new=[(100.0, 200.0), (120.0, 200.0), (100.0, 185.0)],
            target=(4.0, 2.0),
        )

        self.assertAlmostEqual(result[0], 108.0)
        self.assertAlmostEqual(result[1], 194.0)

    def test_coordinate_transform_dialog_returns_target_stage_from_wafer_coordinates(self):
        dialog = self.wafer_widget.CoordTransformDialog(
            [
                {"x": 0.0, "y": 10.0},
                {"x": 0.0, "y": 0.0},
                {"x": 10.0, "y": 0.0},
            ],
            [{"flake_uid": 1, "flake_id": "bf1", "coord_x": 1.0, "coord_y": 7.0}],
        )
        try:
            values = [
                ("30", "40"),
                ("40", "40"),
                ("40", "50"),
            ]
            for idx, (x_value, y_value) in enumerate(values):
                dialog._new_x_edits[idx].setText(x_value)
                dialog._new_y_edits[idx].setText(y_value)

            dialog._flake_combo.setCurrentIndex(1)

            self.assertIn("Wafer coordinate:  (1.0000,  7.0000)", dialog._flake_result_label.text())
            self.assertIn("Target Stage:  X = 33.0000,  Y = 41.0000", dialog._flake_result_label.text())
            self.assertIn("reference residual: RMS = 0.000000", dialog._params_label.text())
            self.assertEqual(dialog._diagram._new_filled, [])
        finally:
            dialog.close()

    def test_coordinate_transform_dialog_keeps_two_point_simple_transform(self):
        dialog = self.wafer_widget.CoordTransformDialog(
            [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}],
            [{"flake_uid": 1, "flake_id": "bf1", "coord_x": 0.5, "coord_y": 0.0}],
        )
        try:
            dialog._new_x_edits[0].setText("10")
            dialog._new_y_edits[0].setText("20")
            dialog._new_x_edits[1].setText("11")
            dialog._new_y_edits[1].setText("20")

            dialog._flake_combo.setCurrentIndex(1)

            self.assertIn("Target Stage:  X = 10.5000,  Y = 20.0000", dialog._flake_result_label.text())
        finally:
            dialog.close()

    def test_add_flake_dialog_has_no_material_input(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            self.assertFalse(hasattr(dialog, "material_input"))
            self.assertNotIn("material", dialog.get_data())
        finally:
            dialog.close()

    def test_add_flake_dialog_accepts_micrometer_scale_coordinates(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            dialog.coord_x.setValue(1_234_567.8912)
            dialog.coord_y.setValue(-7_654_321.1234)

            data = dialog.get_data()

            self.assertAlmostEqual(data["coord_x"], 1_234_567.8912, places=4)
            self.assertAlmostEqual(data["coord_y"], -7_654_321.1234, places=4)
        finally:
            dialog.close()

    def test_fetch_motion_xy_position_reads_localhost_position(self):
        self.config.set_motion_position_url("http://127.0.0.1:61235/position")
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"X":12.3457, "Y":98.7654}'
        response.__enter__.return_value = response

        with mock.patch.object(
            self.wafer_widget,
            "urlopen",
            return_value=response,
        ) as urlopen:
            xy = self.wafer_widget.fetch_motion_xy_position()

        urlopen.assert_called_once_with(
            "http://127.0.0.1:61235/position",
            timeout=1,
        )
        self.assertEqual(xy, (12.3457, 98.7654))

    def test_fetch_motion_xy_position_treats_unavailable_as_none(self):
        http_error = self.wafer_widget.HTTPError(
            "http://127.0.0.1:51235/position",
            503,
            "not connected",
            {},
            None,
        )

        with mock.patch.object(
            self.wafer_widget,
            "urlopen",
            side_effect=http_error,
        ):
            self.assertIsNone(self.wafer_widget.fetch_motion_xy_position())

    def test_add_flake_dialog_auto_coordinate_button_fills_xy(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            with mock.patch.object(
                self.wafer_widget,
                "fetch_motion_xy_position",
                return_value=(12.3457, 98.7654),
            ):
                dialog.fetch_current_coordinates()

            self.assertAlmostEqual(dialog.coord_x.value(), 12.3457, places=4)
            self.assertAlmostEqual(dialog.coord_y.value(), 98.7654, places=4)
        finally:
            dialog.close()

    def test_add_flake_dialog_auto_coordinate_failure_keeps_existing_values(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            dialog.coord_x.setValue(11.0)
            dialog.coord_y.setValue(22.0)

            with mock.patch.object(
                self.wafer_widget,
                "fetch_motion_xy_position",
                return_value=None,
            ), mock.patch.object(
                self.wafer_widget.QMessageBox,
                "warning",
            ) as warning:
                dialog.fetch_current_coordinates()

            self.assertEqual(dialog.coord_x.value(), 11.0)
            self.assertEqual(dialog.coord_y.value(), 22.0)
            warning.assert_called_once()
            self.assertIn("manually", warning.call_args.args[2])
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

    def test_add_flake_dialog_screenshot_photo_replaces_existing_photo(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            with mock.patch.object(
                self.wafer_widget,
                "capture_screen_region",
                side_effect=["first-shot.png", "second-shot.png"],
            ):
                dialog.capture_photo()
                dialog.capture_photo()

            self.assertEqual(dialog.get_data()["photo_path"], "second-shot.png")
            self.assertEqual(dialog.photo_label.text(), "second-shot.png")
        finally:
            dialog.close()

    def test_add_flake_dialog_extra_photos_accumulate_from_screenshot_and_files(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            with mock.patch.object(
                self.wafer_widget,
                "capture_screen_region",
                side_effect=["shot-1.png", "shot-2.png"],
            ):
                dialog.capture_extra_photo()

                with mock.patch.object(
                    self.wafer_widget.QFileDialog,
                    "getOpenFileNames",
                    return_value=(["extra-1.png", "extra-2.png"], ""),
                ):
                    dialog.select_extra_photos()

                dialog.capture_extra_photo()

            self.assertEqual(
                dialog.get_data()["extra_photo_paths"],
                ["shot-1.png", "extra-1.png", "extra-2.png", "shot-2.png"],
            )
            self.assertEqual(dialog.extra_photo_label.text(), "4 selected")
        finally:
            dialog.close()

    def test_add_flake_dialog_allows_removing_selected_photos(self):
        dialog = self.wafer_widget.AddFlakeDialog(1)
        try:
            dialog._set_photo_path("wrong-main.png")
            dialog._append_extra_photo_paths(["wrong-extra.png", "keep-extra.png"])

            dialog.remove_photo()
            dialog._remove_extra_photo_path(0)

            data = dialog.get_data()
            self.assertIsNone(data["photo_path"])
            self.assertEqual(data["extra_photo_paths"], ["keep-extra.png"])
            self.assertEqual(dialog.photo_label.text(), "No photo selected")
            self.assertEqual(dialog.extra_photo_label.text(), "1 selected")
        finally:
            dialog.close()

    def test_screen_capture_restores_modal_dialog_before_region_selection(self):
        wafer_widget = self.wafer_widget
        test_case = self

        class FakeWidget:
            def __init__(self):
                self.visible = True
                self.hide_called = False
                self.opacity = 1.0

            def isVisible(self):
                return self.visible

            def hide(self):
                self.hide_called = True
                self.visible = False

            def show(self):
                self.visible = True

            def windowOpacity(self):
                return self.opacity

            def setWindowOpacity(self, value):
                self.opacity = value

        class FakeApp:
            def __init__(self, widget):
                self.widget = widget

            def topLevelWidgets(self):
                return [self.widget]

            def processEvents(self):
                pass

            def primaryScreen(self):
                return FakeScreen()

        class FakeScreen:
            def geometry(self):
                return wafer_widget.QRect(0, 0, 10, 10)

            def grabWindow(self, window_id):
                pixmap = wafer_widget.QPixmap(10, 10)
                pixmap.fill(wafer_widget.QColor("white"))
                return pixmap

        visible_widget = FakeWidget()

        class FakeCaptureDialog:
            def __init__(self, screenshot, screen_rect):
                pass

            def exec(self):
                test_case.assertTrue(visible_widget.isVisible())
                return wafer_widget.QDialog.Accepted

            def selected_pixmap(self):
                pixmap = wafer_widget.QPixmap(2, 2)
                pixmap.fill(wafer_widget.QColor("white"))
                return pixmap

        fake_app = FakeApp(visible_widget)
        with mock.patch.object(
            self.wafer_widget.QApplication,
            "instance",
            return_value=fake_app,
        ), mock.patch.object(
            self.wafer_widget,
            "ScreenRegionCaptureDialog",
            FakeCaptureDialog,
        ):
            path = self.wafer_widget.capture_screen_region()

        self.assertIsNotNone(path)
        self.assertTrue(visible_widget.isVisible())
        self.assertFalse(visible_widget.hide_called)
        self.assertEqual(visible_widget.opacity, 1.0)

    def test_ref_point_slot_screenshot_replaces_photo(self):
        slot = self.wafer_widget.RefPointSlot(0, {"photo_path": "old.png", "x": 1, "y": 2})
        try:
            with mock.patch.object(
                self.wafer_widget,
                "capture_screen_region",
                side_effect=["first-ref.png", "second-ref.png"],
            ):
                slot._capture_photo()
                slot._capture_photo()

            self.assertEqual(slot._photo_path, "second-ref.png")
        finally:
            slot.close()

    def test_ref_point_slot_auto_coordinate_button_fills_only_that_slot(self):
        slot_a = self.wafer_widget.RefPointSlot(0, {"photo_path": "a.png", "x": 1, "y": 2})
        slot_b = self.wafer_widget.RefPointSlot(1, {"photo_path": "b.png", "x": 3, "y": 4})
        try:
            with mock.patch.object(
                self.wafer_widget,
                "fetch_motion_xy_position",
                return_value=(7.1234, 8.5678),
            ):
                slot_a.fetch_current_coordinates()

            self.assertAlmostEqual(slot_a.x_spin.value(), 7.1234, places=4)
            self.assertAlmostEqual(slot_a.y_spin.value(), 8.5678, places=4)
            self.assertEqual(slot_b.x_spin.value(), 3.0)
            self.assertEqual(slot_b.y_spin.value(), 4.0)
        finally:
            slot_a.close()
            slot_b.close()

    def test_ref_point_slot_auto_coordinate_failure_keeps_existing_values(self):
        slot = self.wafer_widget.RefPointSlot(0, {"photo_path": "old.png", "x": 1, "y": 2})
        try:
            with mock.patch.object(
                self.wafer_widget,
                "fetch_motion_xy_position",
                return_value=None,
            ), mock.patch.object(
                self.wafer_widget.QMessageBox,
                "warning",
            ) as warning:
                slot.fetch_current_coordinates()

            self.assertEqual(slot.x_spin.value(), 1.0)
            self.assertEqual(slot.y_spin.value(), 2.0)
            warning.assert_called_once()
            self.assertIn("manually", warning.call_args.args[2])
        finally:
            slot.close()

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

    def test_flake_buttons_include_edit_and_delete_actions(self):
        widget = self.wafer_widget.WaferWidget()
        try:
            button_texts = [
                button.text()
                for button in widget.findChildren(self.wafer_widget.QPushButton)
            ]

            self.assertIn("Edit Flake", button_texts)
            self.assertIn("Delete Flake", button_texts)
        finally:
            widget.close()

    def test_edit_flake_updates_existing_row_and_replaces_photos(self):
        box_id = self.db.create_box("Box Edit Flake")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake(
            "bf1",
            wafer["wafer_id"],
            material="Graphene",
            thickness="old",
            magnification="10x",
            coord_x=1.0,
            coord_y=2.0,
            photo_path="old-main.png",
            extra_photos=json.dumps(["old-extra.png"]),
            notes="old notes",
        )
        new_extra = self.root_path / "new-extra.png"
        new_extra.write_bytes(b"new extra")
        refreshed = []

        class DummyDialog:
            def __init__(self, wafer_id, parent=None, flake=None):
                self.wafer_id = wafer_id
                self.flake = flake

            def exec(self):
                return self.wafer_widget.QDialog.Accepted

            def get_data(self):
                return {
                    "flake_id": "bf1-edited",
                    "thickness": "12 nm",
                    "magnification": "50x",
                    "photo_path": None,
                    "extra_photo_paths": [str(new_extra)],
                    "coord_x": 3.5,
                    "coord_y": 4.5,
                    "notes": "edited notes",
                }

        class DummyTable:
            def currentRow(self):
                return 0

            def item(self, row, col):
                item = self.wafer_widget.QTableWidgetItem("bf1")
                item.setData(self.wafer_widget.Qt.UserRole, flake_uid)
                return item

        class DummyWidget:
            current_box_id = box_id
            current_wafer_id = wafer["wafer_id"]
            flake_table = DummyTable()

            def load_flakes_for_wafer(self, wafer_dict):
                refreshed.append(wafer_dict)

            def load_grid(self):
                refreshed.append("grid")

        DummyDialog.wafer_widget = self.wafer_widget
        DummyTable.wafer_widget = self.wafer_widget
        with mock.patch.object(self.wafer_widget, "AddFlakeDialog", DummyDialog):
            self.wafer_widget.WaferWidget.edit_flake(DummyWidget())

        flake = self.db.get_flake(flake_uid)
        self.assertEqual(flake["flake_id"], "bf1-edited")
        self.assertEqual(flake["thickness"], "12 nm")
        self.assertEqual(flake["magnification"], "50x")
        self.assertEqual(flake["coord_x"], 3.5)
        self.assertEqual(flake["coord_y"], 4.5)
        self.assertEqual(flake["notes"], "edited notes")
        self.assertEqual(flake["photo_path"], "")
        extra_photos = json.loads(flake["extra_photos"])
        self.assertEqual(len(extra_photos), 1)
        self.assertFalse(Path(extra_photos[0]).is_absolute())
        self.assertTrue(self.config.resolve_data_path(extra_photos[0]).exists())
        self.assertEqual(refreshed, [self.db.get_wafer_by_id(wafer["wafer_id"]), "grid"])

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
            ) as open_url, mock.patch.object(
                self.wafer_widget.QMessageBox,
                "warning",
            ):
                widget.view_photo()

            open_url.assert_called_once()
            self.assertEqual(
                Path(open_url.call_args.args[0].toLocalFile()),
                photo_path,
            )
        finally:
            widget.close()

    def test_view_photo_rebases_managed_onedrive_path_to_current_root(self):
        box_id = self.db.create_box("Box View Rebased Photo")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("bf-rebased", wafer["wafer_id"])
        flake_dir = self.config.FLAKES_DIR / str(flake_uid)
        flake_dir.mkdir(parents=True, exist_ok=True)
        photo_path = flake_dir / "bf.jpg"
        photo_path.write_bytes(b"fake image")
        old_machine_path = (
            Path("C:/Users/Dongkai/OneDrive - Nanyang Technological University")
            / "dongkai-db"
            / "shared"
            / "flakes"
            / str(flake_uid)
            / "bf.jpg"
        )
        self.db.update_flake(flake_uid, photo_path=str(old_machine_path))
        self.db.init_db()
        stored_path = self.db.get_flake(flake_uid)["photo_path"]
        self.assertFalse(Path(stored_path).is_absolute())
        self.assertEqual(
            self.config.resolve_data_path(stored_path),
            photo_path,
        )

        widget = self.wafer_widget.WaferWidget()
        try:
            widget.current_wafer_id = wafer["wafer_id"]
            widget.flake_table.setRowCount(1)
            item = self.wafer_widget.QTableWidgetItem("bf-rebased")
            item.setData(self.wafer_widget.Qt.UserRole, flake_uid)
            widget.flake_table.setItem(0, 0, item)
            widget.flake_table.selectRow(0)

            with mock.patch.object(
                self.wafer_widget.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as open_url, mock.patch.object(
                self.wafer_widget.QMessageBox,
                "warning",
            ):
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
        index_path = (
            self.config.PROJECTS_DIR
            / "proj"
            / "fabrication"
            / "device-edit"
            / "used_flakes.json"
        )
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["layers"][0]["flake_uid"], flake_uid)
        self.assertEqual(payload["layers"][0]["flake_id"], "flake-edit")

    def test_create_device_persists_assembly_photos_json(self):
        self.db.create_project("proj-device-photos", "Project Device Photos")
        photos = json.dumps([
            {"photo_path": "projects/proj-device-photos/fabrication/device-photo/photos/a.png",
             "note": "assembled stack"},
            {"photo_path": "projects/proj-device-photos/fabrication/device-photo/photos/b.png",
             "note": ""},
        ])

        self.db.create_device(
            "device-photo",
            "proj-device-photos",
            assembly_photos=photos,
        )

        self.assertEqual(
            self.db.get_device("device-photo")["assembly_photos"],
            photos,
        )

    def test_device_photo_column_double_click_updates_device_photos(self):
        self.db.create_project("proj-photo-edit", "Project Photo Edit")
        self.db.create_device("device-photo-edit", "proj-photo-edit")
        source_photo = self.root_path / "device-source.png"
        source_photo.write_bytes(b"device photo")

        widget = self.project_widget.ProjectWidget()

        class DummyDialog:
            def __init__(self, entries, parent=None):
                self.entries = entries

            def exec(self):
                return self.project_widget.QDialog.Accepted

            def get_photo_entries(self):
                return [{"photo_path": str(source_photo), "note": "assembly complete"}]

        DummyDialog.project_widget = self.project_widget

        try:
            widget.current_project_id = "proj-photo-edit"
            widget.load_devices("proj-photo-edit")
            headers = [
                widget.device_table.horizontalHeaderItem(i).text()
                for i in range(widget.device_table.columnCount())
            ]
            photos_col = headers.index("Photos")
            self.assertEqual(widget.device_table.item(0, photos_col).text(), "EMPTY")

            with mock.patch.object(self.project_widget, "DevicePhotosDialog", DummyDialog):
                widget.on_device_cell_double_clicked(0, photos_col)

            stored = json.loads(
                self.db.get_device("device-photo-edit")["assembly_photos"]
            )
            self.assertEqual(stored[0]["note"], "assembly complete")
            copied = self.config.resolve_data_path(stored[0]["photo_path"])
            self.assertTrue(copied.exists())
            self.assertEqual(
                copied.parent,
                self.config.PROJECTS_DIR
                / "proj-photo-edit"
                / "fabrication"
                / "device-photo-edit"
                / "photos",
            )
            self.assertEqual(widget.device_table.item(0, photos_col).text(), "1 photo")
        finally:
            widget.close()

    def test_rename_device_updates_layers_and_used_flakes(self):
        box_id = self.db.create_box("Box Rename Device")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("flake-rename", wafer["wafer_id"], material="hBN")
        self.db.create_project("proj-rename", "Project Rename")
        self.db.create_device_with_layers(
            "device-old",
            "proj-rename",
            [{"layer_name": "topbn", "flake_uid": flake_uid}],
        )

        self.db.rename_device("device-old", "device-new")

        self.assertIsNone(self.db.get_device("device-old"))
        self.assertIsNotNone(self.db.get_device("device-new"))
        self.assertEqual(self.db.get_device_layers("device-old"), [])
        layers = self.db.get_device_layers("device-new")
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["flake_uid"], flake_uid)
        self.assertEqual(self.db.get_flake(flake_uid)["used_in_device"], "device-new")

    def test_rename_device_updates_managed_assembly_photo_paths(self):
        self.db.create_project("proj-photo-rename", "Project Photo Rename")
        photo_dir = (
            self.config.PROJECTS_DIR
            / "proj-photo-rename"
            / "fabrication"
            / "device-old"
            / "photos"
        )
        photo_dir.mkdir(parents=True, exist_ok=True)
        photo_path = photo_dir / "assembly.png"
        photo_path.write_bytes(b"assembly photo")
        external_photo = self.root_path / "external-assembly.png"
        external_photo.write_bytes(b"external")
        self.db.create_device(
            "device-old",
            "proj-photo-rename",
            assembly_photos=json.dumps([
                {
                    "photo_path": self.config.to_data_path(photo_path),
                    "note": "managed",
                },
                {
                    "photo_path": str(external_photo),
                    "note": "external",
                },
            ]),
        )

        self.db.rename_device("device-old", "device-new")

        stored = json.loads(self.db.get_device("device-new")["assembly_photos"])
        self.assertEqual(
            stored[0]["photo_path"],
            self.config.to_data_path(
                self.config.PROJECTS_DIR
                / "proj-photo-rename"
                / "fabrication"
                / "device-new"
                / "photos"
                / "assembly.png"
            ),
        )
        self.assertEqual(stored[0]["note"], "managed")
        self.assertEqual(stored[1]["photo_path"], str(external_photo))

    def test_device_id_edit_renames_device_and_photos_use_new_id(self):
        self.db.create_project("proj-id-edit", "Project ID Edit")
        self.db.create_device("device-original", "proj-id-edit")
        source_photo = self.root_path / "device-id-photo.png"
        source_photo.write_bytes(b"device photo")

        widget = self.project_widget.ProjectWidget()

        class DummyDialog:
            def __init__(self, entries, parent=None):
                self.entries = entries

            def exec(self):
                return self.project_widget.QDialog.Accepted

            def get_photo_entries(self):
                return [{"photo_path": str(source_photo), "note": "uses stored id"}]

        DummyDialog.project_widget = self.project_widget

        try:
            widget.current_project_id = "proj-id-edit"
            widget.load_devices("proj-id-edit")
            id_item = widget.device_table.item(0, 0)
            self.assertTrue(id_item.flags() & self.project_widget.Qt.ItemIsEditable)

            id_item.setText("device-renamed")
            headers = [
                widget.device_table.horizontalHeaderItem(i).text()
                for i in range(widget.device_table.columnCount())
            ]
            photos_col = headers.index("Photos")

            with mock.patch.object(self.project_widget, "DevicePhotosDialog", DummyDialog):
                widget.on_device_cell_double_clicked(0, photos_col)

            self.assertIsNone(self.db.get_device("device-original"))
            renamed = self.db.get_device("device-renamed")
            self.assertIsNotNone(renamed)
            stored = json.loads(renamed["assembly_photos"])
            self.assertEqual(stored[0]["note"], "uses stored id")
            copied = self.config.resolve_data_path(stored[0]["photo_path"])
            self.assertEqual(
                copied.parent,
                self.config.PROJECTS_DIR
                / "proj-id-edit"
                / "fabrication"
                / "device-renamed"
                / "photos",
            )
        finally:
            widget.close()

    def test_device_id_edit_keeps_old_id_when_target_folder_blocks_rename(self):
        self.db.create_project("proj-id-blocked", "Project ID Blocked")
        old_fab = (
            self.config.PROJECTS_DIR
            / "proj-id-blocked"
            / "fabrication"
            / "device-old"
        )
        new_fab = (
            self.config.PROJECTS_DIR
            / "proj-id-blocked"
            / "fabrication"
            / "device-new"
        )
        old_fab.mkdir(parents=True, exist_ok=True)
        new_fab.mkdir(parents=True, exist_ok=True)
        self.db.create_device(
            "device-old",
            "proj-id-blocked",
            fab_path=self.config.to_data_path(old_fab),
        )
        widget = self.project_widget.ProjectWidget()

        try:
            widget.current_project_id = "proj-id-blocked"
            widget.load_devices("proj-id-blocked")
            id_item = widget.device_table.item(0, 0)

            with mock.patch.object(self.project_widget.QMessageBox, "warning") as warning:
                id_item.setText("device-new")

            self.assertIsNotNone(self.db.get_device("device-old"))
            self.assertIsNone(self.db.get_device("device-new"))
            self.assertTrue(old_fab.exists())
            self.assertTrue(new_fab.exists())
            warning.assert_called()
        finally:
            widget.close()

    def test_corrupt_device_photos_json_warns_in_gui(self):
        widget = self.project_widget.ProjectWidget()
        try:
            with mock.patch.object(self.project_widget.QMessageBox, "warning") as warning:
                entries = widget._device_photo_entries({"assembly_photos": "not json"})

            self.assertEqual(entries, [])
            warning.assert_called_once()
        finally:
            widget.close()

    def test_new_device_dialog_rolls_back_database_when_measurement_setup_fails(self):
        self.db.create_project("proj", "Project")
        dialog = self.project_widget.NewDeviceDialog("proj")
        dialog.device_id_edit.setText("device-fail")

        with mock.patch("pyflexlab.file_organizer.FileOrganizer",
                        side_effect=RuntimeError("boom")), \
             mock.patch.object(self.project_widget.QMessageBox, "critical"):
            dialog.accept()

        self.assertIsNone(self.db.get_device("device-fail"))

    def test_new_device_dialog_writes_used_flake_index(self):
        box_id = self.db.create_box("Box Device Index")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("flake-index", wafer["wafer_id"], material="hBN")
        self.db.create_project("proj-index", "Project Index")

        dialog = self.project_widget.NewDeviceDialog("proj-index")
        dialog.device_id_edit.setText("device-index")
        dialog.layers = [{
            "layer_name": "topbn",
            "flake_uid": flake_uid,
            "flake_id": "flake-index",
            "material": "hBN",
        }]

        with mock.patch("pyflexlab.file_organizer.FileOrganizer"), \
             mock.patch.object(self.project_widget.os, "symlink"):
            dialog.accept()

        index_path = (
            self.config.PROJECTS_DIR
            / "proj-index"
            / "fabrication"
            / "device-index"
            / "used_flakes.json"
        )
        self.assertTrue(index_path.exists())
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["project_id"], "proj-index")
        self.assertEqual(payload["device_id"], "device-index")
        self.assertEqual(
            payload["layers"],
            [{
                "order_index": 0,
                "layer_name": "topbn",
                "flake_uid": flake_uid,
                "flake_id": "flake-index",
                "material": "hBN",
            }],
        )

    def test_new_device_dialog_collects_and_saves_device_photos(self):
        self.db.create_project("proj-new-photo", "Project New Photo")
        source_photo = self.root_path / "new-device-source.png"
        source_photo.write_bytes(b"new device photo")
        dialog = self.project_widget.NewDeviceDialog("proj-new-photo")
        dialog.device_id_edit.setText("device-new-photo")

        class DummyDialog:
            def __init__(self, entries, parent=None):
                self.entries = entries

            def exec(self):
                return self.project_widget.QDialog.Accepted

            def get_photo_entries(self):
                return [{"photo_path": str(source_photo), "note": "new assembly"}]

        DummyDialog.project_widget = self.project_widget

        try:
            with mock.patch.object(self.project_widget, "DevicePhotosDialog", DummyDialog):
                dialog.edit_device_photos()

            self.assertEqual(dialog.device_photo_label.text(), "1 selected")

            with mock.patch("pyflexlab.file_organizer.FileOrganizer"), \
                 mock.patch.object(self.project_widget.os, "symlink"):
                dialog.accept()

            stored = json.loads(
                self.db.get_device("device-new-photo")["assembly_photos"]
            )
            self.assertEqual(stored[0]["note"], "new assembly")
            copied = self.config.resolve_data_path(stored[0]["photo_path"])
            self.assertTrue(copied.exists())
            self.assertEqual(
                copied.parent,
                self.config.PROJECTS_DIR
                / "proj-new-photo"
                / "fabrication"
                / "device-new-photo"
                / "photos",
            )
        finally:
            dialog.close()

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

    def test_delete_device_with_consumed_flakes_retires_instead_of_removing_files(self):
        box_id = self.db.create_box("Box Retire Device")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("flake-retire", wafer["wafer_id"], material="hBN")
        self.db.create_project("proj-retire", "Project Retire")
        self.db.create_device_with_layers(
            "device-retire",
            "proj-retire",
            [{
                "layer_name": "topbn",
                "flake_uid": flake_uid,
                "flake_id": "flake-retire",
                "material": "hBN",
            }],
        )
        self.project_widget.ProjectWidget.write_used_flakes_index(
            "proj-retire",
            "device-retire",
            [{
                "layer_name": "topbn",
                "flake_uid": flake_uid,
                "flake_id": "flake-retire",
                "material": "hBN",
            }],
        )
        fab_dir = (
            self.config.PROJECTS_DIR
            / "proj-retire"
            / "fabrication"
            / "device-retire"
        )
        index_path = fab_dir / "used_flakes.json"

        class DummyItem:
            def text(self):
                return "device-retire"

        class DummyTable:
            def currentRow(self):
                return 0

            def item(self, row, col):
                return DummyItem()

        class DummyWidget:
            current_project_id = "proj-retire"
            device_table = DummyTable()

            def load_devices(self, project_id):
                pass

        with mock.patch.object(
            self.project_widget.QMessageBox,
            "question",
            return_value=self.project_widget.QMessageBox.Yes,
        ), mock.patch.object(
            self.project_widget.QMessageBox,
            "information",
        ), mock.patch.object(
            self.project_widget.QMessageBox,
            "critical",
        ) as critical:
            self.project_widget.ProjectWidget.on_delete_device(DummyWidget())

        device = self.db.get_device("device-retire")
        self.assertIsNotNone(device)
        self.assertEqual(device["status"], "retired")
        self.assertTrue(index_path.exists())
        self.assertEqual(len(self.db.get_device_layers("device-retire")), 1)
        flake = self.db.get_flake(flake_uid)
        self.assertEqual(flake["status"], "used")
        self.assertEqual(flake["used_in_device"], "device-retire")
        critical.assert_not_called()

    def test_delete_retired_device_with_consumed_flakes_permanently_deletes_after_confirmation(self):
        box_id = self.db.create_box("Box Permanent Delete Device")
        wafer = self.db.get_or_create_wafer(box_id, 0, 0)
        flake_uid = self.db.create_flake("flake-permadelete", wafer["wafer_id"], material="hBN")
        self.db.create_project("proj-permadelete", "Project Permanent Delete")
        self.db.create_device_with_layers(
            "device-permadelete",
            "proj-permadelete",
            [{
                "layer_name": "topbn",
                "flake_uid": flake_uid,
                "flake_id": "flake-permadelete",
                "material": "hBN",
            }],
            status="retired",
        )
        fab_dir = (
            self.config.PROJECTS_DIR
            / "proj-permadelete"
            / "fabrication"
            / "device-permadelete"
        )
        fab_dir.mkdir(parents=True)
        meas_link = (
            self.config.PROJECTS_DIR
            / "proj-permadelete"
            / "measurements"
            / "device-permadelete"
        )
        meas_link.parent.mkdir(parents=True)
        meas_link.write_text("link placeholder", encoding="utf-8")

        class DummyItem:
            def text(self):
                return "device-permadelete"

        class DummyTable:
            def currentRow(self):
                return 0

            def item(self, row, col):
                return DummyItem()

        class DummyWidget:
            current_project_id = "proj-permadelete"
            device_table = DummyTable()

            def load_devices(self, project_id):
                pass

        with mock.patch.object(
            self.project_widget.QMessageBox,
            "question",
            return_value=self.project_widget.QMessageBox.Yes,
        ) as question, mock.patch.object(
            self.project_widget.QMessageBox,
            "information",
        ), mock.patch.object(
            self.project_widget.QMessageBox,
            "critical",
        ) as critical:
            self.project_widget.ProjectWidget.on_delete_device(DummyWidget())

        self.assertIsNone(self.db.get_device("device-permadelete"))
        self.assertEqual(self.db.get_device_layers("device-permadelete"), [])
        self.assertFalse(fab_dir.exists())
        self.assertFalse(meas_link.exists())
        flake = self.db.get_flake(flake_uid)
        self.assertEqual(flake["status"], "used")
        self.assertIsNone(flake["used_in_device"])
        self.assertIn("Permanently delete retired device", question.call_args.args[2])
        critical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
