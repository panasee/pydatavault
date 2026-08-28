"""Independent SQLite store for microscope flake-layer calibrations."""

from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class CalibrationSample:
    """One image region with a user-provided layer count."""

    layers: int
    normalized_rgb: tuple[float, float, float]
    source_image: str
    substrate_rgb: tuple[float, float, float]
    region_rgb: tuple[float, float, float]


SCHEMA = """
CREATE TABLE IF NOT EXISTS calibration_materials (
    material_id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL COLLATE NOCASE,
    substrate TEXT NOT NULL COLLATE NOCASE,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(material_name, substrate)
);

CREATE TABLE IF NOT EXISTS calibration_samples (
    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL
        REFERENCES calibration_materials(material_id) ON DELETE CASCADE,
    layers INTEGER NOT NULL CHECK(layers > 0),
    normalized_r REAL NOT NULL,
    normalized_g REAL NOT NULL,
    normalized_b REAL NOT NULL,
    source_image TEXT NOT NULL,
    substrate_r REAL NOT NULL,
    substrate_g REAL NOT NULL,
    substrate_b REAL NOT NULL,
    region_r REAL NOT NULL,
    region_g REAL NOT NULL,
    region_b REAL NOT NULL
);
"""


class FlakeCalibrationStore:
    """Persist material/substrate profiles independently from the lab database."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else config.FLAKE_CALIBRATION_DB_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_materials(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT m.material_id, m.material_name, m.substrate, m.updated_at,
                          COUNT(s.sample_id) AS sample_count,
                          GROUP_CONCAT(DISTINCT s.layers) AS layer_counts
                   FROM calibration_materials m
                   LEFT JOIN calibration_samples s ON s.material_id=m.material_id
                   GROUP BY m.material_id
                   ORDER BY m.material_name COLLATE NOCASE, m.substrate COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_material(self, material_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT material_id, material_name, substrate, updated_at
                   FROM calibration_materials WHERE material_id=?""",
                (material_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_samples(self, material_id: int) -> list[CalibrationSample]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT layers, normalized_r, normalized_g, normalized_b,
                          source_image, substrate_r, substrate_g, substrate_b,
                          region_r, region_g, region_b
                   FROM calibration_samples
                   WHERE material_id=? ORDER BY layers, sample_id""",
                (material_id,),
            ).fetchall()
        return [
            CalibrationSample(
                row["layers"],
                (row["normalized_r"], row["normalized_g"], row["normalized_b"]),
                row["source_image"],
                (row["substrate_r"], row["substrate_g"], row["substrate_b"]),
                (row["region_r"], row["region_g"], row["region_b"]),
            )
            for row in rows
        ]

    def save_calibration(
        self,
        material_name: str,
        substrate: str,
        samples: list[CalibrationSample],
        material_id: int | None = None,
    ) -> int:
        """Create or atomically recalibrate one material/substrate profile."""
        material_name = material_name.strip()
        substrate = substrate.strip()
        if not material_name:
            raise ValueError("Material name is required")
        if not substrate:
            raise ValueError("Fixed substrate description is required")
        if not samples:
            raise ValueError("Add at least one known layer region")
        for index, sample in enumerate(samples, start=1):
            self._validate_sample(sample, index)

        with self._connect() as conn:
            if material_id is None:
                try:
                    cursor = conn.execute(
                        """INSERT INTO calibration_materials
                           (material_name, substrate) VALUES (?,?)""",
                        (material_name, substrate),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "This material and substrate already exist; select it to recalibrate"
                    ) from exc
                material_id = cursor.lastrowid
            else:
                existing = conn.execute(
                    """SELECT material_name, substrate FROM calibration_materials
                       WHERE material_id=?""",
                    (material_id,),
                ).fetchone()
                if existing is None:
                    raise ValueError("Selected calibration material no longer exists")
                if (
                    existing["material_name"] != material_name
                    or existing["substrate"] != substrate
                ):
                    raise ValueError("Existing material and substrate cannot be renamed here")
                conn.execute(
                    "DELETE FROM calibration_samples WHERE material_id=?",
                    (material_id,),
                )

            conn.executemany(
                """INSERT INTO calibration_samples
                   (material_id, layers, normalized_r, normalized_g, normalized_b,
                    source_image, substrate_r, substrate_g, substrate_b,
                    region_r, region_g, region_b)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        material_id,
                        sample.layers,
                        *sample.normalized_rgb,
                        sample.source_image,
                        *sample.substrate_rgb,
                        *sample.region_rgb,
                    )
                    for sample in samples
                ],
            )
            conn.execute(
                """UPDATE calibration_materials
                   SET updated_at=datetime('now','localtime') WHERE material_id=?""",
                (material_id,),
            )
        return material_id

    @staticmethod
    def _validate_sample(sample: CalibrationSample, index: int) -> None:
        if sample.layers < 1:
            raise ValueError(f"Calibration sample {index} has an invalid layer count")
        if not sample.source_image.strip():
            raise ValueError(f"Calibration sample {index} has no source image name")
        values = (*sample.normalized_rgb, *sample.substrate_rgb, *sample.region_rgb)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Calibration sample {index} contains non-finite values")
        if any(value <= 0 or value > 255 for value in sample.substrate_rgb):
            raise ValueError(f"Calibration sample {index} has invalid substrate RGB")
        if any(value < 0 or value > 255 for value in sample.region_rgb):
            raise ValueError(f"Calibration sample {index} has invalid region RGB")
        expected = tuple(
            100.0 * region / substrate
            for substrate, region in zip(sample.substrate_rgb, sample.region_rgb)
        )
        if math.dist(expected, sample.normalized_rgb) > 1e-6:
            raise ValueError(
                f"Calibration sample {index} normalized RGB does not match its raw RGB"
            )
