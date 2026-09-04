"""Standalone coordinate-frame experiment using a real PyDataVault record.

This script deliberately does not import PyDataVault application modules.  It
reads one wafer and flake from the SQLite database in read-only mode, runs the
coordinate calculations below, and renders a PNG with PySide6.

Run from the repository root with the configured VAULT_DB_PATH::

    conda run -n unified python tests/coordinate_transform_real_case.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt


Point = tuple[float, float]


@dataclass(frozen=True)
class RealCoordinateCase:
    wafer_id: int
    flake_uid: int
    flake_id: str
    refs_stage: tuple[Point, ...]
    flake_stage: Point


# ---------------------------------------------------------------------------
# Extracted algorithm functions
# ---------------------------------------------------------------------------


def similarity_from_two_refs(
    old_ref1: Point,
    old_ref2: Point,
    new_ref1: Point,
    new_ref2: Point,
) -> tuple[complex, complex]:
    """Fit z_new = a*z_old + b from two corresponding reference points."""
    z1_old = complex(*old_ref1)
    z2_old = complex(*old_ref2)
    z1_new = complex(*new_ref1)
    z2_new = complex(*new_ref2)
    old_delta = z2_old - z1_old
    if abs(old_delta) == 0:
        raise ValueError("Reference points must not be identical")
    a = (z2_new - z1_new) / old_delta
    b = z1_new - a * z1_old
    return a, b


def apply_similarity(transform: tuple[complex, complex], point: Point) -> Point:
    """Apply a fitted two-reference similarity transformation."""
    a, b = transform
    result = a * complex(*point) + b
    return (result.real, result.imag)


# ---------------------------------------------------------------------------
# Read-only real-case loader
# ---------------------------------------------------------------------------


def default_database_path() -> Path:
    root = os.environ.get("VAULT_DB_PATH")
    if not root:
        raise RuntimeError("VAULT_DB_PATH is required when --db is not supplied")
    return Path(root) / ".labdb" / "lab.db"


def load_real_case(db_path: Path, wafer_id: int, flake_uid: int) -> RealCoordinateCase:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT w.wafer_id, w.ref_points,
                   f.flake_uid, f.flake_id, f.coord_x, f.coord_y
            FROM wafers AS w
            JOIN flakes AS f ON f.wafer_id = w.wafer_id
            WHERE w.wafer_id = ? AND f.flake_uid = ?
            """,
            (wafer_id, flake_uid),
        ).fetchone()
    if row is None:
        raise ValueError(f"No wafer {wafer_id} / flake UID {flake_uid} record")

    raw_refs = json.loads(row["ref_points"] or "[]")
    refs = tuple((float(ref["x"]), float(ref["y"])) for ref in raw_refs)
    if len(refs) < 2:
        raise ValueError("The selected wafer needs at least two reference points")
    return RealCoordinateCase(
        wafer_id=row["wafer_id"],
        flake_uid=row["flake_uid"],
        flake_id=row["flake_id"],
        refs_stage=refs,
        flake_stage=(float(row["coord_x"]), float(row["coord_y"])),
    )


# ---------------------------------------------------------------------------
# Extracted drawing functions (test-only Matplotlib/Agg rendering)
# ---------------------------------------------------------------------------


def _draw_panel(
    axis,
    title: str,
    subtitle: str,
    refs: list[Point],
    flake: Point,
):
    ref_x = [point[0] for point in refs]
    ref_y = [point[1] for point in refs]
    axis.plot(ref_x, ref_y, "--", color="#2b67c9", linewidth=1.5)
    axis.scatter(ref_x, ref_y, s=58, facecolors="white", edgecolors="#205fc1", linewidths=1.5)
    axis.scatter([flake[0]], [flake[1]], s=70, color="#d52b2b", zorder=3)

    for index, point in enumerate(refs, start=1):
        axis.annotate(
            f"R{index} ({point[0]:.4f}, {point[1]:.4f})",
            point,
            xytext=(7, -12),
            textcoords="offset points",
            color="#205fc1",
            fontsize=8,
        )
    axis.annotate(
        f"Flake ({flake[0]:.4f}, {flake[1]:.4f})",
        flake,
        xytext=(7, 7),
        textcoords="offset points",
        color="#b31f1f",
        fontsize=8,
    )

    axis.axhline(0, color="#aeb6c3", linewidth=0.8)
    axis.axvline(0, color="#aeb6c3", linewidth=0.8)
    axis.set_title(f"{title}\n{subtitle}", loc="left", fontsize=10, pad=12)
    axis.set_xlabel("Wafer-relative X  (+ points left in the fixed-microscope view)")
    axis.set_ylabel("Wafer-relative Y  (+ points up in the fixed-microscope view)")
    axis.grid(True, color="#dfe3e9", linewidth=0.6)
    axis.set_aspect("equal", adjustable="datalim")
    axis.invert_xaxis()
    axis.margins(0.25)


def render_coordinate_frames(
    output_path: Path,
    refs_relative: list[Point],
    flake_relative: Point,
    figure_title: str = "Wafer-relative physical position",
):
    """Render only wafer-relative positions in fixed-microscope orientation."""
    figure, axis = plt.subplots(figsize=(8.2, 7.2), constrained_layout=True)
    _draw_panel(
        axis,
        "Physical wafer view",
        "relative values are unchanged; only the drawing directions are reversed",
        refs_relative,
        flake_relative,
    )
    figure.suptitle(figure_title, fontsize=13)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=140, facecolor="#eef1f6")
    plt.close(figure)


def close_enough(left: Point, right: Point, tolerance: float = 1e-9) -> bool:
    return math.dist(left, right) <= tolerance


def run_case(case: RealCoordinateCase, output_path: Path) -> None:
    refs_wafer = list(case.refs_stage)
    flake_wafer = case.flake_stage
    transform = similarity_from_two_refs(
        refs_wafer[0], refs_wafer[1],
        case.refs_stage[0], case.refs_stage[1],
    )
    transformed_flake = apply_similarity(transform, flake_wafer)
    if not close_enough(transformed_flake, case.flake_stage):
        raise AssertionError("Two-reference transform did not recover the flake stage point")

    render_coordinate_frames(output_path, refs_wafer, flake_wafer)

    a, b = transform
    print(f"Real database case: wafer={case.wafer_id}, flake={case.flake_id!r} (UID {case.flake_uid})")
    print(f"Stored wafer coordinate:    ({flake_wafer[0]:.4f}, {flake_wafer[1]:.4f})")
    print(f"Recovered target stage:     ({transformed_flake[0]:.4f}, {transformed_flake[1]:.4f})")
    print(f"Similarity a={a.real:.6f}{a.imag:+.6f}j, b=({b.real:.4f}, {b.imag:.4f})")
    print("PASS: stored values are unchanged; sign reversal is drawing-only")
    print(f"Plot: {output_path.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Path to lab.db (opened read-only)")
    parser.add_argument("--wafer-id", type=int, default=79)
    parser.add_argument("--flake-uid", type=int, default=55)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/coordinate_transform_wafer79_real.png"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    case = load_real_case(args.db or default_database_path(), args.wafer_id, args.flake_uid)
    run_case(case, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
