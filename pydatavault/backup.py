"""Backup creation for PyDataVault data roots."""

from __future__ import annotations

import json
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import zstandard

from . import config


def create_backup(destination_dir: str | Path, timestamp: str | None = None) -> Path:
    """Create a zstd-compressed tar backup of VAULT_DB_PATH and PYLAB_DB_OUT."""
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    vault_root = config.ROOT_PATH.resolve()
    pyflexlab_root = config.PYFLEXLAB_OUT_PATH.resolve()
    _ensure_destination_outside_sources(destination.resolve(), [vault_root, pyflexlab_root])

    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = destination / f"pydatavault-backup-{stamp}.tar.zst"

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "format": "tar.zst",
        "sources": {
            "vault": {
                "env": "VAULT_DB_PATH",
                "resolved_path": str(config.ROOT_PATH),
                "archive_root": "vault",
            },
            "pyflexlab_out": {
                "env": "PYLAB_DB_OUT",
                "resolved_path": str(config.PYFLEXLAB_OUT_PATH),
                "archive_root": "pyflexlab_out",
            },
        },
    }

    with tempfile.TemporaryDirectory(prefix="pydatavault-backup-") as tmp_name:
        tmp_dir = Path(tmp_name)
        db_snapshot = tmp_dir / "lab.db"
        _snapshot_sqlite_db(config.DB_FILE, db_snapshot)

        with archive_path.open("wb") as raw:
            compressor = zstandard.ZstdCompressor(level=10, threads=-1)
            with compressor.stream_writer(raw) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as tar:
                    _add_json(tar, "manifest.json", manifest)
                    _add_tree(tar, vault_root, "vault", skip_paths={config.DB_FILE.resolve()})
                    if db_snapshot.exists():
                        tar.add(db_snapshot, arcname="vault/.labdb/lab.db")
                    if pyflexlab_root.exists():
                        _add_tree(tar, pyflexlab_root, "pyflexlab_out")

    return archive_path


def _ensure_destination_outside_sources(destination: Path, source_roots: list[Path]):
    for source in source_roots:
        try:
            destination.relative_to(source)
        except ValueError:
            continue
        raise ValueError(
            "Backup destination must be outside VAULT_DB_PATH and PYLAB_DB_OUT"
        )


def _snapshot_sqlite_db(source: Path, destination: Path):
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(source))
    try:
        dst_conn = sqlite3.connect(str(destination))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _add_json(tar: tarfile.TarFile, arcname: str, payload: dict):
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    tar.addfile(info, fileobj=_BytesReader(data))


def _add_tree(
    tar: tarfile.TarFile,
    root: Path,
    arc_root: str,
    skip_paths: set[Path] | None = None,
):
    skip_paths = skip_paths or set()
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        resolved = path.resolve()
        if resolved in skip_paths:
            continue
        arcname = Path(arc_root) / path.relative_to(root)
        tar.add(path, arcname=str(arcname), recursive=False)


class _BytesReader:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk
