"""Directory links, including unprivileged Windows junctions."""

import os
import stat
from pathlib import Path


def is_junction(path: Path) -> bool:
    """Recognize junctions (including broken ones) on Python 3.10+."""
    if os.name != "nt":
        return False
    try:
        return path.lstat().st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except FileNotFoundError:
        return False


def create_directory_link(target: Path, link: Path):
    """Prefer a symlink; fall back only when Windows denies that privilege."""
    target = target.resolve()
    link.parent.mkdir(parents=True, exist_ok=True)
    if (link.is_symlink() or is_junction(link)) and link.resolve() == target:
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise
        # CPython's Windows API avoids shell quoting and needs no elevation.
        import _winapi

        _winapi.CreateJunction(str(target), str(link.absolute()))


def remove_directory_link(link: Path):
    """Remove only the link, never the contents of its target."""
    if is_junction(link):
        link.rmdir()
    else:
        link.unlink()
