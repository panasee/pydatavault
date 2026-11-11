"""Small, portable Gaussian calibration models; no GUI, HTTP, or database imports.

RGB distributions follow the approach of Sterbentz et al. (2021), adapted here
to supervised region summaries and independent acquisition groups. Shrinkage,
channel selection and rejection limits are engineering defaults, not thresholds
validated by that paper. See docs/flake_training.md for the complete contract.
"""

from __future__ import annotations

import numpy as np

from .flake_imaging import PROCESSING_VERSION

MODEL_SCHEMA = "pydatavault.flake-gaussian.v1"
FEATURE_SCHEMA = "microscope.rgb-ratio-percent.v1"
PROCESSING_KEY = "rgb8-bg-median-region-median-erode1-v1"
CHANNEL_OPTIONS = ("auto", "R", "G", "B", "RG", "RB", "GB", "RGB")
MIN_GROUPS = 3
SHRINKAGE = 0.5
NOISE_FLOOR = 1.0  # percentage points, background = 100
MIN_MARGIN = 0.5  # difference in negative log likelihood, not a probability


def training_group(value, image_hash):
    if value is not None and (not isinstance(value, str) or len(value) > 200):
        raise ValueError("Training group must be text of at most 200 characters")
    return "user:" + value.strip() if value and value.strip() else "image:" + image_hash


def _class_key(row):
    if row["layers"] is not None:
        return f"layers:{row['layers']}"
    return f"nm:{float(row['thickness_nm'])!r}"


def _observations(references):
    # Equal weight per photo within a group, then equal weight per group.
    by_class = {}
    for row in references:
        key = _class_key(row)
        by_class.setdefault(key, []).append(row)
    classes = []
    for key, rows in sorted(by_class.items()):
        grouped = {}
        for row in rows:
            vector = np.asarray(row["normalized_rgb"], dtype=float)
            if vector.shape != (3,) or not np.isfinite(vector).all() or np.any(vector < 0):
                raise ValueError("Training features must be three nonnegative finite RGB percentages")
            grouped.setdefault(row["training_group"], {}).setdefault(row["image_hash"], []).append(vector)
        values = {group: np.mean([np.mean(v, axis=0) for v in photos.values()], axis=0)
                  for group, photos in grouped.items()}
        if len(values) < MIN_GROUPS:
            raise ValueError(f"{key} needs at least {MIN_GROUPS} independent training groups; found {len(values)}")
        nm = [r["thickness_nm"] for r in rows if r["thickness_nm"] is not None]
        classes.append({"class_id": key, "layers": rows[0]["layers"],
                        "thickness_nm": float(np.median(nm)) if nm else None,
                        "values": values, "reference_photos": len({r['image_hash'] for r in rows})})
    if not classes:
        raise ValueError("No usable labeled reference photos; save clean reference regions first")
    return classes


def _fit(values):
    values = np.asarray(values, dtype=float)
    covariance = np.atleast_2d(np.cov(values, rowvar=False, ddof=1))
    covariance = ((1 - SHRINKAGE) * covariance + SHRINKAGE * np.diag(np.diag(covariance))
                  + np.eye(values.shape[1]) * NOISE_FLOOR ** 2)
    return {"mean": values.mean(axis=0).tolist(), "covariance": covariance.tolist(),
            "precision": np.linalg.inv(covariance).tolist(),
            "log_determinant": float(np.linalg.slogdet(covariance)[1])}


def _scores(vector, models):
    scores, distances = [], []
    for model in models:
        delta = vector - np.asarray(model["mean"])
        squared = max(0.0, float(delta @ np.asarray(model["precision"]) @ delta))
        scores.append(0.5 * (squared + model["log_determinant"]))
        distances.append(float(np.sqrt(squared / len(vector))))
    return np.asarray(scores), distances


def _cross_validate(classes, indices):
    records = []
    groups = sorted(set().union(*(c["values"] for c in classes)))
    for held_out in groups:
        fitted = [_fit([v[indices] for group, v in c["values"].items() if group != held_out]) for c in classes]
        for actual, item in enumerate(classes):
            if held_out not in item["values"]:
                continue
            scores, distances = _scores(item["values"][held_out][indices], fitted)
            order = np.argsort(scores, kind="stable")
            predicted = int(order[0])
            margin = float(scores[order[1]] - scores[order[0]]) if len(order) > 1 else None
            records.append({"actual": actual, "predicted": predicted, "group": held_out,
                            "true_distance": distances[actual], "distance": distances[predicted], "margin": margin})
    return records


