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
    material    TEXT DEFAULT '',
    -- Up to 3 reference points stored as JSON list:
    -- [{"photo_path":"...", "x":..., "y":...}, ...]
    ref_points  TEXT DEFAULT '[]',
    notes       TEXT DEFAULT '',
    UNIQUE(box_id, row, col)
);

CREATE TABLE IF NOT EXISTS flakes (
    flake_uid   INTEGER PRIMARY KEY AUTOINCREMENT,
    flake_id    TEXT NOT NULL,
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
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(wafer_id, flake_id)
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
    flake_uid   INTEGER REFERENCES flakes(flake_uid) ON DELETE SET NULL,
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
    with get_conn() as conn:
        wafer_columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(wafers)").fetchall()
        }
        flake_columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(flakes)").fetchall()
        }
        layer_columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(device_layers)").fetchall()
        }
        fk_rows = conn.execute("PRAGMA foreign_key_list(flakes)").fetchall()
        wafer_fk = next(
            (row for row in fk_rows if row.get("from") == "wafer_id"),
            None,
        )
        needs_flake_uid = "flake_uid" not in flake_columns
        needs_layer_uid = "flake_uid" not in layer_columns
        needs_wafer_policy = wafer_fk and wafer_fk.get("on_delete") != "SET NULL"

        if "material" not in wafer_columns:
            conn.execute("ALTER TABLE wafers ADD COLUMN material TEXT DEFAULT ''")

        if needs_flake_uid or needs_layer_uid or needs_wafer_policy:
            _rebuild_flake_schema(conn, flake_columns, layer_columns)
            return

        conn.execute("UPDATE flakes SET wafer_id=NULL WHERE status='used'")

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
    with get_conn() as conn:
        fk_rows = conn.execute("PRAGMA foreign_key_list(flakes)").fetchall()
        wafer_fk = next(
            (row for row in fk_rows if row.get("from") == "wafer_id"),
            None,
        )
        # Only migrate when the wafer_id policy is currently wrong.
        # (A brand-new DB created from SCHEMA will already have SET NULL.)
        if wafer_fk and wafer_fk.get("on_delete") != "SET NULL":
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


def _rebuild_flake_schema(conn, flake_columns: dict, layer_columns: dict):
    """Rebuild flakes/device_layers around internal flake_uid references."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("""
            CREATE TABLE flakes_new (
                flake_uid     INTEGER PRIMARY KEY AUTOINCREMENT,
                flake_id      TEXT NOT NULL,
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
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(wafer_id, flake_id)
            );

            CREATE TABLE device_layers_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                layer_name  TEXT NOT NULL,
                flake_uid   INTEGER REFERENCES flakes_new(flake_uid) ON DELETE SET NULL,
                order_index INTEGER DEFAULT 0
            );
        """)

        if "flake_uid" in flake_columns:
            conn.execute("""
                INSERT INTO flakes_new
                    (flake_uid, flake_id, wafer_id, material, thickness,
                     magnification, photo_path, coord_x, coord_y, status,
                     used_in_device, notes, created_at)
                SELECT flake_uid, flake_id, wafer_id, material, thickness,
                       magnification, photo_path, coord_x, coord_y, status,
                       used_in_device, notes, created_at
                FROM flakes
            """)
        else:
            conn.execute("""
                INSERT INTO flakes_new
                    (flake_id, wafer_id, material, thickness, magnification,
                     photo_path, coord_x, coord_y, status, used_in_device,
                     notes, created_at)
                SELECT flake_id, wafer_id, material, thickness, magnification,
                       photo_path, coord_x, coord_y, status, used_in_device,
                       notes, created_at
                FROM flakes
            """)

        if "flake_uid" in layer_columns:
            conn.execute("""
                INSERT INTO device_layers_new
                    (id, device_id, layer_name, flake_uid, order_index)
                SELECT id, device_id, layer_name, flake_uid, order_index
                FROM device_layers
            """)
        else:
            conn.execute("""
                INSERT INTO device_layers_new
                    (id, device_id, layer_name, flake_uid, order_index)
                SELECT dl.id, dl.device_id, dl.layer_name, f.flake_uid,
                       dl.order_index
                FROM device_layers dl
                LEFT JOIN flakes_new f ON dl.flake_id = f.flake_id
            """)

        conn.executescript("""
            DROP TABLE device_layers;
            DROP TABLE flakes;
            ALTER TABLE flakes_new RENAME TO flakes;
            ALTER TABLE device_layers_new RENAME TO device_layers;
        """)
        conn.execute("UPDATE flakes SET wafer_id=NULL WHERE status='used'")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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
        conn.execute(
            """DELETE FROM flakes
               WHERE status='available'
                 AND wafer_id IN (
                     SELECT wafer_id FROM wafers WHERE box_id=?
                 )""",
            (box_id,))
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
    allowed = {"label", "material", "ref_points", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE wafers SET {sets} WHERE wafer_id=?",
                     (*fields.values(), wafer_id))
        if "material" in fields:
            conn.execute(
                "UPDATE flakes SET material=? WHERE wafer_id=?",
                (fields["material"], wafer_id),
            )


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
                 coord_y: float = 0.0, notes: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO flakes
               (flake_id, wafer_id, material, thickness, magnification,
                photo_path, coord_x, coord_y, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (flake_id, wafer_id, material, thickness, magnification,
             photo_path, coord_x, coord_y, notes))
        return cur.lastrowid


def get_flakes_for_wafer(wafer_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM flakes WHERE wafer_id=? ORDER BY flake_id",
            (wafer_id,)).fetchall()


def get_flake(flake_uid: int) -> Optional[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM flakes WHERE flake_uid=?", (flake_uid,)).fetchone()


def get_available_flakes(material_filter: str = "") -> list[dict]:
    with get_conn() as conn:
        if material_filter:
            return conn.execute(
                """SELECT f.*, w.row AS wafer_row, w.col AS wafer_col,
                          w.label AS wafer_label, wb.name AS box_name
                   FROM flakes f
                   LEFT JOIN wafers w ON f.wafer_id = w.wafer_id
                   LEFT JOIN wafer_boxes wb ON w.box_id = wb.box_id
                   WHERE f.status='available' AND f.material LIKE ?
                   ORDER BY wb.name, w.row, w.col, f.flake_id""",
                (f"%{material_filter}%",)).fetchall()
        return conn.execute(
            """SELECT f.*, w.row AS wafer_row, w.col AS wafer_col,
                      w.label AS wafer_label, wb.name AS box_name
               FROM flakes f
               LEFT JOIN wafers w ON f.wafer_id = w.wafer_id
               LEFT JOIN wafer_boxes wb ON w.box_id = wb.box_id
               WHERE f.status='available'
               ORDER BY wb.name, w.row, w.col, f.flake_id"""
        ).fetchall()


def get_all_flakes() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM flakes ORDER BY flake_uid").fetchall()


def count_flakes() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM flakes").fetchone()
        return row["cnt"] if row else 0


def update_flake(flake_uid: int, **kwargs):
    allowed = {"wafer_id", "material", "thickness", "magnification",
               "photo_path", "coord_x", "coord_y", "status",
               "used_in_device", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE flakes SET {sets} WHERE flake_uid=?",
                     (*fields.values(), flake_uid))


def delete_flake(flake_uid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM flakes WHERE flake_uid=?", (flake_uid,))


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


def create_device_with_layers(device_id: str, project_id: str,
                              layers: list[dict],
                              description: str = "",
                              fab_date: str = "",
                              status: str = "planned",
                              fab_path: str = "",
                              meas_path: str = "",
                              notes: str = "") -> str:
    """Create a device and consume its flakes in one transaction."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO devices
               (device_id, project_id, description, fab_date, status,
                fab_path, meas_path, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (device_id, project_id, description, fab_date, status,
             fab_path, meas_path, notes))
        for order_index, layer in enumerate(layers):
            conn.execute(
                """INSERT INTO device_layers
                   (device_id, layer_name, flake_uid, order_index)
                   VALUES (?,?,?,?)""",
                (device_id, layer['layer_name'], layer['flake_uid'], order_index))
            conn.execute(
                """UPDATE flakes
                   SET wafer_id=NULL, status='used', used_in_device=?
                   WHERE flake_uid=?""",
                (device_id, layer['flake_uid']))
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


