"""Coordinate transformation utilities.

Wraps pyflexlab.auxiliary.Flakes.coor_transition with a fallback
implementation if pyflexlab is not available.
"""

import math


def coor_transition(
    ref1: tuple, ref1_new: tuple,
    ref2: tuple, ref2_new: tuple,
    target: tuple,
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
        from pyflexlab.auxiliary import Flakes
        result = Flakes.coor_transition(
            ref1=ref1, ref1_new=ref1_new,
            ref2=ref2, ref2_new=ref2_new,
            target=target, suppress_print=True,
        )
        return result
    except (ImportError, Exception):
        pass

    # Fallback: complex-number based transformation
    rel_old = complex(ref2[0] - ref1[0], ref2[1] - ref1[1])
    rel_new = complex(ref2_new[0] - ref1_new[0], ref2_new[1] - ref1_new[1])
    dist_old = abs(rel_old)
    dist_new = abs(rel_new)

    if dist_old == 0:
        return (target[0], target[1])

    rot = (rel_new / dist_new) / (rel_old / dist_old)
    target_at_ori = complex(target[0] - ref1[0], target[1] - ref1[1])
    target_new = target_at_ori * rot + complex(ref1_new[0], ref1_new[1])
    return (target_new.real, target_new.imag)


def compute_transform_info(
    ref1: tuple, ref1_new: tuple,
    ref2: tuple, ref2_new: tuple,
) -> dict:
    """Compute transformation metadata (rotation angle, scale ratio)."""
    rel_old = complex(ref2[0] - ref1[0], ref2[1] - ref1[1])
    rel_new = complex(ref2_new[0] - ref1_new[0], ref2_new[1] - ref1_new[1])
    dist_old = abs(rel_old)
    dist_new = abs(rel_new)

    if dist_old == 0:
        return {"scale": 1.0, "rotation_deg": 0.0}

    rot = (rel_new / dist_new) / (rel_old / dist_old)
    angle_deg = math.degrees(math.atan2(rot.imag, rot.real))
    scale = dist_new / dist_old

    return {
        "scale": scale,
        "rotation_deg": angle_deg,
        "displacement": (ref1_new[0] - ref1[0], ref1_new[1] - ref1[1]),
    }
