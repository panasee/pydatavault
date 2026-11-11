"""Photo provenance, processing, rejection behavior, editor and public API tests."""

from io import BytesIO
import base64
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
from PIL import Image
import pytest

from pydatavault.flake_analysis import FlakeAnalysisService
from pydatavault.flake_api import make_server
from pydatavault.flake_imaging import (
    decode_image, masks_from_polygons, normalize_image, polygon_mask,
    predict_regions, region_features,
)
from pydatavault.flake_photo_store import FlakePhotoStore


def png(rgb):
    buffer = BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def scene(gain=1.0):
    rgb = np.full((90, 120, 3), (160, 180, 200), dtype=np.uint8)
    rgb[20:65, 15:45] = (128, 144, 160)
    rgb[20:65, 45:75] = (96, 126, 160)
    labels = np.zeros(rgb.shape[:2], dtype=np.int32)
    labels[20:65, 15:45] = 1
    labels[20:65, 45:75] = 2
    background = np.zeros(labels.shape, dtype=bool)
    background[5:15, 5:100] = True
    return np.rint(rgb * gain).astype(np.uint8), labels, background


def annotations():
    return {1: {"layers": 1, "thickness_nm": 0.7, "provenance": "AFM"},
            2: {"layers": 2, "thickness_nm": 1.4, "provenance": "Raman"}}


BACKGROUND_POLYGONS = [[[5, 5], [99, 5], [99, 14], [5, 14]]]
FLAKE_POLYGONS = [[[15, 20], [44, 20], [44, 64], [15, 64]],
                  [[45, 20], [74, 20], [74, 64], [45, 64]]]


@pytest.fixture
def store(tmp_path):
    return FlakePhotoStore(tmp_path / "photos.db")


def profile(store, material="MoS2", substrate="285 nm SiO2/Si", optics="scope A / 50x / white"):
    return store.ensure_profile(material, substrate, optics)


def test_channel_gain_normalization_preserves_float_features():
    rgb, labels, background = scene()
    adjusted = (rgb.astype(float) * [0.5, 1.0, 0.8]).astype(np.uint8)
    first, _, _ = region_features(rgb, labels, background)
    second, _, _ = region_features(adjusted, labels, background)
    np.testing.assert_allclose([r["normalized_rgb"] for r in first], [r["normalized_rgb"] for r in second], atol=1e-5)
    brighter = rgb.copy()
    brighter[25:55, 20:40] = (240, 240, 240)
    normalized, _, _ = normalize_image(brighter, background)
    assert normalized[30, 30, 0] == 150  # un-clipped ratio, not an 8-bit preview


def test_manual_polygons_reproduce_full_resolution_masks():
    _, expected_labels, expected_background = scene()
    labels, background = masks_from_polygons((90, 120), BACKGROUND_POLYGONS, FLAKE_POLYGONS)
    np.testing.assert_array_equal(labels, expected_labels)
    np.testing.assert_array_equal(background, expected_background)
    # Arbitrary polygon shape is retained, rather than its bounding rectangle.
    triangle = polygon_mask((90, 120), [[80, 30], [110, 30], [80, 60]])
    assert triangle[35, 85] and not triangle[55, 105]
    multiple = BACKGROUND_POLYGONS + [[[5, 70], [10, 70], [10, 80], [5, 80]]]
    _, background = masks_from_polygons((90, 120), multiple, FLAKE_POLYGONS)
    assert background[75, 7] and background[10, 7] and not background[50, 7]


@pytest.mark.parametrize("points", [
    [[1, 1], [15, 15], [1, 15], [15, 1]],  # crossing edges
    [[-1, 1], [10, 1], [10, 10]],
    [[1, 1], [120, 1], [10, 10]],
    [[1, 1], [2, 2], [3, 3]],
    [[1, 1], [10, 1]],
    [[1, 1], [float("nan"), 1], [10, 10]],
])
def test_invalid_polygons_are_rejected(points):
    with pytest.raises(ValueError):
        polygon_mask((90, 120), points)


