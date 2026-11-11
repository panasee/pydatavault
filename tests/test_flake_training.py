"""Learned channels, one-class rejection, versioned export and data integrity."""

import base64
import http.client
import json
import sqlite3
import threading

import numpy as np
import pytest

from pydatavault.flake_analysis import FlakeAnalysisService, StaleModelError
from pydatavault.flake_api import make_server
from pydatavault.flake_imaging import region_features
from pydatavault.flake_photo_store import FlakePhotoStore
from pydatavault.flake_training import predict_with_model, train_model
from test_flake_photos import annotations, png, profile, scene, BACKGROUND_POLYGONS, FLAKE_POLYGONS


def snapshot(discriminating="G", single=False):
    references = []
    for group in range(6):
        for layer in ([2] if single else [1, 2]):
            vector = np.array([80.0 + group % 2, 80.0 + group % 2, 80.0 + group % 2])
            vector["RGB".index(discriminating)] = 65 + 15 * layer + (group % 3 - 1) * 0.1
            references.append({"layers": layer, "thickness_nm": layer * 0.7,
                               "normalized_rgb": vector.tolist(), "image_hash": f"hash-{group}",
                               "training_group": f"user:flake-{group}"})
    return {"references": references, "data_fingerprint": "test-fingerprint", "source_photos": [],
            "profile": {"profile_id": 1, "material": "test", "substrate": "285 nm", "optical_setup": "A"}}


def feature(vector):
    return {"region_id": 1, "normalized_rgb": vector, "warnings": []}


def seed_store(tmp_path, *, single=False):
    store = FlakePhotoStore(tmp_path / "models.db")
    pid = profile(store)
    rgb, labels, background = scene()
    labels = labels.copy()
    if single:
        labels[labels == 2] = 0
    truth = {1: annotations()[1]} if single else annotations()
    for i, gain in enumerate([0.5, 0.75, 1.0]):
        data, _, _ = scene(gain)
        store.save_photo(pid, png(data), f"photo-{i}.png", labels, background, truth,
                         metadata={"training_group": f"flake-{i}"})
    return store, pid, rgb, labels, background


@pytest.mark.parametrize("channel", ["R", "G", "B"])
def test_auto_channels_learn_discrimination_for_each_material(channel):
    model = train_model(snapshot(channel))
    assert model["channels"] == [channel]
    assert model["mode"] == "multiclass"
    assert len(model["training"]["validation"]["channel_candidates"]) == 7
    assert model["training"]["validation"]["balanced_accuracy"] == 1.0
    query = [80.0, 80.0, 80.0]
    query["RGB".index(channel)] = 95.0
    result = predict_with_model([feature(query)], json.loads(json.dumps(model)))[0]
    assert result["status"] == "match" and result["layers"] == 2
    query["RGB".index(channel)] = 130.0
    assert predict_with_model([feature(query)], model)[0]["status"] == "out_of_distribution"


def test_single_thickness_uses_all_channels_and_rejects_without_inventing_other_labels():
    model = train_model(snapshot(single=True))
    assert model["mode"] == "single_target" and model["channels"] == list("RGB")
    assert model["training"]["validation"]["balanced_accuracy"] is None
    assert model["training"]["validation"]["false_positive_rate"] is None
    result = predict_with_model([feature([80.5, 95, 80.5])], model)[0]
    assert result["status"] == "target_like" and result["is_target_like"] is True
    assert result["layers"] is None and result["candidate_layers"] == 2
    result = predict_with_model([feature([25, 25, 25])], model)[0]
    assert result["status"] == "out_of_distribution" and result["is_target_like"] is False
    assert result["layers"] is None
    manual = train_model(snapshot(single=True), channels="G")
    assert manual["channels"] == ["G"]


