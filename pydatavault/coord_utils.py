"""Coordinate transformation utilities.

Wraps pyflexlab.auxiliary.Flakes.coor_transition with a fallback
implementation if pyflexlab is not available.
"""

import math
from collections.abc import Callable


def _det3(matrix: list[list[float]]) -> float:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def _solve_3x3(matrix: list[list[float]], values: list[float]) -> tuple[float, float, float]:
    det = _det3(matrix)
    if abs(det) < 1e-12:
        raise ValueError("Reference points must not be collinear")

    solved = []
    for col in range(3):
        replaced = [row[:] for row in matrix]
        for row in range(3):
            replaced[row][col] = values[row]
        solved.append(_det3(replaced) / det)
    return (solved[0], solved[1], solved[2])


def _pyflexlab_coor_transition(
    ref1: tuple, ref1_new: tuple,
    ref2: tuple, ref2_new: tuple,
    target: tuple,
) -> tuple[float, float]:
    from pyflexlab.auxiliary import Flakes
    return Flakes.coor_transition(
        ref1=ref1, ref1_new=ref1_new,
        ref2=ref2, ref2_new=ref2_new,
        target=target, suppress_print=True,
    )


def coor_transition(
    ref1: tuple, ref1_new: tuple,
    ref2: tuple, ref2_new: tuple,
    target: tuple,
    on_fallback: Callable[[Exception], None] | None = None,
) -> tuple[float, float]:
    """Transform target coordinates from old reference frame to new.

    Uses complex-number rotation+scale+translation, matching the algorithm
    in pyflexlab.auxiliary.Flakes.coor_transition.

    Args:
        ref1, ref2: Two reference points in the old coordinate system.
        ref1_new, ref2_new: The same two points in the new coordinate system.
        target: The point to transform (in old coordinates).

    Returns:
        (x_new, y_new) in the new coordinate system.
    """
    try:
        return _pyflexlab_coor_transition(ref1, ref1_new, ref2, ref2_new, target)
    except Exception as exc:
        if on_fallback is not None:
            on_fallback(exc)

    # Fallback: complex-number based transformation
    rel_old = complex(ref2[0] - ref1[0], ref2[1] - ref1[1])
    rel_new = complex(ref2_new[0] - ref1_new[0], ref2_new[1] - ref1_new[1])
    dist_old = abs(rel_old)
    dist_new = abs(rel_new)

    if dist_old == 0:
        raise ValueError("Reference points must not be identical")
    if dist_new == 0:
        raise ValueError("New reference points must not be identical")

    rot = (rel_new / dist_new) / (rel_old / dist_old)
    target_at_ori = complex(target[0] - ref1[0], target[1] - ref1[1])
    target_new = target_at_ori * rot + complex(ref1_new[0], ref1_new[1])
    return (target_new.real, target_new.imag)