def test_missing_or_overlapping_manual_selections_are_rejected(store):
    pid = profile(store)
    rgb, labels, background = scene()
    service = FlakeAnalysisService(store=store)
    for supplied in [{}, {"labels": labels}, {"substrate_mask": background}]:
        with pytest.raises(ValueError, match="Draw substrate and flake"):
            service.analyze(rgb, pid, **supplied)
    with pytest.raises(ValueError, match="at least one flake"):
        service.analyze(rgb, pid, labels=np.zeros_like(labels), substrate_mask=background)
    for substrate, flakes in [(None, FLAKE_POLYGONS), (BACKGROUND_POLYGONS, []),
                              (BACKGROUND_POLYGONS, BACKGROUND_POLYGONS),
                              (BACKGROUND_POLYGONS, [FLAKE_POLYGONS[0]] * 2)]:
        with pytest.raises(ValueError):
            masks_from_polygons(rgb.shape[:2], substrate, flakes)
    assert store.list_photos(pid) == []


def test_bad_background_and_overlapping_masks_are_rejected():
    rgb, labels, background = scene()
    rgb[background] = 255
    with pytest.raises(ValueError, match="saturation"):
        normalize_image(rgb, background)
    rgb, labels, background = scene()
    background[30, 30] = True
    with pytest.raises(ValueError, match="overlap"):
        region_features(rgb, labels, background)
    with pytest.raises(ValueError, match="boolean"):
        normalize_image(rgb, background.astype(int))


def test_decode_preserves_orientation_and_rejects_transparency():
    rgb, _, _ = scene()
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG", exif=exif)
    decoded = decode_image(buffer.getvalue())
    np.testing.assert_array_equal(decoded, np.rot90(rgb, -1))
    buffer = BytesIO()
    Image.new("RGBA", (10, 10), (100, 100, 100, 0)).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="Transparent"):
        decode_image(buffer.getvalue())


def test_photo_round_trip_revision_conflict_and_original_provenance(store):
    pid = profile(store)
    rgb, labels, background = scene()
    original = png(rgb)
    photo_id = store.save_photo(pid, original, "original.png", labels, background, annotations(), metadata={"objective": "50x"})
    saved = store.load_photo(photo_id)
    assert saved["original"] == original
    assert saved["annotations"] == annotations()
    np.testing.assert_array_equal(saved["labels"], labels)
    np.testing.assert_array_equal(saved["substrate_mask"], background)
    assert saved["metadata"]["objective"] == "50x"
    assert saved["normalized"].dtype == np.float32
    labels[65:70, 15:45] = 1
    store.save_photo(pid, original, "original.png", labels, background, annotations(), photo_id=photo_id, expected_revision=1)
    assert store.load_photo(photo_id)["revision"] == 2
    with pytest.raises(ValueError, match="reload"):
        store.save_photo(pid, original, "original.png", labels, background, annotations(), photo_id=photo_id, expected_revision=1)
    assert store.load_photo(photo_id)["revision"] == 2
    with pytest.raises(ValueError, match="already exists"):
        store.save_photo(pid, original, "renamed.png", labels, background, annotations())
    assert len(store.list_photos(pid)) == 1


@pytest.mark.parametrize("invalid", [
    {1: {"layers": 1, "provenance": "AFM"}},
    {1: {"layers": 1.5, "provenance": "AFM"}, 2: {"layers": 2, "provenance": "AFM"}},
    {1: {"layers": True, "provenance": "AFM"}, 2: {"layers": 2, "provenance": "AFM"}},
    {1: {"thickness_nm": float("nan"), "provenance": "AFM"}, 2: {"layers": 2, "provenance": "AFM"}},
    {1: {"layers": 1}, 2: {"layers": 2, "provenance": "AFM"}},
])
def test_invalid_annotation_does_not_partially_update_database(store, invalid):
    pid = profile(store)
    rgb, labels, background = scene()
    photo_id = store.save_photo(pid, png(rgb), "valid.png", labels, background, annotations())
    with pytest.raises(ValueError):
        store.save_photo(pid, png(rgb), "valid.png", labels, background, invalid, photo_id=photo_id, expected_revision=1)
    assert store.load_photo(photo_id)["annotations"] == annotations()
    assert store.load_photo(photo_id)["revision"] == 1


