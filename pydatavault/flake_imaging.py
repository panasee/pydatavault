"""Manual region masks and substrate-relative optical features; no segmentation.

Coordinates always refer to EXIF-oriented, full-resolution RGB pixels.
Labels: -1 ignored, 0 unselected/background, positive integers manual flake regions.
"""

from __future__ import annotations

from io import BytesIO
import hashlib

import numpy as np
from PIL import Image, ImageDraw, ImageOps

PROCESSING_VERSION = "substrate-ratio-v1"
MAX_PIXELS = 16_000_000
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_REGIONS = 256


def validate_rgb(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("Image must be an H x W x 3 uint8 RGB array")
    if min(rgb.shape[:2]) < 3 or rgb.shape[0] * rgb.shape[1] > MAX_PIXELS:
        raise ValueError(f"Image must be at least 3 x 3 and at most {MAX_PIXELS} pixels")
    return rgb


def decode_image(data: bytes) -> np.ndarray:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is empty or exceeds 32 MiB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError(f"Image exceeds {MAX_PIXELS} pixels")
            if source.mode not in ("RGB", "RGBA", "L", "P"):
                raise ValueError("Use an 8-bit RGB microscope image (high-bit-depth/RAW is unsupported)")
            oriented = ImageOps.exif_transpose(source)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                if np.any(np.asarray(oriented.convert("RGBA"))[:, :, 3] != 255):
                    raise ValueError("Transparent images cannot be used for optical calibration")
            return validate_rgb(np.asarray(oriented.convert("RGB")).copy())
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Cannot decode microscope image: {exc}") from exc


def image_digest(rgb: np.ndarray) -> str:
    digest = hashlib.sha256(str(rgb.shape).encode("ascii"))
    digest.update(np.ascontiguousarray(rgb).tobytes())
    return digest.hexdigest()


def validate_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    mask = np.asarray(mask)
    if mask.shape != shape or mask.dtype != np.bool_:
        raise ValueError("Substrate mask must be a boolean array matching the image")
    if np.count_nonzero(mask) < 9:
        raise ValueError("Select at least 9 clean substrate pixels")
    return mask


def validate_labels(labels: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    labels = np.asarray(labels)
    if labels.shape != shape or labels.dtype.kind not in "iu":
        raise ValueError("Region labels must be an integer array matching the image")
    if labels.min() < -1 or labels.max() > 65535:
        raise ValueError("Region IDs must be between -1 and 65535")
    if len(np.unique(labels[labels > 0])) > MAX_REGIONS:
        raise ValueError(f"At most {MAX_REGIONS} regions are supported per photo")
    return labels.astype(np.int32, copy=False)


def substrate_reference(rgb: np.ndarray, substrate_mask: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Measure a manually selected reference without allocating a corrected image."""
    validate_rgb(rgb)
    mask = validate_mask(substrate_mask, rgb.shape[:2])
    pixels = rgb[mask].astype(np.float32)
    reference = np.median(pixels, axis=0)
    if np.any(reference <= 5) or np.any(reference >= 250):
        raise ValueError("Substrate is near black or saturation; choose clean background or retake the photo")
    warnings = []
    if np.mean(np.any((pixels <= 2) | (pixels >= 253), axis=1)) > 0.01:
        warnings.append("substrate_clipping")
    relative_spread = np.percentile(np.abs(pixels - reference), 90, axis=0) / reference
    if np.any(relative_spread > 0.08):
        warnings.append("nonuniform_substrate")
    return reference, warnings


def normalize_image(rgb: np.ndarray, substrate_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Remove multiplicative brightness/channel gains; background becomes 100% RGB.

    This is a relative reference, not a full camera color calibration. It cannot
    recover clipping or compensate arbitrary gamma, spectral changes or shading.
    """
    reference, warnings = substrate_reference(rgb, substrate_mask)
    return rgb.astype(np.float32) * (100.0 / reference), reference, warnings


def polygon_mask(shape: tuple[int, int], points) -> np.ndarray:
    """Rasterize a simple, non-self-intersecting polygon in full-image coordinates."""
    vertices = np.asarray(points, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 2 or not 3 <= len(vertices) <= 512:
        raise ValueError("A polygon needs 3 to 512 (x, y) vertices")
    if not np.isfinite(vertices).all():
        raise ValueError("Polygon coordinates must be finite")
    if np.array_equal(vertices[0], vertices[-1]):
        vertices = vertices[:-1]
    if len(vertices) < 3 or len(np.unique(vertices, axis=0)) != len(vertices):
        raise ValueError("Polygon vertices must be distinct")
    height, width = shape
    if np.any(vertices < 0) or np.any(vertices[:, 0] > width - 1) or np.any(vertices[:, 1] > height - 1):
        raise ValueError("Polygon lies outside the image")

    def cross(a, b, c):
        return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))

    def on_segment(a, b, c):
        return np.all(c >= np.minimum(a, b) - 1e-9) and np.all(c <= np.maximum(a, b) + 1e-9)

    for i in range(len(vertices)):
        a, b = vertices[i], vertices[(i + 1) % len(vertices)]
        for j in range(i + 1, len(vertices)):
            if j == i + 1 or (i == 0 and j == len(vertices) - 1):
                continue
            c, d = vertices[j], vertices[(j + 1) % len(vertices)]
            ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            if ((ab_c * ab_d < 0 and cd_a * cd_b < 0)
                or (abs(ab_c) < 1e-9 and on_segment(a, b, c))
                or (abs(ab_d) < 1e-9 and on_segment(a, b, d))
                or (abs(cd_a) < 1e-9 and on_segment(c, d, a))
                or (abs(cd_b) < 1e-9 and on_segment(c, d, b))):
                raise ValueError("Polygon edges must not cross or touch themselves")
    area = abs(sum(cross(np.zeros(2), vertices[i], vertices[(i + 1) % len(vertices)])
                   for i in range(len(vertices)))) / 2
    if area < 1:
        raise ValueError("Polygon is empty or too narrow")
    mask_image = Image.new("1", (width, height))
    ImageDraw.Draw(mask_image).polygon([tuple(p) for p in vertices], fill=1)
    return np.asarray(mask_image, dtype=bool)


def masks_from_polygons(shape: tuple[int, int], substrate_polygons, flake_polygons) -> tuple[np.ndarray, np.ndarray]:
    """Convert explicit user selections to masks; never infer a missing selection."""
    if not isinstance(substrate_polygons, list) or not 1 <= len(substrate_polygons) <= MAX_REGIONS:
        raise ValueError("Provide at least one substrate polygon (maximum 256)")
    if not isinstance(flake_polygons, list) or not 1 <= len(flake_polygons) <= MAX_REGIONS:
        raise ValueError("Provide at least one flake polygon (maximum 256)")
    background = np.zeros(shape, dtype=bool)
    for points in substrate_polygons:
        background |= polygon_mask(shape, points)
    validate_mask(background, shape)
    labels = np.zeros(shape, dtype=np.int32)
    for region_id, points in enumerate(flake_polygons, 1):
        mask = polygon_mask(shape, points)
        if np.any(mask & (background | (labels > 0))):
            raise ValueError("Flake and substrate polygons must not overlap")
        labels[mask] = region_id
    return labels, background


def region_boundaries(labels: np.ndarray) -> np.ndarray:
    boundary = np.zeros(labels.shape, dtype=bool)
    boundary[1:] |= labels[1:] != labels[:-1]
    boundary[:-1] |= labels[1:] != labels[:-1]
    boundary[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    boundary[:, :-1] |= labels[:, 1:] != labels[:, :-1]
    return boundary & (labels > 0)


def region_features(rgb: np.ndarray, labels: np.ndarray, substrate_mask: np.ndarray) -> tuple[list[dict], np.ndarray, list[str]]:
    rgb = validate_rgb(rgb)
    substrate_mask = validate_mask(substrate_mask, rgb.shape[:2])
    labels = validate_labels(labels, rgb.shape[:2])
    if np.any((labels > 0) & substrate_mask):
        raise ValueError("Flake regions overlap the selected substrate")
    normalized, reference, warnings = normalize_image(rgb, substrate_mask)
    features = []
    for region_id in np.unique(labels[labels > 0]):
        mask = labels == region_id
        # Exclude the one-pixel 4-neighbour boundary from color sampling.
        # This operates on the user mask; it does not detect image edges.
        padded = np.pad(mask, 1)
        inner = mask & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
        sample_mask = inner if inner.sum() >= 9 else mask
        pixels = normalized[sample_mask]
        raw = rgb[sample_mask]
        y, x = np.nonzero(mask)
        region_warnings = []
        if len(raw) < 9:
            region_warnings.append("region_too_small")
        if np.mean(np.any((raw <= 2) | (raw >= 253), axis=1)) > 0.01:
            region_warnings.append("region_clipping")
        if np.any(np.std(pixels, axis=0) > 5):
            region_warnings.append("heterogeneous_region")
        features.append({"region_id": int(region_id), "area_px": int(mask.sum()),
                         "sample_pixels": int(sample_mask.sum()),
                         "bbox": [int(x.min()), int(y.min()), int(x.max() + 1), int(y.max() + 1)],
                         "centroid_xy": [float(x.mean()), float(y.mean())],
                         "normalized_rgb": np.median(pixels, axis=0).tolist(),
                         "std_rgb": np.std(pixels, axis=0).tolist(),
                         "region_rgb": np.median(raw, axis=0).tolist(),
                         "substrate_rgb": reference.tolist(), "warnings": region_warnings})
    return features, normalized, warnings


def predict_regions(features: list[dict], references: list[dict], *, quality_warnings: list[str] | None = None) -> list[dict]:
    """Conservative nearest centroid baseline; scores are distances, not probabilities.

    Each original photo has equal weight. Unrepresented/ambiguous colors are
    rejected; fewer than three photos or a single class only yields a candidate.
    Nanometer-only labels never imply a layer count.
    """
    grouped = {}
    for ref in references:
        key = ("layers", ref["layers"]) if ref["layers"] is not None else ("nm", ref["thickness_nm"])
        grouped.setdefault(key, []).append(ref)
    models = []
    for key, rows in grouped.items():
        by_photo = {}
        for row in rows:
            by_photo.setdefault(row["image_hash"], []).append(row["normalized_rgb"])
        values = np.array([np.mean(v, axis=0) for v in by_photo.values()])
        center = np.mean(values, axis=0)
        spread = np.maximum(np.std(values, axis=0), 1.0)
        nm_values = [r["thickness_nm"] for r in rows if r["thickness_nm"] is not None]
        models.append({"layers": key[1] if key[0] == "layers" else None,
                       "thickness_nm": float(np.median(nm_values)) if nm_values else None,
                       "center": center, "spread": spread, "photo_count": len(values)})
    results = []
    for feature in features:
        result = {**feature, "layers": None, "thickness_nm": None, "candidate_layers": None,
                  "candidate_thickness_nm": None, "status": "no_reference", "distance": None,
                  "margin": None, "reference_photos": 0}
        if models:
            ranked = sorted(((float(np.sqrt(np.mean(((np.array(feature["normalized_rgb"]) - m["center"]) / m["spread"]) ** 2))), i)
                             for i, m in enumerate(models)))
            distance, index = ranked[0]
            model = models[index]
            margin = ranked[1][0] - distance if len(ranked) > 1 else None
            status = "match"
            if distance > 3:
                status = "out_of_distribution"
            elif margin is not None and margin < 0.75:
                status = "ambiguous"
            elif len(models) < 2 or model["photo_count"] < 3:
                status = "insufficient_reference"
            elif quality_warnings or feature["warnings"]:
                status = "needs_review"
            result.update(candidate_layers=model["layers"], candidate_thickness_nm=model["thickness_nm"],
                          status=status, distance=distance, margin=margin, reference_photos=model["photo_count"])
            if status == "match":
                result.update(layers=model["layers"], thickness_nm=model["thickness_nm"])
        results.append(result)
    return results