def test_repeated_photos_of_one_group_do_not_inflate_training_or_validation():
    data = snapshot()
    base = train_model(data, channels="RGB")
    copies = []
    for row in data["references"][:2]:
        for i in range(10):
            copies.append({**row, "image_hash": f"repeat-{i}"})
    data["references"] += copies
    repeated = train_model(data, channels="RGB")
    assert repeated["training"]["group_count"] == base["training"]["group_count"]
    for actual, expected in zip(repeated["classes"], base["classes"]):
        np.testing.assert_allclose(actual["mean"], expected["mean"])
        np.testing.assert_allclose(actual["covariance"], expected["covariance"], atol=1e-12)
    for name in ("balanced_accuracy", "confusion_matrix", "coverage"):
        assert repeated["training"]["validation"][name] == base["training"]["validation"][name]
    for row in data["references"]:
        row["training_group"] = "user:same-flake"
    with pytest.raises(ValueError, match="independent training groups"):
        train_model(data)


def test_quality_training_membership_and_identical_classes_never_return_confident_match():
    model = train_model(snapshot())
    query = feature([80, 80, 80])
    assert predict_with_model([query], model, image_hash="hash-0")[0]["status"] == "training_sample"
    assert predict_with_model([query], model, group="user:flake-0")[0]["status"] == "training_sample"
    assert predict_with_model([query], model, quality_warnings=["nonuniform_substrate"])[0]["status"] == "needs_review"
    data = snapshot()
    for row in data["references"]:
        row["normalized_rgb"] = [80, 80, 80]
    ambiguous = train_model(data)
    assert "weak_training_separation" in ambiguous["training"]["diagnostics"]
    result = predict_with_model([query], ambiguous)[0]
    assert result["status"] == "needs_review" and result["layers"] is None


def test_covariance_is_invertible_for_small_constant_samples_and_preserves_correlations():
    data = snapshot(single=True)
    for i, row in enumerate(data["references"]):
        row["normalized_rgb"] = [80 + i, 80 + i, 90]
    model = train_model(data)
    c = model["classes"][0]
    covariance = np.asarray(c["covariance"])
    assert covariance[0, 1] > 0
    assert np.min(np.linalg.eigvalsh(covariance)) > 0
    np.testing.assert_allclose(covariance @ c["precision"], np.eye(3), atol=1e-12)


def test_nm_only_training_does_not_assign_layers():
    data = snapshot(single=True)
    for row in data["references"]:
        row["layers"], row["thickness_nm"] = None, 8.5
    model = train_model(data)
    result = predict_with_model([feature([80.5, 95, 80.5])], model)[0]
    assert result["candidate_thickness_nm"] == 8.5 and result["candidate_layers"] is None


def test_model_roundtrip_versions_staleness_and_material_isolation(tmp_path):
    store, pid, rgb, labels, background = seed_store(tmp_path)
    service = FlakeAnalysisService(store=store)
    model = service.train(pid)
    assert model["revision"] == 1 and not model["stale"]
    assert FlakePhotoStore(store.path).get_model(pid) == model
    other = profile(store, material="WSe2")
    assert store.get_model(other) is None
    assert store.get_model(other, model_id=model["model_id"]) is None
    known = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert {r["status"] for r in known["regions"]} == {"training_sample"}
    rgb[85, 115] = [150, 170, 190]
    result = service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    assert [r["layers"] for r in result["regions"]] == [1, 2]
    assert result["model_id"] == model["model_id"] and result["inference_method"] == "trained_model"
    features, _, warnings = region_features(rgb, labels, background)
    assert result["regions"] == predict_with_model(features, json.loads(json.dumps(model)), quality_warnings=warnings)
    photo = store.load_photo(1)
    truth = photo["annotations"]
    truth[1]["provenance"] = "new measurement"
    store.save_photo(pid, photo["original"], "p.png", photo["labels"], photo["substrate_mask"], truth,
                     metadata=photo["metadata"], photo_id=1, expected_revision=1)
    assert store.get_model(pid)["stale"]
    with pytest.raises(StaleModelError):
        service.analyze(rgb, pid, labels=labels, substrate_mask=background)
    updated = service.train(pid, channels="RGB")
    assert updated["revision"] == 2 and not updated["stale"]
    assert store.get_model(model_id=model["model_id"])["stale"]