def test_profiles_keep_material_substrate_and_optics_separate(store):
    ids = [profile(store), profile(store, material="WSe2"), profile(store, substrate="90 nm SiO2/Si"),
           profile(store, optics="scope B / 100x")]
    assert len(set(ids)) == 4
    assert profile(store, material="mos2") == ids[0]
    rgb, labels, background = scene()
    store.save_photo(ids[0], png(rgb), "calibration.png", labels, background, annotations())
    assert store.reference_features(ids[1]) == []


def test_prediction_rejects_sparse_unknown_ambiguous_and_self_matches(store):
    pid = profile(store)
    rgb, labels, background = scene()
    service = FlakeAnalysisService(store.path)
    empty = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert {r["status"] for r in empty["regions"]} == {"no_reference"}
    for gain in [0.5, 0.75, 1.0]:
        data, _, _ = scene(gain)
        store.save_photo(pid, png(data), f"gain-{gain}.png", labels, background, annotations())
    self_result = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert {r["status"] for r in self_result["regions"]} == {"insufficient_reference"}
    # A different image with unchanged feature colors is a held-out query.
    rgb[85, 115] = (155, 175, 195)
    result = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert [r["layers"] for r in result["regions"]] == [1, 2]
    assert all(r["reference_photos"] == 3 for r in result["regions"])
    # A new acquisition with brightness and unequal RGB gains remains comparable.
    shifted = np.rint(rgb.astype(float) * [0.5, 1.0, 0.8]).astype(np.uint8)
    shifted_result = service.analyze(shifted, pid, labels=labels, substrate_mask=background)
    assert [r["layers"] for r in shifted_result["regions"]] == [1, 2]
    np.testing.assert_allclose([r["normalized_rgb"] for r in result["regions"]],
                               [r["normalized_rgb"] for r in shifted_result["regions"]], atol=1e-5)
    rgb[labels == 1] = (25, 50, 200)
    result = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert result["regions"][0]["status"] == "out_of_distribution"
    assert result["regions"][0]["layers"] is None
    references = store.reference_features(pid)
    for ref in references:
        ref["normalized_rgb"] = [80, 80, 80]
    features, _, _ = region_features(scene()[0], labels, background)
    result = predict_regions(features[:1], references)
    assert result[0]["status"] == "ambiguous"
    assert result[0]["layers"] is None


def test_nm_labels_do_not_invent_layers_or_use_low_quality_training(store):
    pid = profile(store)
    rgb, labels, background = scene()
    nm = {1: {"thickness_nm": 8.5, "provenance": "AFM"}, 2: {"thickness_nm": 21.0, "provenance": "AFM"}}
    store.save_photo(pid, png(rgb), "nm.png", labels, background, nm)
    features, _, _ = region_features(rgb, labels, background)
    results = predict_regions(features, store.reference_features(pid))
    assert results[0]["candidate_layers"] is None
    assert results[0]["candidate_thickness_nm"] == 8.5
    rgb[labels == 1] = (255, 120, 150)
    store.save_photo(pid, png(rgb), "clipped.png", labels, background, annotations())
    assert len(store.reference_features(pid)) == 3  # clipped region is excluded


def test_headless_analysis_needs_no_lab_qt_or_http_environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("VAULT_DB_PATH", "PYLAB_DB_OUT", "PYLAB_DB_LOCAL")}
    script = "from pydatavault.flake_analysis import FlakeAnalysisService; import sys; FlakeAnalysisService(sys.argv[1]); assert not {'PySide6', 'pydatavault.config', 'http.server', 'scipy'} & sys.modules.keys()"
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "headless.db")], env=env, check=True, capture_output=True)


