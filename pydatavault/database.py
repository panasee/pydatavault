"""SQLite database layer for PyDataVault."""

import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager
from typing import Optional
from . import config


def _dict_factory(cursor, row):
    """Return query results as dicts instead of tuples."""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


@contextmanager
def get_conn():
    """Context manager for database connections."""
    conn = sqlite3.connect(str(config.DB_FILE))
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ──────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS wafer_boxes (
    box_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    rows        INTEGER NOT NULL DEFAULT 5,
    cols        INTEGER NOT NULL DEFAULT 5,
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS wafers (
    wafer_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id      INTEGER NOT NULL REFERENCES wafer_boxes(box_id) ON DELETE CASCADE,
    row         INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    label       TEXT DEFAULT '',
    -- Up to 3 reference points stored as JSON list:
    -- [{"photo_path":"...", "x":..., "y":...}, ...]
    ref_points  TEXT DEFAULT '[]',
    notes       TEXT DEFAULT '',
    UNIQUE(box_id, row, col)
);

CREATE TABLE IF NOT EXISTS flakes (
    flake_id    TEXT PRIMARY KEY,
    wafer_id    INTEGER REFERENCES wafers(wafer_id) ON DELETE SET NULL,
    material    TEXT NOT NULL DEFAULT '',
    thickness   TEXT DEFAULT '',
    magnification TEXT DEFAULT '',
    photo_path  TEXT DEFAULT '',
    coord_x     REAL DEFAULT 0.0,
    coord_y     REAL DEFAULT 0.0,
    status      TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','used')),
    used_in_device TEXT DEFAULT NULL REFERENCES devices(device_id) ON DELETE SET NULL,
    notes       TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS projects (
    project_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    description TEXT DEFAULT '',
    fab_date    TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'planned'
                CHECK(status IN ('planned','fabricated','measured','retired')),
    fab_path    TEXT DEFAULT '',
    meas_path   TEXT DEFAULT '',
    meas_date   TEXT DEFAULT '',
    meas_notes  TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS device_layers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    layer_name  TEXT NOT NULL,
    flake_id    TEXT REFERENCES flakes(flake_id) ON DELETE SET NULL,
    order_index INTEGER DEFAULT 0
);
"""


def init_db():
    """Initialize the database schema and apply any pending migrations."""
    config.ensure_dirs()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate()


def _migrate():
    """Apply incremental schema migrations to existing databases.

    Each migration is idempotent: it checks whether the change is already
    present before attempting it.
    """
    # Migration: flakes.wafer_id must be ON DELETE SET NULL.
    #
    # Earlier versions shipped ON DELETE CASCADE (which wiped used-flake
    # provenance when a wafer was deleted).  The correct policy is:
    #   • application code explicitly deletes available flakes before
    #     removing a wafer;
    #   • used flakes survive with wafer_id=NULL so device_layers retains
    #     the material/thickness history.
    #
    # SQLite does not support ALTER COLUMN, so we rebuild the table.
    _CORRECT_POLICY = "ON DELETE SET NULL"
    _WRONG_POLICY   = "ON DELETE CASCADE"

    with get_conn() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='flakes'"
        ).fetchone()
        sql = (row.get("sql") or "") if row else ""
        # Only migrate when the wrong policy is currently in place.
        # (A brand-new DB created from SCHEMA will already have SET NULL.)
        if _WRONG_POLICY in sql and _CORRECT_POLICY not in sql:
            conn.executescript("""
                PRAGMA foreign_keys = OFF;

                CREATE TABLE flakes_new (
                    flake_id      TEXT PRIMARY KEY,
                    wafer_id      INTEGER REFERENCES wafers(wafer_id) ON DELETE SET NULL,
                    material      TEXT NOT NULL DEFAULT '',
                    thickness     TEXT DEFAULT '',
                    magnification TEXT DEFAULT '',
                    photo_path    TEXT DEFAULT '',
                    coord_x       REAL DEFAULT 0.0,
                    coord_y       REAL DEFAULT 0.0,
                    status        TEXT NOT NULL DEFAULT 'available'
                                  CHECK(status IN ('available','used')),
                    used_in_device TEXT DEFAULT NULL
                                  REFERENCES devices(device_id) ON DELETE SET NULL,
                    notes         TEXT DEFAULT '',
                    created_at    TEXT DEFAULT (datetime('now','localtime'))
                );

                INSERT INTO flakes_new SELECT * FROM flakes;

                DROP TABLE flakes;

                ALTER TABLE flakes_new RENAME TO flakes;

                PRAGMA foreign_keys = ON;
            """)


# ── Wafer Box CRUD ──────────────────────────────────────────────────────

def create_box(name: str, rows: int = 5, cols: int = 5, notes: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO wafer_boxes (name, rows, cols, notes) VALUES (?,?,?,?)",
            (name, rows, cols, notes))
        return cur.lastrowid


def get_all_boxes() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM wafer_boxes ORDER BY name").fetchall()


def update_box(box_id: int, **kwargs):
    allowed = {"name", "rows", "cols", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE wafer_boxes SET {sets} WHERE box_id=?",
                     (*fields.values(), box_id))


def delete_box(box_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM wafer_boxes WHERE box_id=?", (box_id,))


# ── Wafer CRUD ──────────────────────────────────────────────────────────

def get_or_create_wafer(box_id: int, row: int, col: int) -> dict:
    """Get wafer at position, creating it if needed."""
    with get_conn() as conn:
        w = conn.execute(
            "SELECT * FROM wafers WHERE box_id=? AND row=? AND col=?",
            (box_id, row, col)).fetchone()
        if w:
            return w
        conn.execute(
            "INSERT INTO wafers (box_id, row, col) VALUES (?,?,?)",
            (box_id, row, col))
        return conn.execute(
            "SELECT * FROM wafers WHERE box_id=? AND row=? AND col=?",
            (box_id, row, col)).fetchone()


def get_wafers_for_box(box_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM wafers WHERE box_id=? ORDER BY row, col",
            (box_id,)).fetchall()


def update_wafer(wafer_id: int, **kwargs):
    allowed = {"label", "ref_points", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE wafers SET {sets} WHERE wafer_id=?",
                     (*fields.values(), wafer_id))


def delete_wafer(wafer_id: int):
    """Delete a wafer and clean up its flakes.

    Policy:
      • 'available' flakes are deleted (they have no further use once the
        physical wafer is discarded).
      • 'used' flakes are preserved: their wafer_id is set to NULL by the
        ON DELETE SET NULL FK, but material/thickness/device linkage remain
        intact for device provenance.

    The wafer row itself is then deleted; the FK cascade handles
    wafer_boxes → wafers automatically when a box is removed.
    """
    with get_conn() as conn:
        # Explicitly remove available flakes first so they don't become orphans.
        conn.execute(
            "DELETE FROM flakes WHERE wafer_id=? AND status='available'",
            (wafer_id,))
        # Delete the wafer; ON DELETE SET NULL on flakes.wafer_id will NULL-out
        # any remaining (used) flake rows automatically.
        conn.execute("DELETE FROM wafers WHERE wafer_id=?", (wafer_id,))


def get_wafer_by_id(wafer_id: int) -> Optional[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM wafers WHERE wafer_id=?",
                            (wafer_id,)).fetchone()


# ── Flake CRUD ──────────────────────────────────────────────────────────

def create_flake(flake_id: str, wafer_id: int, material: str = "",
                 thickness: str = "", magnification: str = "",
                 photo_path: str = "", coord_x: float = 0.0,
                 coord_y: float = 0.0, notes: str = "") -> str:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO flakes
               (flake_id, wafer_id, material, thickness, magnification,
                photo_path, coord_x, coord_y, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (flake_id, wafer_id, material, thickness, magnification,
             photo_path, coord_x, coord_y, notes))
        return flake_id


def get_flakes_for_wafer(wafer_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM flakes WHERE wafer_id=? ORDER BY flake_id",
            (wafer_id,)).fetchall()


def get_flake(flake_id: str) -> Optional[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM flakes WHERE flake_id=?", (flake_id,)).fetchone()


def get_available_flakes(material_filter: str = "") -> list[dict]:
    with get_conn() as conn:
        if material_filter:
            return conn.execute(
                "SELECT * FROM flakes WHERE status='available' AND material LIKE ? ORDER BY flake_id",
                (f"%{material_filter}%",)).fetchall()
        return conn.execute(
            "SELECT * FROM flakes WHERE status='available' ORDER BY flake_id"
        ).fetchall()


def get_all_flakes() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM flakes ORDER BY flake_id").fetchall()


def update_flake(flake_id: str, **kwargs):
    allowed = {"wafer_id", "material", "thickness", "magnification",
               "photo_path", "coord_x", "coord_y", "status",
               "used_in_device", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE flakes SET {sets} WHERE flake_id=?",
                     (*fields.values(), flake_id))


def delete_flake(flake_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM flakes WHERE flake_id=?", (flake_id,))


# ── Project CRUD ────────────────────────────────────────────────────────

def create_project(project_id: str, name: str, description: str = "") -> str:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (project_id, name, description) VALUES (?,?,?)",
            (project_id, name, description))
        return project_id


def get_all_projects() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects ORDER BY name").fetchall()


def get_project(project_id: str) -> Optional[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM projects WHERE project_id=?",
            (project_id,)).fetchone()


def update_project(project_id: str, **kwargs):
    allowed = {"name", "description"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE projects SET {sets} WHERE project_id=?",
                     (*fields.values(), project_id))


def delete_project(project_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))


# ── Device CRUD ─────────────────────────────────────────────────────────

def create_device(device_id: str, project_id: str, description: str = "",
                  fab_date: str = "", status: str = "planned",
                  fab_path: str = "", meas_path: str = "",
                  notes: str = "") -> str:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO devices
               (device_id, project_id, description, fab_date, status,
                fab_path, meas_path, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (device_id, project_id, description, fab_date, status,
             fab_path, meas_path, notes))
        return device_id


def get_devices_for_project(project_id: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM devices WHERE project_id=? ORDER BY created_at",
            (project_id,)).fetchall()


def get_device(device_id: str) -> Optional[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM devices WHERE device_id=?",
            (device_id,)).fetchone()


def get_all_devices() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()


def update_device(device_id: str, **kwargs):
    allowed = {"project_id", "description", "fab_date", "status",
               "fab_path", "meas_path", "meas_date", "meas_notes", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE devices SET {sets} WHERE device_id=?",
                     (*fields.values(), device_id))


def delete_device(device_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM devices WHERE device_id=?", (device_id,))


# ── Device Layer CRUD ───────────────────────────────────────────────────

def add_device_layer(device_id: str, layer_name: str,
                     flake_id: str, order_index: int = 0) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO device_layers (device_id, layer_name, flake_id, order_index)
               VALUES (?,?,?,?)""",
            (device_id, layer_name, flake_id, order_index))
        return cur.lastrowid


def get_device_layers(device_id: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT dl.*, f.material, f.thickness
               FROM device_layers dl
               LEFT JOIN flakes f ON dl.flake_id = f.flake_id
               WHERE dl.device_id=? ORDER BY dl.order_index""",
            (device_id,)).fetchall()


def delete_device_layer(layer_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM device_layers WHERE id=?", (layer_id,))


# ── Queries ─────────────────────────────────────────────────────────────

def count_flakes_on_wafer(wafer_id: int) -> int:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) as cnt FROM flakes WHERE wafer_id=? AND status='available'",
            (wafer_id,)).fetchone()
        return r["cnt"] if r else 0


def get_wafer_flake_counts(box_id: int) -> dict:
    """Return {(row,col): count} for all wafers in a box."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT w.row, w.col, COUNT(f.flake_id) as cnt
               FROM wafers w
               LEFT JOIN flakes f ON w.wafer_id = f.wafer_id AND f.status='available'
               WHERE w.box_id=?
               GROUP BY w.row, w.col""",
            (box_id,)).fetchall()
        return {(r["row"], r["col"]): r["cnt"] for r in rows}


def get_project_device_summary(project_id: str) -> list[dict]:
    """Get devices with layer count for a project."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT d.*, COUNT(dl.id) as layer_count
               FROM devices d
               LEFT JOIN device_layers dl ON d.device_id = dl.device_id
               WHERE d.project_id=?
               GROUP BY d.device_id
               ORDER BY d.created_at""",
            (project_id,)).fetchall()
