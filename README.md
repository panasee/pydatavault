# PyDataVault

A PySide6 desktop GUI for managing van der Waals heterostructure fabrication and measurement data. Replaces manual file naming and scattered Excel tracking with a structured, database-backed workflow.

---

## Features

| | |
|---|---|
| **Wafer / Flake management** | Visual 5×5 grid per wafer box; multi-flake support per wafer position; per-wafer coordinate reference system (up to 3 reference photos + stage coordinates); built-in coordinate transformation tool |
| **Project / Device management** | Project-scoped device tracking; automatic fabrication and measurement folder creation on device registration; symlink from project tree to measurement data store; layer-by-layer flake consumption tracking |
| **pyflexlab integration** | Measurement folders initialised via `pyflexlab.FileOrganizer`, guaranteeing correct folder structure and notebook templates; `OUT_DB_PATH` resolved through pyflexlab's own environment logic |
| **Flake layer analyzer** | Standalone microscope-image utility under **Tools** with a material selector, persistent material/substrate calibration database, zoom/pan/cropping, and per-photo substrate normalization |
| **Flake photo library** | **Tools → Flake Photo Library** provides manual substrate/flake polygons, substrate-relative normalization and layer/thickness estimates, plus labeled photo storage; the planned motion-control integration is documented in the [storage API contract](docs/flake_storage_api.md) |
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
│   ├── lab.db                  ← SQLite metadata database
│   ├── flake_layer_calibrations.db ← independent optical calibration database
│   └── flake_photo_library.db      ← photo library, region masks and thickness labels
├── projects/
│   └── <project-id>/
│       ├── fabrication/
│       │   └── <device-id>/    ← assembly photos, consumed flake data
│       ├── measurements/
│       │   └── <device-id> ──→ $PYLAB_DB_OUT/<device-id>   (symlink; junction fallback on Windows)
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
```

The same ID is used for the fabrication folder, the measurement folder, and the database record, making the correspondence unambiguous across all three locations.

---

## Wafer coordinate system

Each wafer supports up to three reference points, each storing a microscope photo and its original stage coordinates (x, y). Those stored reference and flake values form a coordinate chart attached to the wafer. When relocating a flake, enter the current absolute stage coordinates of any two or three references; the tool fits one rigid rotation + translation and returns the flake's absolute target-stage X/Y. Three references provide a least-squares fit with visible residuals. The diagram shows only the wafer-attached coordinates in the fixed-microscope orientation (X left, Y up); this drawing sign reversal never changes the returned stage coordinates.

## Flake photo library and motion-control

Open **Tools → Flake Photo Library…** and create a profile for a material, substrate
(including oxide thickness), and optical setup. Import a photo and draw polygons
for clean substrate and each flake region, then choose **Estimate thickness**.
No automatic edge detection or color segmentation runs here. Unknown photos do
not need thickness labels or a database write. Regions can be redrawn, removed
and restored with Undo. To add reference data, enter measured layers and/or nm
plus their source, then save. Double-click a saved photo to revise its selections.

After saving references, **Train / update profile** fits material/substrate/optics-specific
parameters, automatically compares RGB channel combinations, and supports either
multiple thicknesses or one target thickness. Use the same training group for repeat
photos of one flake; each thickness requires at least three usable independent groups.
A single-target result means target similarity, not exclusion of every other thickness.
Training creates versioned JSON parameters; edits to references require retraining.
See [training and parameter API](docs/flake_training.md) for the model, diagnostics,
and implemented `GET /v1/profiles/{profile_id}/model` / `GET /v1/models/{model_id}` endpoints.

The independent `.labdb/flake_photo_library.db` contains original image bytes,
full-resolution masks, normalized floating-point images, features and labels in
one transaction. Existing point-sampling calibrations remain available in the
older analyzer; they are not silently converted into photo references. Backups
snapshot all three SQLite databases, including the complete photo library.

For future motion-control integration, start with the
[Flake storage API contract](docs/flake_storage_api.md). The agreed architecture
puts automatic flake processing and inference in the microscope application;
the external PyDataVault service provides sample/resource persistence and queries.
The local manual-selection tool remains a separate user convenience.
The document defines endpoints, payloads, image coordinates, processing versions,
concurrent edits, retries and reference caching. **The target `/api/v1` storage
API and thin client are not implemented yet.**

[The manual-selection guide](docs/flake_photo_library.md) documents the local
Python interface and retained `/v1` analysis prototype (default port 51237).
These are separate from the planned storage contract. Normalization compensates
multiplicative brightness and RGB channel gains relative to the selected substrate;
it cannot remove every camera or illumination difference. Real thickness accuracy
still requires independent microscope photos with measured labels.