def test_http_api_end_to_end_and_bad_requests(store):
    pid = profile(store)
    rgb, _, _ = scene()
    server = make_server(store.path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def request(method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            connection.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        status, body = request("GET", "/v1/profiles")
        assert status == 200 and body["profiles"][0]["profile_id"] == pid
        payload = {"profile_id": pid, "image_base64": base64.b64encode(png(rgb)).decode(),
                   "substrate_polygons": BACKGROUND_POLYGONS, "flake_polygons": FLAKE_POLYGONS}
        status, body = request("POST", "/v1/analyze", payload)
        assert status == 200 and len(body["regions"]) == 2
        assert body["regions"][0]["status"] == "no_reference"
        assert store.list_photos(pid) == []  # inference is read only
        for updates in [{"image_base64": "invalid!"}, {"profile_id": 999}, {"substrate_rect": [-1, 0, 5, 5]},
                        {"options": {"color_regions": 3.5}}, {"profile_id": True},
                        {"substrate_polygons": None}, {"flake_polygons": []},
                        {"flake_polygons": BACKGROUND_POLYGONS}]:
            assert request("POST", "/v1/analyze", {**payload, **updates})[0] == 400
        assert request("GET", "/missing")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def qt_app(request, tmp_path):
    # PySide6 installs a process-wide import hook that conflicts with this
    # environment's six/dateutil stack when pyflexlab is imported afterwards.
    # Keep GUI tests isolated, and also verify the editor has no lab-env coupling.
    if os.environ.get("PYDATAVAULT_GUI_TEST_CHILD") != "1":
        env = dict(os.environ, PYDATAVAULT_GUI_TEST_CHILD="1", QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", f"{Path(__file__).resolve()}::{request.node.name}",
             "-q", "--tb=short", f"--basetemp={tmp_path / 'gui-subprocess'}"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield None
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen backend has no system font discovery.
    if not QFontDatabase.families() and sys.platform == "win32":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    from pydatavault import style
    style.apply_app_style(app)
    yield app


def test_editor_edit_undo_save_reload_and_conflict_handling(store, tmp_path, qt_app, monkeypatch):
    if qt_app is None:
        return
    from pydatavault.flake_photo_widget import FlakePhotoLibraryDialog
    pid = profile(store)
    rgb, labels, background = scene()
    path = tmp_path / "photo.png"
    path.write_bytes(png(rgb))
    dialog = FlakePhotoLibraryDialog(store=store)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda exc: errors.append(str(exc)))
    try:
        dialog.load_image(path)
        assert not dialog.labels.any() and not dialog.substrate_mask.any()
        assert not dialog.predict_button.isEnabled()
        dialog.add_polygon(BACKGROUND_POLYGONS[0])
        for points in FLAKE_POLYGONS:
            dialog.mode.setCurrentText("Draw flake polygon")
            dialog.add_polygon(points)
        np.testing.assert_array_equal(dialog.labels, labels)
        np.testing.assert_array_equal(dialog.substrate_mask, background)
        assert dialog.predict_button.isEnabled()
        # Unknown photos can be queried without supplying a label or saving.
        dialog.predict()
        wait_for_job(dialog, qt_app)
        assert {r["status"] for r in dialog.predictions.values()} == {"no_reference"}
        assert dialog.table.item(0, 5).text() == "No reference data"
        assert dialog.table.item(0, 2).text() == ""
        assert not dialog.annotations and store.list_photos(pid) == []
        dialog.annotations = annotations()
        dialog.refresh_table()
        dialog.pick_region(1)
        dialog.mode.setCurrentText("Redraw selected flake")
        dialog.add_polygon([[20, 25], [40, 25], [40, 55], [20, 55]])
        assert dialog.labels[30, 30] == 1 and dialog.labels[20, 15] == 0
        assert 1 not in dialog.annotations and not dialog.predictions
        dialog.undo()
        np.testing.assert_array_equal(dialog.labels, labels)
        assert dialog.annotations == annotations()
        dialog.save()
        assert not errors
        photo_id = dialog.photo_id
        assert store.load_photo(photo_id)["annotations"] == annotations()
        dialog.open_saved(dialog.photos.item(0))
        assert dialog.revision == 1 and not dialog.dirty
        assert dialog.selected_region is None and dialog.selected_ids() == []
        assert store.load_photo(photo_id)["metadata"]["selection_method"] == "manual_polygons"
        dialog.pick_region(2)
        dialog.remove_regions()
        assert not np.any(dialog.labels == 2) and 2 not in dialog.annotations
        dialog.undo()
        assert dialog.annotations == annotations()
        dialog.table.item(0, 4).setText("AFM verified")
        dialog.save()
        assert dialog.revision == 2 and not errors
        assert store.load_photo(photo_id)["annotations"][1]["provenance"] == "AFM verified"
        # Another editor wins first; a stale save must retain local edits.
        stored = store.load_photo(photo_id)
        store.save_photo(pid, stored["original"], "photo.png", labels, background,
                         annotations(), photo_id=photo_id, expected_revision=2)
        dialog.table.item(0, 4).setText("local unsaved source")
        dialog.save()
        assert dialog.revision == 2 and dialog.dirty
        assert "reload" in errors.pop()
        assert store.load_photo(photo_id)["revision"] == 3
        dialog.show()
        qt_app.processEvents()
        dialog.grab().save(str(tmp_path / "editor.png"))
    finally:
        dialog.dirty = False
        dialog.close()


def wait_for_job(dialog, app):
    deadline = time.monotonic() + 10
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not dialog.busy and dialog.isEnabled()


def test_editor_worker_keeps_ui_responsive_and_restores_state_on_failure(store, tmp_path, qt_app, monkeypatch):
    if qt_app is None:
        return
    from pydatavault.flake_photo_widget import FlakePhotoLibraryDialog
    profile(store)
    rgb, _, _ = scene()
    path = tmp_path / "photo.png"
    path.write_bytes(png(rgb))
    dialog = FlakePhotoLibraryDialog(store=store)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda exc: errors.append(str(exc)))
    try:
        dialog.load_image(path)
        dialog.add_polygon(BACKGROUND_POLYGONS[0])
        dialog.mode.setCurrentText("Draw flake polygon")
        dialog.add_polygon(FLAKE_POLYGONS[0])
        previous = dialog.labels.copy()
        dialog.predict()
        assert dialog.busy
        wait_for_job(dialog, qt_app)
        assert not errors and dialog.predict_button.isEnabled()
        np.testing.assert_array_equal(previous, dialog.labels)
        def fail():
            raise ValueError("deliberate input failure")
        dialog.run_task("test failure", fail, lambda _: None)
        wait_for_job(dialog, qt_app)
        assert errors == ["deliberate input failure"]
        np.testing.assert_array_equal(previous, dialog.labels)
    finally:
        dialog.dirty = False
        dialog.close()


def test_editor_polygon_mouse_keyboard_and_invalid_edits(store, tmp_path, qt_app, monkeypatch):
    if qt_app is None:
        return
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from pydatavault.flake_photo_widget import FlakePhotoLibraryDialog
    profile(store)
    rgb, _, _ = scene()
    path = tmp_path / "photo.png"
    path.write_bytes(png(rgb))
    dialog = FlakePhotoLibraryDialog(store=store)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda exc: errors.append(str(exc)))
    try:
        dialog.show()
        dialog.load_image(path)
        qt_app.processEvents()
        canvas = dialog.canvas
        for point in BACKGROUND_POLYGONS[0]:
            QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(*point)))
        QTest.keyClick(canvas, Qt.Key_Return)
        assert dialog.substrate_mask[10, 20] and not canvas.points
        assert not dialog.predict_button.isEnabled()  # no flake yet
        dialog.mode.setCurrentText("Draw flake polygon")
        for point in FLAKE_POLYGONS[0]:
            QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(*point)))
        QTest.keyClick(canvas, Qt.Key_Backspace)
        assert len(canvas.points) == 3
        QTest.keyClick(canvas, Qt.Key_Escape)
        assert not canvas.points and not dialog.labels.any() and dialog.isVisible()
        for point in FLAKE_POLYGONS[0]:
            QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(*point)))
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(*FLAKE_POLYGONS[0][-1])))
        assert dialog.labels[30, 30] == 1 and dialog.predict_button.isEnabled()
        previous = dialog.labels.copy()
        history_size = len(dialog.history)
        dialog.mode.setCurrentText("Draw flake polygon")
        dialog.add_polygon(FLAKE_POLYGONS[0])  # overlap, no mutation
        assert "overlaps another" in errors.pop()
        np.testing.assert_array_equal(dialog.labels, previous)
        assert len(dialog.history) == history_size
        dialog.mode.setCurrentText("Draw substrate polygon")
        dialog.add_polygon(FLAKE_POLYGONS[0])
        assert "overlaps a flake" in errors.pop()
        dialog.clear_background()
        assert not dialog.predict_button.isEnabled()
        dialog.undo()
        assert dialog.predict_button.isEnabled()
        # Import does not infer masks, even for an unusable all-white photo.
        path.write_bytes(png(np.full_like(rgb, 255)))
        dialog.load_image(path)
        assert not dialog.substrate_mask.any() and not dialog.labels.any()
        dialog.add_polygon(BACKGROUND_POLYGONS[0])
        assert "saturation" in errors.pop()
        assert not dialog.substrate_mask.any() and not dialog.history
    finally:
        dialog.dirty = False
        dialog.close()


