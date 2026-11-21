# NOMAD Experiment Watcher

A standalone Python application for monitoring experimental data
folders, managing experiment metadata, generating measurement-specific
archives, and incrementally transferring experimental data to a remote
NOMAD Oasis server.

> **Current milestone:** Local discovery, metadata management, SQLite
> tracking, archive generation, NOMAD upload creation, incremental file
> upload, remote verification, and manifest synchronisation are implemented.
> **Next milestone:** NOMAD processing, entry retrieval, and scientific
> parser validation. Development will proceed incrementally.

## 1. Project goals

The watcher is intended to:

1.  Monitor a configured experimental data directory.
2.  Detect new experiment folders.
3.  Initialize and preserve experiment metadata.
4.  Detect new or changed measurement files.
5.  Wait until files are stable before packaging them.
6.  Generate one archive per measurement type and experiment.
7.  Regenerate an archive when additional data arrives, without
    modifying original files.
8.  Maintain operational state in a local SQLite database.
9.  Upload archives incrementally to NOMAD, using one NOMAD upload per
    experiment.
10. Recover safely after watcher restarts and avoid unnecessary
    duplicate uploads.
11. Ultimately associate measurement entries with a parent experiment
    entry.

The watcher runs on a computer with access to experimental data. A
separate computer hosts the remote NOMAD Oasis instance. Communication
is intended to use HTTPS REST APIs.

## 2. Design principles

-   **Independent application:** the watcher is separate from the NOMAD
    plugin environment.
-   **Original data is preserved:** generated archives are stored
    outside the experimental data directory.
-   **Researcher metadata is preserved:** the watcher must not overwrite
    researcher-authored experiment details.
-   **SQLite is the operational state store:** the database tracks
    experiment and archive state.
-   **Incremental workflow:** new measurement data should update the
    corresponding archive and, eventually, the existing NOMAD upload.
-   **Validate before replacing:** a newly generated archive is
    validated before it replaces the previous archive.
-   **Unpublished by default:** uploads should initially remain
    unpublished; publishing is a manual action.
-   **Incremental development:** each phase is implemented and tested
    before proceeding.

## NOMAD connection and secret configuration

The NOMAD REST API base URL is configured in `config/config.yaml`:

```yaml
nomad:
  base_url: "https://YOUR-NOMAD-HOST/nomad-oasis/api/v1"
  env_file: ".env"
  token_env_var: "NOMAD_PAT"
```

The PAT must **not** be stored in `config.yaml` or committed to Git.
The local `.env` file is plain text (not encrypted), so protect it with
your Windows account permissions. For a managed deployment, use Windows
Credential Manager or another OS secret store instead.

For local development:

1. Copy `.env.example` to `.env` in the project root.
2. Put the PAT in `.env`:

   ```dotenv
   NOMAD_PAT=your_personal_access_token
   ```

3. Keep `.env` private. It is excluded by `.gitignore`.
4. Install updated dependencies with `pip install -e .`.
5. Restart the watcher.

The application loads `.env` at startup. A PAT already supplied through
the Windows environment takes precedence over the `.env` value. Do not
paste the PAT into source code, logs, chat, or a shared configuration file.

The `base_url` value must include the NOMAD API path
`/nomad-oasis/api/v1`. If a Cloudflare Quick Tunnel URL changes, update
this value in `config/config.yaml`.

When NOMAD credentials are unavailable, local discovery and archive
generation continue, but remote upload creation and file transfer are
disabled. After credentials are configured, restart the watcher.

## 3. Current project structure

The structure below reflects the architecture developed so far. Some
folders may only be created when their corresponding phase is
implemented.

``` text
nomad-experiment-watcher/
├── pyproject.toml
├── README.md
├── PROGRESS_DIARY.md
├── .gitignore
├── config/
│   └── config.yaml
├── database/
│   └── watcher.db
├── archive_cache/
│   └── <experiment_id>/
│       ├── <experiment_id>.rtpzip
│       ├── <experiment_id>.xrdzip
│       ├── <experiment_id>.sulfurzip
│       └── <experiment_id>.hyperzip
├── logs/
├── src/
│   └── nomad_experiment_watcher/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── database.py
│       ├── experiments/
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   ├── metadata.py
│       │   └── archive_manager.py
│       └── monitoring/
│           ├── __init__.py
│           └── folder_monitor.py
└── tests/
```

NOMAD integration is separated into its own package:

``` text
src/nomad_experiment_watcher/nomad/
├── __init__.py
├── client.py
├── uploader.py
└── processor.py
```

The client and uploader are implemented. Processing and entry-to-measurement
association remain future work.

## 4. Experimental data layout

