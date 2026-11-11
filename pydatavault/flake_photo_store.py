"""Portable, transactional photo/annotation database; no GUI or lab config import."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

import numpy as np

from .flake_imaging import PROCESSING_VERSION, decode_image, image_digest, region_features, validate_labels, validate_mask
from .flake_training import training_group

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS photo_profiles (
    profile_id INTEGER PRIMARY KEY,
    material TEXT NOT NULL COLLATE NOCASE,
    substrate TEXT NOT NULL COLLATE NOCASE,
    optical_setup TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(material, substrate, optical_setup)
);
CREATE TABLE IF NOT EXISTS flake_photos (
    photo_id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES photo_profiles(profile_id),
    image_hash TEXT NOT NULL,
    source_name TEXT NOT NULL,
    original BLOB NOT NULL,
    arrays BLOB NOT NULL,
    processing_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(profile_id, image_hash)
);
CREATE TABLE IF NOT EXISTS photo_regions (
    photo_id INTEGER NOT NULL REFERENCES flake_photos(photo_id) ON DELETE CASCADE,
    region_id INTEGER NOT NULL,
    layers INTEGER CHECK(layers > 0),
    thickness_nm REAL CHECK(thickness_nm > 0),
    provenance TEXT NOT NULL,
    feature_json TEXT NOT NULL,
    PRIMARY KEY(photo_id, region_id),
    CHECK(layers IS NOT NULL OR thickness_nm IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS photos_profile_index ON flake_photos(profile_id);
CREATE TABLE IF NOT EXISTS flake_models (
    model_id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES photo_profiles(profile_id),
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(profile_id, revision)
);
"""


def default_photo_database() -> Path:
    root = os.environ.get("VAULT_DB_PATH")
    if not root:
        raise ValueError("Pass a database path or set VAULT_DB_PATH")
    return Path(root) / ".labdb" / "flake_photo_library.db"


class FlakePhotoStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_photo_database()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Photo database was created by a newer version of PyDataVault")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_profile(self, material: str, substrate: str, optical_setup: str) -> int:
        values = tuple(v.strip() for v in (material, substrate, optical_setup))
        if not all(values):
            raise ValueError("Material, substrate and optical setup are required")
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO photo_profiles(material,substrate,optical_setup) VALUES (?,?,?)", values)
            return conn.execute("SELECT profile_id FROM photo_profiles WHERE material=? AND substrate=? AND optical_setup=?", values).fetchone()[0]

    def get_profile(self, profile_id: int) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM photo_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown photo profile")
        return dict(row)

    def list_profiles(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT p.*, COUNT(f.photo_id) AS photo_count
                FROM photo_profiles p LEFT JOIN flake_photos f ON p.profile_id=f.profile_id
                GROUP BY p.profile_id ORDER BY material, substrate, optical_setup""")]

    def list_photos(self, profile_id: int) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT f.photo_id, f.source_name, f.revision,
                f.created_at, f.updated_at, COUNT(r.region_id) AS region_count
                FROM flake_photos f LEFT JOIN photo_regions r ON r.photo_id=f.photo_id
                WHERE profile_id=? GROUP BY f.photo_id ORDER BY f.photo_id DESC""", (profile_id,))]

    def save_photo(self, profile_id: int, original: bytes, source_name: str,
                   labels: np.ndarray, substrate_mask: np.ndarray, annotations: dict[int, dict],
                   *, metadata: dict | None = None, photo_id: int | None = None,
                   expected_revision: int | None = None) -> int:
        """Atomically append or revise a photo, preserving source bytes and masks.

        Every visible region requires ground truth. Use label -1 to exclude an
        uncertain region. Revisions require an optimistic-lock revision number.
        """
        rgb = decode_image(original)
        labels = validate_labels(labels, rgb.shape[:2])
        substrate_mask = validate_mask(substrate_mask, rgb.shape[:2])
        features, normalized, warnings = region_features(rgb, labels, substrate_mask)
        if not features:
            raise ValueError("Label at least one flake region before saving")
        if set(annotations) != {f["region_id"] for f in features}:
            raise ValueError("Provide thickness for every region, or remove unwanted regions")
        validated = []
        for feature in features:
            annotation = annotations[feature["region_id"]]
            layers, thickness = annotation.get("layers"), annotation.get("thickness_nm")
            if layers is not None and (type(layers) is not int or layers < 1):
                raise ValueError("Layer counts must be positive integers")
            if thickness is not None and (isinstance(thickness, bool) or not isinstance(thickness, (float, int))
                                          or not math.isfinite(thickness) or thickness <= 0):
                raise ValueError("Thickness must be a positive finite value in nm")
            if layers is None and thickness is None:
                raise ValueError(f"Region {feature['region_id']} needs a layer count or thickness in nm")
            provenance = str(annotation.get("provenance", "")).strip()
            if not provenance:
                raise ValueError("Record how each thickness was established (AFM, Raman, etc.)")
            validated.append((feature["region_id"], layers, thickness, provenance, json.dumps(feature, allow_nan=False)))
        buffer = BytesIO()
        np.savez_compressed(buffer, labels=labels, substrate_mask=substrate_mask, normalized=normalized)
        meta = dict(metadata or {})
        training_group(meta.get("training_group"), image_digest(rgb))
        meta.update(quality_warnings=warnings, width=rgb.shape[1], height=rgb.shape[0])
        metadata_json = json.dumps(meta, allow_nan=False)
        digest = image_digest(rgb)
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM photo_profiles WHERE profile_id=?", (profile_id,)).fetchone() is None:
                raise ValueError("Unknown photo profile")
            try:
                if photo_id is None:
                    cursor = conn.execute("""INSERT INTO flake_photos
                        (profile_id,image_hash,source_name,original,arrays,processing_version,metadata_json)
                        VALUES (?,?,?,?,?,?,?)""", (profile_id, digest, Path(source_name).name, original,
                                                    buffer.getvalue(), PROCESSING_VERSION, metadata_json))
                    photo_id = cursor.lastrowid
                else:
                    if expected_revision is None:
                        raise ValueError("An expected revision is required when editing a saved photo")
                    cursor = conn.execute("""UPDATE flake_photos SET arrays=?, metadata_json=?,
                        processing_version=?, revision=revision+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        WHERE photo_id=? AND profile_id=? AND image_hash=? AND revision=?""",
                        (buffer.getvalue(), metadata_json, PROCESSING_VERSION, photo_id, profile_id, digest, expected_revision))
                    if cursor.rowcount != 1:
                        raise ValueError("Photo changed or belongs to a different profile/image; reload it before editing")
                    conn.execute("DELETE FROM photo_regions WHERE photo_id=?", (photo_id,))
                conn.executemany("""INSERT INTO photo_regions
                    (photo_id,region_id,layers,thickness_nm,provenance,feature_json) VALUES (?,?,?,?,?,?)""",
                    [(photo_id, *row) for row in validated])
            except sqlite3.IntegrityError as exc:
                raise ValueError("This photo already exists in the profile; open its saved entry to edit it") from exc
        return photo_id

    def load_photo(self, photo_id: int) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM flake_photos WHERE photo_id=?", (photo_id,)).fetchone()
            annotations = conn.execute("SELECT * FROM photo_regions WHERE photo_id=?", (photo_id,)).fetchall()
        if row is None:
            raise ValueError("Unknown photo")
        photo = dict(row)
        photo["rgb"] = decode_image(photo["original"])
        with np.load(BytesIO(photo.pop("arrays")), allow_pickle=False) as arrays:
            photo.update({key: arrays[key].copy() for key in ("labels", "substrate_mask", "normalized")})
        photo["metadata"] = json.loads(photo.pop("metadata_json"))
        photo["annotations"] = {r["region_id"]: {"layers": r["layers"], "thickness_nm": r["thickness_nm"],
                                                "provenance": r["provenance"]} for r in annotations}
        return photo

    def reference_features(self, profile_id: int, *, exclude_hash: str | None = None) -> list[dict]:
        return [r for r in self.training_snapshot(profile_id)["references"] if r["image_hash"] != exclude_hash]

    def training_snapshot(self, profile_id: int) -> dict:
        """Read all source revisions and eligible features from one SQLite snapshot."""
        with self.connect() as conn:
            conn.execute("BEGIN")
            return self._training_snapshot(conn, profile_id)

    @staticmethod
    def _training_snapshot(conn, profile_id):
        profile = conn.execute("SELECT * FROM photo_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if profile is None:
            raise ValueError("Unknown photo profile")
        source = [dict(r) for r in conn.execute("""SELECT photo_id, revision, image_hash, processing_version
            FROM flake_photos WHERE profile_id=? ORDER BY photo_id""", (profile_id,))]
        signature = json.dumps({"profile": dict(profile), "photos": source}, sort_keys=True, separators=(",", ":"))
        rows = conn.execute("""SELECT r.*, f.image_hash, f.metadata_json FROM photo_regions r
            JOIN flake_photos f ON f.photo_id=r.photo_id WHERE f.profile_id=? AND f.processing_version=?
            ORDER BY f.photo_id, r.region_id""", (profile_id, PROCESSING_VERSION)).fetchall()
        result = []
        for row in rows:
            feature = json.loads(row["feature_json"])
            metadata = json.loads(row["metadata_json"])
            if feature["warnings"] or metadata.get("quality_warnings"):
                continue
            result.append({**feature, "image_hash": row["image_hash"], "layers": row["layers"],
                           "thickness_nm": row["thickness_nm"],
                           "training_group": training_group(metadata.get("training_group"), row["image_hash"])})
        return {"profile": dict(profile), "source_photos": source, "references": result,
                "data_fingerprint": hashlib.sha256(signature.encode("utf-8")).hexdigest()}

    def save_model(self, payload: dict) -> dict:
        """Publish an immutable model only if its source data has not changed."""
        profile_id = payload["profile"]["profile_id"]
        encoded = json.dumps(payload, allow_nan=False)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._training_snapshot(conn, profile_id)
            if payload["training"]["data_fingerprint"] != current["data_fingerprint"]:
                raise ValueError("Reference data changed during training; train the profile again")
            revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM flake_models WHERE profile_id=?", (profile_id,)).fetchone()[0]
            cursor = conn.execute("INSERT INTO flake_models(profile_id,revision,payload_json) VALUES (?,?,?)",
                                  (profile_id, revision, encoded))
            model_id = cursor.lastrowid
        return self.get_model(profile_id, model_id=model_id)

    def get_model(self, profile_id=None, *, model_id=None) -> dict | None:
        """Return latest or pinned model, with live stale state; never refit on read."""
        if profile_id is None and model_id is None:
            raise ValueError("Provide profile_id or model_id")
        with self.connect() as conn:
            conn.execute("BEGIN")
            if model_id is None:
                row = conn.execute("SELECT * FROM flake_models WHERE profile_id=? ORDER BY revision DESC LIMIT 1", (profile_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM flake_models WHERE model_id=?", (model_id,)).fetchone()
            if row is None or (profile_id is not None and row["profile_id"] != profile_id):
                return None
            current = self._training_snapshot(conn, row["profile_id"])
            payload = json.loads(row["payload_json"])
            return {**payload, "model_id": row["model_id"], "revision": row["revision"],
                    "created_at": row["created_at"],
                    "stale": payload["training"]["data_fingerprint"] != current["data_fingerprint"],
                    "current_data_fingerprint": current["data_fingerprint"]}