def test_editor_trains_single_target_exports_and_marks_model_stale(store, tmp_path, qt_app, monkeypatch):
    if qt_app is None:
        return
    from PySide6.QtWidgets import QFileDialog
    from pydatavault.flake_photo_widget import FlakePhotoLibraryDialog
    pid = profile(store)
    rgb, labels, background = scene()
    labels[labels == 2] = 0
    for i, gain in enumerate([0.5, 0.75, 1.0]):
        data, _, _ = scene(gain)
        store.save_photo(pid, png(data), f"reference-{i}.png", labels, background, {1: annotations()[1]},
                         metadata={"training_group": f"flake-{i}", "objective": "50x"})
    dialog = FlakePhotoLibraryDialog(store=store)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda exc: errors.append(str(exc)))
    try:
        dialog.train_profile()
        wait_for_job(dialog, qt_app)
        assert not errors and "Target similarity only" in dialog.model_info.text()
        assert store.get_model(pid)["mode"] == "single_target"
        rgb[85, 115] = [150, 170, 190]
        path = tmp_path / "query.png"
        path.write_bytes(png(rgb))
        dialog.load_image(path)
        dialog.add_polygon(BACKGROUND_POLYGONS[0])
        dialog.mode.setCurrentText("Draw flake polygon")
        dialog.add_polygon(FLAKE_POLYGONS[0])
        dialog.predict()
        wait_for_job(dialog, qt_app)
        assert dialog.predictions[1]["status"] == "target_like"
        assert "Target-like" in dialog.table.item(0, 5).text()
        assert dialog.table.item(0, 2).text() == ""  # Never populate ground truth from prediction.
        export = tmp_path / "model.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(export), "JSON"))
        dialog.export_model()
        exported = json.loads(export.read_text(encoding="utf-8"))
        assert exported == store.get_model(pid)
        dialog.show()
        qt_app.processEvents()
        dialog.grab().save(str(tmp_path / "trained-editor.png"))
        dialog.dirty = False
        dialog.open_saved(dialog.photos.item(0))
        assert dialog.training_group.text() == "flake-2"
        dialog.table.item(0, 4).setText("AFM rechecked")
        dialog.save()
        assert store.load_photo(dialog.photo_id)["metadata"]["objective"] == "50x"
        assert "STALE" in dialog.model_info.text()
        dialog.predict()
        wait_for_job(dialog, qt_app)
        assert "train this profile again" in errors.pop()
        dialog.train_profile()
        wait_for_job(dialog, qt_app)
        assert store.get_model(pid)["revision"] == 2 and not errors
    finally:
        dialog.dirty = False
        dialog.close()
