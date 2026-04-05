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
    """Compute transform parameters matching pyflexlab's Flakes.coor_transition.

    pyflexlab's algorithm is a *pure-rotation* transform (no scale applied to
    the target point):
        rot  = unit(rel_new) / unit(rel_old)   # |rot| == 1
        out  = (target - ref1) * rot + ref1_new

    The parameters reported here are therefore:

        displacement  -- (ref1_new[0] - ref1[0], ref1_new[1] - ref1[1])
                         i.e. the Cartesian shift of the first reference point,
                         consistent with pyflexlab's own diagnostic print.
        rotation_deg  -- angle of `rot` in degrees (counter-clockwise positive),
                         identical to np.angle(rot)*180/pi in pyflexlab.
        scale         -- dist_new / dist_old (length ratio between the two
                         reference segments); used as a diagnostic — values
                         far from 1.0 indicate a magnification mismatch.
    """
    rel_old = complex(ref2[0] - ref1[0], ref2[1] - ref1[1])
    rel_new = complex(ref2_new[0] - ref1_new[0], ref2_new[1] - ref1_new[1])
    dist_old = abs(rel_old)
    dist_new = abs(rel_new)

    if dist_old == 0:
        return {"scale": 1.0, "rotation_deg": 0.0,
                "displacement": (ref1_new[0] - ref1[0], ref1_new[1] - ref1[1])}

    # Pure-rotation factor (unit vector ratio), same as pyflexlab
    rot = (rel_new / dist_new) / (rel_old / dist_old)

    return {
        "scale": dist_new / dist_old,
        "rotation_deg": math.degrees(math.atan2(rot.imag, rot.real)),
        "displacement": (ref1_new[0] - ref1[0], ref1_new[1] - ref1[1]),
    }
