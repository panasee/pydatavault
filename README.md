# PyDataVault

A PySide6 desktop GUI for managing van der Waals heterostructure fabrication and measurement data. Replaces manual file naming and scattered Excel tracking with a structured, database-backed workflow.

---

## Features

| | |
|---|---|
| **Wafer / Flake management** | Visual 5×5 grid per wafer box; multi-flake support per wafer position; per-wafer coordinate reference system (up to 3 reference photos + stage coordinates); built-in coordinate transformation tool |
| **Project / Device management** | Project-scoped device tracking; automatic fabrication and measurement folder creation on device registration; symlink from project tree to measurement data store; layer-by-layer flake consumption tracking |
| **pyflexlab integration** | Measurement folders initialised via `pyflexlab.FileOrganizer`, guaranteeing correct folder structure and notebook templates; `OUT_DB_PATH` resolved through pyflexlab's own environment logic |
| **Fully editable records** | Every field (dates, notes, status, coordinates, …) can be modified after the fact without affecting underlying data files |
| **SQLite backend** | Single `lab.db` file stores all metadata; the file system remains the authoritative store for raw data |

---

## Project structure

```
pydatavault/              ← project root
├── pyproject.toml
├── README.md
└── pydatavault/          ← Python package
    ├── __init__.py
    ├── __main__.py       ← entry point
    ├── config.py         ← path resolution
    ├── database.py       ← SQLite schema and CRUD
    ├── coord_utils.py    ← coordinate transformation
    ├── main_window.py
    ├── wafer_widget.py
    └── project_widget.py
```

The package is completely independent of the research data directory it manages.

---

## Environment variables

PyDataVault requires the following environment variables to be set before launch. There are no silent fallbacks — missing variables raise a `RuntimeError` on startup.

| Variable | Required | Owner | Purpose |
|---|:---:|---|---|
| `VAULT_DB_PATH` | ✅ | PyDataVault | Root directory for the project tree, shared flake library, and SQLite database |
| `PYLAB_DB_OUT` | ✅ | pyflexlab | Measurement data output directory. May live on a different drive or machine from `VAULT_DB_PATH`. Resolved via pyflexlab's own `set_envs()` logic, which also supports machine-specific variants such as `PYLAB_DB_OUT_<HOSTNAME>` |
| `PYLAB_DB_LOCAL` | ✅ | pyflexlab | Local configuration directory containing `measure_types.json` and notebook templates used by `pyflexlab.FileOrganizer` |
| `PYLAB_LOCAL_SPECIFIC` | ☑️ optional | pyflexlab | Machine-specific override for `PYLAB_DB_LOCAL`. When both `PYLAB_LOCAL_SPECIFIC` and `PYLAB_OUT_SPECIFIC` are set, pyflexlab uses them directly and skips `set_envs()` |
| `PYLAB_OUT_SPECIFIC` | ☑️ optional | pyflexlab | Machine-specific override for `PYLAB_DB_OUT` (see above) |

> `PYLAB_DB_OUT` and `VAULT_DB_PATH` are **completely independent** paths and should not be assumed to share any common parent directory.

---

## Data directory layout

`VAULT_DB_PATH` contains the PyDataVault-managed tree:

```
$VAULT_DB_PATH/
├── .labdb/
│   └── lab.db                  ← SQLite metadata database
├── projects/
│   └── <project-id>/
│       ├── fabrication/
│       │   └── <device-id>/    ← assembly photos, consumed flake data
│       ├── measurements/
│       │   └── <device-id> ──→ $PYLAB_DB_OUT/<device-id>   (symlink)
│       ├── analysis/
│       ├── reports/
│       └── cad/
├── shared/
│   └── flakes/                 ← flake photo library (pre-consumption)
├── templates/
└── archive/
```

`PYLAB_DB_OUT` is managed entirely by pyflexlab and contains actual measurement data:

```
$PYLAB_DB_OUT/
├── project_record.json
└── <device-id>/
    ├── assist_measure.ipynb
    ├── assist_post.ipynb
    └── <test-data>/
```

---

## Installation

```bash
pip install -e .
```

Requires Python ≥ 3.10. `pyflexlab` and `pyomnix` are listed as dependencies and must be installed (from source if not on PyPI).

---

## Usage

```bash
# Windows
set VAULT_DB_PATH=D:\path\to\data

# macOS / Linux
export VAULT_DB_PATH=/path/to/data

pydatavault          # via entry point
# or
python -m pydatavault
```

pyflexlab environment variables (`PYLAB_DB_OUT`, `PYLAB_DB_LOCAL`) should be configured in your shell profile or a `.env` file loaded before launch, following your existing pyflexlab setup.

---

## Device ID convention

```
{PROJECT}-{description}-{YYYYMM}

CISS-30degLH-tMoTe-202411
JJ-NbSeCSB3-202512
TNS-sample1-202506
```

The same ID is used for the fabrication folder, the measurement folder, and the database record, making the correspondence unambiguous across all three locations.

---

## Wafer coordinate system

Each wafer supports up to three reference points, each storing a microscope photo and its stage coordinates (x, y). When relocating a flake in a new session, select any two stored reference points, enter their current stage coordinates, and the tool computes the transformed flake position via complex-number rotation + scale + translation — matching the algorithm in `pyflexlab.auxiliary.Flakes.coor_transition`.