Each experiment is represented by a directory. Measurement files are
placed in subdirectories named after their measurement type.

``` text
Experimental_Data/
├── Cu_186/
│   ├── experiment_details.json
│   ├── experiment_manifest.json
│   ├── RTP/
│   ├── XRD/
│   ├── Sulfurization/
│   └── Hyperspectral/
└── Cu_187/
    └── ...
```

### `experiment_details.json`

This is researcher-owned scientific metadata. It may contain fields such
as `experiment_id`, `experiment_uuid`, `sample_id`, `title`,
`description`, `researcher`, `material_system`, `substrate`,
`deposition_method`, `deposition_date`, and `measurements`.

The watcher may add missing defaults during initialization, but it must
preserve researcher-provided values.

### `experiment_manifest.json`

This is watcher-managed operational metadata. It is intended to track
the experiment UUID, expected measurement types, detected data, archive
status, upload status, processing status, and NOMAD entry identifiers.

The SQLite database is the authoritative operational state store; the
manifest is a convenient per-experiment record.

## 5. Archive generation

The current design creates one archive per measurement type per
experiment.

``` text
archive_cache/
└── Cu_186/
    ├── Cu_186.rtpzip
    ├── Cu_186.xrdzip
    ├── Cu_186.sulfurzip
    └── Cu_186.hyperzip
```

  Measurement folder   Archive extension
  -------------------- -------------------
  `RTP/`               `.rtpzip`
  `XRD/`               `.xrdzip`
  `Sulfurization/`     `.sulfurzip`
  `Hyperspectral/`     `.hyperzip`

The archive manager scans measurement directories, collects eligible
files, calculates a source fingerprint, observes quiet/stability
periods, creates a temporary ZIP, validates it, checks whether the
source changed during packaging, and replaces the destination archive
only after successful generation.

Original measurement files remain untouched. Relative paths inside each
measurement directory are preserved in the archive.

### Example archive configuration

``` yaml
archive:
  output_directory: "archive_cache"
  quiet_period_seconds: 300
  stability_period_seconds: 60
  compression_level: 6
  measurement_types:
    RTP: ".rtpzip"
    XRD: ".xrdzip"
    Sulfurization: ".sulfurzip"
    Hyperspectral: ".hyperzip"
```

The values are examples. Short intervals are useful for development
tests; longer intervals are recommended for real experiments.

## 6. Local database

The SQLite database currently contains two tables.

### `experiments`

Stores the experiment UUID, experiment ID, local directory path, NOMAD
upload ID, experiment status, and timestamps.

### `archive_state`

Stores one row per experiment and measurement type. Important fields
include:

-   `source_fingerprint`: fingerprint of the currently detected source
    files.
-   `generated_fingerprint`: fingerprint used for the last successfully
    generated archive.
-   `archive_checksum`: checksum of the generated archive.
-   `source_file_count`: number of detected source files.
-   `archive_status`: local archive generation state.
-   `last_source_change`: time of the last detected source change.
-   `last_generated`: time of the last successful archive generation.

The database module provides experiment registration/retrieval and
archive-state retrieval/update methods.

**Do not delete the SQLite database during normal development or
troubleshooting.** Back it up before schema migrations or manual
changes.

## 7. Existing NOMAD plugin and parser considerations

The separate `nomad_combinatorial_thin_film` plugin contains
parsers/schemas for several measurement types, including RTP, XRD,
sulfurization, and hyperspectral data.

The current parser expectations must be respected during NOMAD
integration. Based on the plugin inspection so far:

-   RTP parsing expects JSON data with fields such as `run_id`,
    `sample_id`, `time_series_data`, and `setpoints`.
-   Sulfurization parsing currently reads `.plw` files.
-   XRD parsing accepts ZIP-style archives containing supported
    ASC/XRDML data.
-   Hyperspectral parsing uses ENVI `.hdr` files and associated `.bil`
    data.
-   The experiment-plan parser handles `experiment_plan.txt`.

The custom archive extensions generated by the watcher do not, by
themselves, guarantee that NOMAD will parse their contents. Phase 2 must
verify the deployed NOMAD API and processing behaviour, and determine
whether archive layout or parser configuration needs adjustment.

## 8. Development status

### Phase 1 --- local watcher and archive generation

**Status: Working; further robustness testing remains.**

Implemented components include configuration loading, experiment folder
discovery, metadata initialization, UUID persistence and consistency
checks, SQLite state management, measurement folder scanning,
measurement-specific archive generation, ZIP integrity validation,
temporary archive generation and replacement, and archive state
recording.

Two issues were encountered and fixed:

1.  Missing archive-state methods in `WatcherDatabase` were addressed by
    rewriting `database.py` with the required methods and table
    creation.