def train_model(snapshot, *, channels="auto"):
    """Fit one or several thickness classes from a consistent store snapshot."""
    if channels not in CHANNEL_OPTIONS:
        raise ValueError(f"Channels must be one of {', '.join(CHANNEL_OPTIONS)}")
    references = snapshot["references"]
    classes = _observations(references)
    one_class = len(classes) == 1
    # Positives alone cannot identify which channels discriminate other thicknesses.
    options = ["RGB"] if one_class and channels == "auto" else list(CHANNEL_OPTIONS[1:]) if channels == "auto" else [channels]
    candidates = []
    for option in options:
        indices = ["RGB".index(c) for c in option]
        records = _cross_validate(classes, indices)
        accuracy = float(np.mean([np.mean([r["predicted"] == k for r in records if r["actual"] == k])
                                  for k in range(len(classes))])) if not one_class else None
        signed_margin = float(np.median([r["margin"] * (1 if r["actual"] == r["predicted"] else -1)
                                        for r in records])) if not one_class else None
        candidates.append({"channels": list(option), "balanced_accuracy": accuracy,
                           "median_signed_margin": signed_margin, "records": records})
    # Accuracy first; tied accuracy prefers fewer channels, then greater separation.
    selected = max(candidates, key=lambda c: (c["balanced_accuracy"] or 0, -len(c["channels"]),
                                             c["median_signed_margin"] or 0))
    indices = ["RGB".index(c) for c in selected["channels"]]
    fitted = []
    for k, item in enumerate(classes):
        model = _fit([v[indices] for v in item["values"].values()])
        distances = [r["true_distance"] for r in selected["records"] if r["actual"] == k]
        # Estimate positive variation, but never grow an unlimited acceptance region.
        limit = float(np.clip(np.quantile(distances, 0.95) * 1.1, 2.0, 4.0))
        fitted.append({**model, "class_id": item["class_id"], "layers": item["layers"],
                       "thickness_nm": item["thickness_nm"], "distance_limit": limit,
                       "training_groups": len(item["values"]), "reference_photos": item["reference_photos"]})
    confusion = [[0 for _ in classes] for _ in classes]
    accepted, correct_accepted, positive_retention = 0, 0, []
    for row in selected["records"]:
        confusion[row["actual"]][row["predicted"]] += 1
        eligible = row["distance"] <= fitted[row["predicted"]]["distance_limit"] and (one_class or row["margin"] >= MIN_MARGIN)
        accepted += int(eligible)
        correct_accepted += int(eligible and row["actual"] == row["predicted"])
    for k, model in enumerate(fitted):
        rows = [r for r in selected["records"] if r["actual"] == k]
        positive_retention.append(float(np.mean([r["true_distance"] <= model["distance_limit"] for r in rows])))
    diagnostics = []
    if one_class:
        diagnostics.append("positive_only_no_specificity_estimate")
    if min(c["training_groups"] for c in fitted) < 5:
        diagnostics.append("small_training_set")
    if any(group.startswith("image:") for c in classes for group in c["values"]):
        diagnostics.append("ungrouped_photos_assumed_independent")
    if min(positive_retention) < 0.8 or (not one_class and selected["balanced_accuracy"] < 0.8):
        diagnostics.append("weak_training_separation")
    return {"model_schema": MODEL_SCHEMA, "algorithm": "regularized_gaussian", "algorithm_version": 1,
            "profile": snapshot["profile"], "processing_version": PROCESSING_VERSION,
            "feature_schema": FEATURE_SCHEMA, "processing_key": PROCESSING_KEY,
            "feature_units": "substrate_equals_100_percent", "channels": selected["channels"],
            "mode": "single_target" if one_class else "multiclass", "classes": fitted,
            "decision": {"score": "gaussian_nll_equal_prior", "distance": "mahalanobis_rms",
                         "minimum_margin": MIN_MARGIN, "covariance_shrinkage": SHRINKAGE,
                         "noise_floor_percent": NOISE_FLOOR,
                         "requires_review": "weak_training_separation" in diagnostics},
            "training": {"data_fingerprint": snapshot["data_fingerprint"],
                         "photo_hashes": sorted({r["image_hash"] for r in references}),
                         "groups": sorted({r["training_group"] for r in references}),
                         "photo_count": len({r["image_hash"] for r in references}),
                         "region_count": len(references), "group_count": len({r["training_group"] for r in references}),
                         "source_photos": snapshot["source_photos"], "channels_requested": channels,
                         "channel_selection": ("manual" if channels != "auto" else
                                               "positive_only_keep_channels" if one_class else "grouped_leave_one_out"),
                         "diagnostics": diagnostics,
                         "validation": {"kind": "grouped_leave_one_out_training_diagnostic", "independent_test": False,
                                        "balanced_accuracy": selected["balanced_accuracy"],
                                        "confusion_matrix": confusion, "class_order": [c["class_id"] for c in classes],
                                        "coverage": accepted / len(selected["records"]),
                                        "accepted_accuracy": correct_accepted / accepted if accepted and not one_class else None,
                                        "positive_retention_by_class": positive_retention,
                                        "false_positive_rate": None,
                                        "channel_candidates": [{k: v for k, v in c.items() if k != "records"} for c in candidates]}},
            "literature": ["https://doi.org/10.1038/s41598-021-85159-9",
                           "https://doi.org/10.1088/0957-4484/23/49/495713",
                           "https://doi.org/10.1021/acsnano.2c04833"]}