def rigid_from_points(
    refs_wafer: list[tuple[float, float]],
    refs_stage: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """Fit a rotation and translation from wafer-relative to stage coordinates.

    Both coordinate systems use their numeric X/Y values directly.  The
    fixed-microscope sign reversal belongs only to drawing and is deliberately
    absent here.  With more than two references this is a least-squares rigid
    fit, so measurement noise cannot introduce affine shear or nonuniform
    scaling.

    Returns ``(cos_theta, sin_theta, tx, ty)`` for::

        x_stage = cos_theta*x_wafer - sin_theta*y_wafer + tx
        y_stage = sin_theta*x_wafer + cos_theta*y_wafer + ty
    """
    if len(refs_wafer) != len(refs_stage) or len(refs_wafer) < 2:
        raise ValueError("Rigid transform requires the same 2 or more reference points")

    old = [complex(x, y) for x, y in refs_wafer]
    new = [complex(x, y) for x, y in refs_stage]
    old_center = sum(old) / len(old)
    new_center = sum(new) / len(new)
    covariance = sum(
        (new_point - new_center) * (old_point - old_center).conjugate()
        for old_point, new_point in zip(old, new)
    )
    if abs(covariance) < 1e-12:
        raise ValueError("Reference points do not define a rotation")

    rotation = covariance / abs(covariance)
    translation = new_center - rotation * old_center
    return (
        rotation.real,
        rotation.imag,
        translation.real,
        translation.imag,
    )


def apply_rigid(
    transform: tuple[float, float, float, float],
    target: tuple[float, float],
) -> tuple[float, float]:
    """Apply a wafer-relative to absolute-stage rigid transform."""
    cos_theta, sin_theta, tx, ty = transform
    x, y = target
    return (
        cos_theta * x - sin_theta * y + tx,
        sin_theta * x + cos_theta * y + ty,
    )


def rigid_transition(
    refs_wafer: list[tuple[float, float]],
    refs_stage: list[tuple[float, float]],
    target_wafer: tuple[float, float],
) -> tuple[float, float]:
    """Convert a wafer-relative target into an absolute stage coordinate."""
    return apply_rigid(rigid_from_points(refs_wafer, refs_stage), target_wafer)


def compute_rigid_transform_info(
    refs_wafer: list[tuple[float, float]],
    refs_stage: list[tuple[float, float]],
) -> dict:
    """Return rigid-fit parameters and reference residuals for display."""
    transform = rigid_from_points(refs_wafer, refs_stage)
    cos_theta, sin_theta, tx, ty = transform
    residuals = [
        math.dist(apply_rigid(transform, old), new)
        for old, new in zip(refs_wafer, refs_stage)
    ]
    return {
        "transform": transform,
        "rotation_deg": math.degrees(math.atan2(sin_theta, cos_theta)),
        "displacement": (tx, ty),
        "residual_rms": math.sqrt(sum(value * value for value in residuals) / len(residuals)),
        "residual_max": max(residuals),
    }


def affine_from_points(
    refs_old: list[tuple[float, float]],
    refs_new: list[tuple[float, float]],
) -> tuple[float, float, float, float, float, float]:
    """Return affine coefficients mapping three old points to three new points.

    The returned tuple is (a, b, c, d, e, f), applied as:
        x_new = a*x_old + b*y_old + c
        y_new = d*x_old + e*y_old + f
    """
    if len(refs_old) != 3 or len(refs_new) != 3:
        raise ValueError("Affine transform requires exactly 3 reference points")

    matrix = [[x, y, 1.0] for x, y in refs_old]
    x_coeffs = _solve_3x3(matrix, [x for x, _ in refs_new])
    y_coeffs = _solve_3x3(matrix, [y for _, y in refs_new])
    return (*x_coeffs, *y_coeffs)


def apply_affine(
    transform: tuple[float, float, float, float, float, float],
    target: tuple[float, float],
) -> tuple[float, float]:
    """Apply affine coefficients to a single point."""
    a, b, c, d, e, f = transform
    x, y = target
    return (a * x + b * y + c, d * x + e * y + f)


def affine_transition(
    refs_old: list[tuple[float, float]],
    refs_new: list[tuple[float, float]],
    target: tuple[float, float],
) -> tuple[float, float]:
    """Transform target coordinates with a three-point affine transform."""
    return apply_affine(affine_from_points(refs_old, refs_new), target)


def compute_affine_transform_info(
    refs_old: list[tuple[float, float]],
    refs_new: list[tuple[float, float]],
) -> dict:
    """Compute display parameters for a three-point affine transform."""
    a, b, c, d, e, f = affine_from_points(refs_old, refs_new)
    determinant = a * e - b * d
    return {
        "coefficients": (a, b, c, d, e, f),
        "determinant": determinant,
        "orientation": "reflected" if determinant < 0 else "preserved",
        "scale_x": math.hypot(a, d),
        "scale_y": math.hypot(b, e),
    }


def compute_transform_info(
    ref1: tuple, ref1_new: tuple,
    ref2: tuple, ref2_new: tuple,
) -> dict:
    """Compute parameters of the full 2-D similarity transform z_new = a*z_old + b.

    Two reference points uniquely determine all 4 degrees of freedom:
        translation  (dx, dy)  — 2 DOF  → from b = z1_new - a*z1_old
        rotation     θ         — 1 DOF  → arg(a)
        uniform scale s        — 1 DOF  → |a|

    Returns:
        displacement  -- (b.real, b.imag): translational component of the
                         transform (image of the old-system origin).
        rotation_deg  -- arg(a) in degrees, counter-clockwise positive.
        scale         -- |a| = dist_new / dist_old.  Values close to 1 mean
                         the two coordinate systems share the same scale;
                         deviations indicate a magnification difference.
    """
    z1_old = complex(ref1[0],     ref1[1])
    z2_old = complex(ref2[0],     ref2[1])
    z1_new = complex(ref1_new[0], ref1_new[1])
    z2_new = complex(ref2_new[0], ref2_new[1])

    dz_old = z2_old - z1_old
    if abs(dz_old) == 0:
        return {"scale": 1.0, "rotation_deg": 0.0,
                "displacement": (ref1_new[0] - ref1[0], ref1_new[1] - ref1[1])}

    a = (z2_new - z1_new) / dz_old   # complex: encodes scale + rotation
    b = z1_new - a * z1_old           # complex: translational offset

    return {
        "scale": abs(a),
        "rotation_deg": math.degrees(math.atan2(a.imag, a.real)),
        "displacement": (b.real, b.imag),
    }
