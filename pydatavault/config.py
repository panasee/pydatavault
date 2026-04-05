"""Configuration and path management for PyDataVault."""

import os
from pathlib import Path


def get_root_path() -> Path:
    """Get the PyDataVault root path from VAULT_DB_PATH, or a local fallback."""
    env_path = os.environ.get("VAULT_DB_PATH")
    if env_path:
        return Path(env_path)
    fallback = Path(__file__).parent.parent / "db_test"
    fallback.mkdir(exist_ok=True)
    return fallback


def get_pyflexlab_out_path() -> Path:
    """Get pyflexlab's OUT_DB_PATH by deferring to pyflexlab itself.

    pyflexlab resolves machine-specific env var variants (e.g. PYLAB_DB_OUT_XXX)
    via set_envs() on import, so reading pyflexlab.constants.OUT_DB_PATH is the
    only correct way to get the final resolved path.

    Raises RuntimeError if pyflexlab is not installed or OUT_DB_PATH is not set.
    """
    try:
        from pyflexlab import constants as _pfl
        if _pfl.OUT_DB_PATH is not None:
            return Path(_pfl.OUT_DB_PATH)
        raise RuntimeError(
            "pyflexlab is installed but OUT_DB_PATH is not set. "
            "Set PYLAB_DB_OUT (or a machine-specific variant) before launching PyDataVault."
        )
    except ImportError:
        raise RuntimeError(
            "pyflexlab is not installed. "
            "Install it or set PYLAB_DB_OUT so PyDataVault can locate the measurement data directory."
        )


ROOT_PATH = get_root_path()
DB_DIR = ROOT_PATH / ".labdb"
DB_FILE = DB_DIR / "lab.db"
PROJECTS_DIR = ROOT_PATH / "projects"
SHARED_DIR = ROOT_PATH / "shared"
FLAKES_DIR = SHARED_DIR / "flakes"
ARCHIVE_DIR = ROOT_PATH / "archive"
TEMPLATES_DIR = ROOT_PATH / "templates"

# Resolved at call time so that changes to PYLAB_DB_OUT after import are picked up.
PYFLEXLAB_OUT_PATH = get_pyflexlab_out_path()


def ensure_dirs():
    """Create all PyDataVault-owned directories.

    Does NOT create PYFLEXLAB_OUT_PATH — that directory is owned by pyflexlab
    and may not even be on this machine's local filesystem.
    """
    for d in [DB_DIR, PROJECTS_DIR, SHARED_DIR,
              FLAKES_DIR, ARCHIVE_DIR, TEMPLATES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