2.  A Windows `fsync()` bad-file-descriptor error during archive
    generation was addressed by opening the temporary archive in `r+b`
    mode and treating an `fsync()` failure as a warning rather than
    aborting an otherwise validated archive.

### Phase 1 items that still need dedicated tests

-   Late-arriving files trigger regeneration correctly.
-   Files being actively written are not packaged prematurely.
-   Source changes during archive generation are detected.
-   A watcher restart resumes correctly from SQLite state.
-   Missing or deleted source files do not leave misleading archive
    state.
-   Large hyperspectral datasets can be packaged reliably.

The current source fingerprint is based on file metadata such as
relative path, size, and modification time. A content-based strategy may
be considered if stronger change detection is needed, taking the cost of
hashing large files into account.

### Phase 2 --- NOMAD communication and incremental upload

**Status: Implemented; live end-to-end operation still requires validation
with the current configured server tunnel and local PAT.**

The deployed NOMAD Oasis API has been tested for upload creation,
raw-file transfer, remote path replacement, and file-size verification.
The watcher now loads the API base URL from `config/config.yaml` and the
PAT from a local `.env` file.

Implemented:
- Configurable NOMAD API base URL and PAT secret loading.
- One reusable NOMAD upload per experiment, created at discovery.
- Incremental measurement archive uploads into measurement-specific
  remote directories.
- Remote file existence and size verification.
- Reuse of existing upload IDs after watcher restarts.
- SQLite-backed upload state and local manifest synchronisation.
- Publishing remains a manual action.

Remaining:
- Trigger and monitor NOMAD processing.
- Retrieve entry IDs and associate them with measurement types.
- Validate parser output against real experiment archives.

The PAT must not be committed to source control or stored in
`config.yaml`. The local `.env` file is a plain-text development secret;
protect it with Windows file permissions. Use an OS secret store for a
managed deployment.

The intended policy is to keep uploads unpublished initially. Publishing
remains a manual action.

### Later phase --- parent experiment entry

The eventual goal is to create a parent combinatorial thin-film
experiment entry that references the measurement entries generated by
the existing NOMAD parsers.

The exact schema and reference implementation should be designed after
the upload and processing workflow is verified.

## 9. Proposed NOMAD integration architecture

NOMAD-specific functionality should remain separate from the local
watcher and archive manager.

  -----------------------------------------------------------------------
  Module                              Planned responsibility
  ----------------------------------- -----------------------------------
  `nomad/client.py`                   HTTP session, authentication,
                                      common API requests

  `nomad/uploader.py`                 Create upload, transfer archive
                                      files, update existing upload

  `nomad/processor.py`                Trigger processing, inspect status,
                                      retrieve entries
  -----------------------------------------------------------------------

The intended high-level workflow is:

``` text
Experiment discovered
        |
        v
Create one NOMAD upload for the experiment
        |
        v
Upload measurement archives
        |
        v
Trigger NOMAD processing
        |
        v
Check processing result
        |
        v
Retrieve measurement entry IDs
        |
        v
Save upload and entry information locally
        |
        v
Wait for additional experimental data
        |
        v
Regenerate changed measurement archive
        |
        v
Update the same NOMAD upload
        |
        v
Process again and reconcile entries
```

This is the target workflow, not yet a verified description of the
deployed NOMAD API. In particular, replacing an archive in an existing
upload and reprocessing it must be tested against the actual server
version before implementation is finalized.

## 10. Development environment

The project has been developed on Windows using PowerShell and Python.
Python 3.11 or newer is the target.

The project uses a `src/` layout. If `pyproject.toml` is configured for
editable installation, install from the project root:

``` powershell
python -m pip install -e .
```

Use the startup command already defined by the project. Do not assume a
command name or entry point until it has been confirmed in
`pyproject.toml`.

## 11. Development workflow

Continue development in small, testable steps:

1.  Inspect the current implementation before editing.
2.  Change only the modules required for the current task.
3.  State clearly whether a file should be replaced or code appended.
4.  Test the change locally before moving on.
5.  Preserve existing SQLite state.
6.  Keep secrets out of Git.
7.  Verify API behaviour against the actual NOMAD Oasis deployment.
8.  Record important decisions and test results in `PROGRESS_DIARY.md`.

## 12. Immediate next step

Begin Phase 2 with a minimal NOMAD connection/authentication test:

-   Identify the exact NOMAD Oasis base URL.
-   Determine the deployed NOMAD version/API details.
-   Read the PAT securely from an environment variable or secret store.
-   Make one authenticated request to a suitable endpoint.
-   Report the HTTP status and a safe summary of the response without
    printing the token.

Do not implement upload creation or archive replacement until this
connection test succeeds.