def test_training_publication_rejects_concurrent_changes_and_failed_training_keeps_previous_model(tmp_path):
    store, pid, rgb, labels, background = seed_store(tmp_path, single=True)
    service = FlakeAnalysisService(store=store)
    model = service.train(pid)
    snapshot_before = store.training_snapshot(pid)
    rgb[85, 115] = [150, 170, 190]
    store.save_photo(pid, png(rgb), "extra.png", labels, background, {1: annotations()[1]})
    with pytest.raises(ValueError, match="changed during training"):
        store.save_model(train_model(snapshot_before))
    with pytest.raises(ValueError, match="Channels"):
        service.train(pid, channels="invalid")
    assert store.get_model(pid)["model_id"] == model["model_id"]
    with pytest.raises(ValueError, match="Training group"):
        store.save_photo(pid, png(rgb), "extra.png", labels, background, {1: annotations()[1]}, metadata={"training_group": 42})


def test_v1_database_upgrade_preserves_original_samples(tmp_path):
    store, pid, _, _, _ = seed_store(tmp_path)
    before = store.load_photo(1)
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TABLE flake_models")
        conn.execute("PRAGMA user_version=1")
    upgraded = FlakePhotoStore(store.path)
    assert upgraded.load_photo(1)["original"] == before["original"]
    assert upgraded.load_photo(1)["annotations"] == before["annotations"]
    assert FlakeAnalysisService(store=upgraded).train(pid)["revision"] == 1


def test_export_api_is_sufficient_for_offline_single_target_inference_and_reports_staleness(tmp_path):
    store, pid, rgb, labels, background = seed_store(tmp_path, single=True)
    model = FlakeAnalysisService(store=store).train(pid)
    server = make_server(store.path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def request(method, path, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            conn.request(method, path, json.dumps(body) if body else None, {"Content-Type": "application/json"})
            response = conn.getresponse()
            status, data = response.status, json.loads(response.read())
            conn.close()
            return status, data
        status, exported = request("GET", f"/v1/profiles/{pid}/model")
        assert status == 200 and exported["model"] == model
        assert request("GET", f"/v1/models/{model['model_id']}")[1] == exported
        assert request("GET", "/v1/profiles/999/model")[0] == 404
        assert "trained_model_export" in request("GET", "/v1/health")[1]["capabilities"]
        rgb[85, 115] = [150, 170, 190]
        payload = {"profile_id": pid, "image_base64": base64.b64encode(png(rgb)).decode(),
                   "substrate_polygons": BACKGROUND_POLYGONS, "flake_polygons": FLAKE_POLYGONS[:1]}
        status, result = request("POST", "/v1/analyze", payload)
        assert status == 200 and result["regions"][0]["status"] == "target_like"
        features, _, warnings = region_features(rgb, labels, background)
        offline = predict_with_model(features, exported["model"], quality_warnings=warnings)
        assert result["regions"] == offline
        assert request("POST", "/v1/analyze", {**payload, "training_group": "flake-0"})[1]["regions"][0]["status"] == "training_sample"
        assert request("POST", "/v1/analyze", {**payload, "training_group": 42})[0] == 400
        store.save_photo(pid, png(rgb), "extra.png", labels, background, {1: annotations()[1]})
        assert request("GET", f"/v1/profiles/{pid}/model")[1]["model"]["stale"]
        status, error = request("POST", "/v1/analyze", payload)
        assert status == 409 and error["code"] == "model_stale"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_unknown_model_and_feature_formats_are_rejected():
    model = train_model(snapshot())
    for changed in [{"model_schema": "future.v2"}, {"processing_key": "different"}, {"stale": True}]:
        with pytest.raises(ValueError):
            predict_with_model([feature([80, 80, 80])], {**model, **changed})
    for vector in [[80, 80], [float("nan"), 80, 80], [-1, 80, 80]]:
        with pytest.raises(ValueError):
            predict_with_model([feature(vector)], model)