def predict_with_model(features, model, *, quality_warnings=None, image_hash=None, group=None):
    """Reproduce predictions using only a JSON model and compatible features.

    Callers must supply features from the model's profile and processing pipeline.
    This does not infer material or silently convert feature versions.
    """
    if (model.get("model_schema") != MODEL_SCHEMA or model.get("algorithm_version") != 1
            or model.get("algorithm") != "regularized_gaussian"
            or model.get("feature_schema") != FEATURE_SCHEMA or model.get("processing_key") != PROCESSING_KEY
            or model.get("processing_version") != PROCESSING_VERSION
            or model.get("feature_units") != "substrate_equals_100_percent"):
        raise ValueError("Unsupported or incompatible trained model")
    if model.get("stale"):
        raise ValueError("Training data changed; train this profile again before using its model")
    indices = ["RGB".index(c) for c in model["channels"]]
    known = image_hash is not None and image_hash in model["training"]["photo_hashes"]
    known = known or (group is not None and group in model["training"]["groups"])
    results = []
    for feature in features:
        vector = np.asarray(feature["normalized_rgb"], dtype=float)
        if vector.shape != (3,) or not np.isfinite(vector).all() or np.any(vector < 0):
            raise ValueError("Query features must be three nonnegative finite RGB percentages")
        scores, distances = _scores(vector[indices], model["classes"])
        order = np.argsort(scores, kind="stable")
        index = int(order[0])
        candidate = model["classes"][index]
        margin = float(scores[order[1]] - scores[index]) if len(order) > 1 else None
        single = model["mode"] == "single_target"
        status = "target_like" if single else "match"
        if known:
            status = "training_sample"
        elif quality_warnings or feature.get("warnings") or model["decision"]["requires_review"]:
            status = "needs_review"
        elif distances[index] > candidate["distance_limit"]:
            status = "out_of_distribution"
        elif margin is not None and margin < model["decision"]["minimum_margin"]:
            status = "ambiguous"
        results.append({**feature, "layers": candidate["layers"] if status == "match" else None,
                        "thickness_nm": candidate["thickness_nm"] if status == "match" else None,
                        "candidate_layers": candidate["layers"], "candidate_thickness_nm": candidate["thickness_nm"],
                        "status": status, "distance": distances[index], "distance_limit": candidate["distance_limit"],
                        "margin": margin, "score": float(scores[index]), "reference_photos": candidate["reference_photos"],
                        "is_target_like": (status == "target_like") if single and status in ("target_like", "out_of_distribution") else None})
    return results
