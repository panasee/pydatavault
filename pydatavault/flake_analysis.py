"""Local manual-selection thickness tool, independent of GUI and HTTP transport."""

from __future__ import annotations

import numpy as np

from .flake_imaging import (PROCESSING_VERSION, decode_image, image_digest,
                            predict_regions, region_features, validate_rgb)
from .flake_photo_store import FlakePhotoStore
from .flake_training import predict_with_model, train_model, training_group


class StaleModelError(ValueError):
    pass


class FlakeAnalysisService:
    """Read reference data and classify only explicitly supplied regions."""

    def __init__(self, database_path=None, *, store=None):
        self.store = store if store is not None else FlakePhotoStore(database_path)

    def train(self, profile_id: int, *, channels="auto") -> dict:
        snapshot = self.store.training_snapshot(profile_id)
        return self.store.save_model(train_model(snapshot, channels=channels))

    def analyze(self, image: bytes | np.ndarray, profile_id: int, *, substrate_mask=None, labels=None,
                training_group_id=None) -> dict:
        if labels is None or substrate_mask is None:
            raise ValueError("Draw substrate and flake regions before estimating thickness")
        profile = self.store.get_profile(profile_id)
        rgb = decode_image(image) if isinstance(image, bytes) else validate_rgb(image)
        features, _, warnings = region_features(rgb, labels, substrate_mask)
        if not features:
            raise ValueError("Draw at least one flake region before estimating thickness")
        digest = image_digest(rgb)
        group = training_group(training_group_id, digest)
        model = self.store.get_model(profile_id)
        if model is not None:
            if model["stale"]:
                raise StaleModelError("Reference data changed; train this profile again before estimating thickness")
            predictions = predict_with_model(features, model, quality_warnings=warnings, image_hash=digest, group=group)
        else:
            references = self.store.reference_features(profile_id, exclude_hash=digest)
            predictions = predict_regions(features, references, quality_warnings=warnings)
        return {"processing_version": PROCESSING_VERSION, "profile": profile,
                "image_size": [rgb.shape[1], rgb.shape[0]], "warnings": warnings,
                "inference_method": "trained_model" if model else "reference_baseline",
                "model_id": model["model_id"] if model else None, "model_revision": model["revision"] if model else None,
                "model_mode": model["mode"] if model else None, "channels": model["channels"] if model else list("RGB"),
                "substrate_rgb": features[0]["substrate_rgb"],
                "regions": predictions}