def count_devices() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM devices").fetchone()
        return row["cnt"] if row else 0


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
                     flake_uid: int, order_index: int = 0) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO device_layers (device_id, layer_name, flake_uid, order_index)
               VALUES (?,?,?,?)""",
            (device_id, layer_name, flake_uid, order_index))
        return cur.lastrowid


def get_device_layers(device_id: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT dl.*, f.flake_id, f.material, f.thickness
               FROM device_layers dl
               LEFT JOIN flakes f ON dl.flake_uid = f.flake_uid
               WHERE dl.device_id=? ORDER BY dl.order_index""",
            (device_id,)).fetchall()


def delete_device_layer(layer_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM device_layers WHERE id=?", (layer_id,))


def add_device_layers_and_mark_flakes(device_id: str, layers: list[dict],
                                      start_index: int = 0):
    """Append layers to a device and mark their flakes as used atomically."""
    if not layers:
        return
    with get_conn() as conn:
        for offset, layer in enumerate(layers):
            conn.execute(
                """INSERT INTO device_layers
                   (device_id, layer_name, flake_uid, order_index)
                   VALUES (?,?,?,?)""",
                (device_id, layer['layer_name'], layer['flake_uid'],
                 start_index + offset))
            conn.execute(
                """UPDATE flakes
                   SET wafer_id=NULL, status='used', used_in_device=?
                   WHERE flake_uid=?""",
                (device_id, layer['flake_uid']))


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
            """SELECT w.row, w.col, COUNT(f.flake_uid) as cnt
               FROM wafers w
               LEFT JOIN flakes f ON w.wafer_id = f.wafer_id AND f.status='available'
               WHERE w.box_id=?
               GROUP BY w.row, w.col""",
            (box_id,)).fetchall()
        return {(r["row"], r["col"]): r["cnt"] for r in rows}


def get_wafer_grid_summary(box_id: int) -> dict:
    """Return {(row,col): {count, material}} for all wafers in a box."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT w.row, w.col, w.material, COUNT(f.flake_uid) as cnt
               FROM wafers w
               LEFT JOIN flakes f ON w.wafer_id = f.wafer_id AND f.status='available'
               WHERE w.box_id=?
               GROUP BY w.wafer_id, w.row, w.col, w.material""",
            (box_id,)).fetchall()
        return {
            (r["row"], r["col"]): {
                "count": r["cnt"],
                "material": r.get("material") or "",
            }
            for r in rows
        }


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
