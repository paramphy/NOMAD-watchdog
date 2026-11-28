# NOMAD Experiment Watcher --- Progress Diary

This diary records implementation milestones, design decisions, problems
encountered, fixes, and planned work. Add a new dated entry whenever a
meaningful implementation or testing milestone is completed.

## Project record

-   **Project:** `nomad-experiment-watcher`
-   **Purpose:** Monitor experimental data folders, generate measurement
    archives, and incrementally upload experimental data to NOMAD Oasis.
-   **Development environment:** Windows, Python, PowerShell.
-   **Current phase:** Phase 1 working; Phase 2 not started.
-   **Last recorded milestone:** 13 November 2026 --- automatic local
    archive generation confirmed working after the Windows `fsync()`
    fix.

## 1. Project concept and architecture

The watcher is a standalone Python application, independent of the
existing `nomad_combinatorial_thin_film` NOMAD plugin.

The intended workflow is:

1.  Discover experiment folders.
2.  Initialize and preserve experiment metadata.
3.  Detect measurement files.
4.  Wait for source files to become stable.
5.  Generate one archive per measurement type.
6.  Track local state in SQLite.
7.  Upload archives to one NOMAD upload per experiment.
8.  Reprocess updated data and reconcile measurement entries.
9.  Eventually create a parent experiment entry referencing the
    measurement entries.

The application runs on a computer with access to experimental data and
communicates with a separate remote NOMAD Oasis server over HTTPS.

## 2. Key design decisions

  -----------------------------------------------------------------------
  Topic                               Decision
  ----------------------------------- -----------------------------------
  Application architecture            Standalone Python application,
                                      separate from the NOMAD plugin

  Experiment identity                 Persistent UUID associated with
                                      each experiment

  Researcher metadata                 `experiment_details.json`;
                                      researcher values must be preserved

  Operational metadata                `experiment_manifest.json`

  Operational state                   SQLite is authoritative

  Archive granularity                 One archive per measurement type
                                      per experiment

  Archive location                    Outside `Experimental_Data`, in
                                      `archive_cache/`

  Original data                       Never modified or deleted by
                                      archive generation

  Archive replacement                 Generate and validate temporary
                                      archive before atomic replacement

  Late-arriving data                  Regenerate the relevant measurement
                                      archive after quiet/stability
                                      checks

  NOMAD upload grouping               One NOMAD upload per experiment

  Publication                         Keep uploads unpublished initially;
                                      publishing is manual

  Development style                   Implement and test in small steps
  -----------------------------------------------------------------------

### Archive filename mapping

  Measurement     Extension
  --------------- --------------
  RTP             `.rtpzip`
  XRD             `.xrdzip`
  Sulfurization   `.sulfurzip`
  Hyperspectral   `.hyperzip`

## 3. Implementation milestones

### Milestone 1 --- Project structure and configuration

**Status: Implemented in the Phase 1 development.**

The project uses a `src/nomad_experiment_watcher/` package layout, with
separate modules for configuration, database operations, experiment
management, metadata, archive management, and folder monitoring.

Configuration includes monitoring paths, scan behaviour, logging,
database location, and archive settings.

### Milestone 2 --- Experiment discovery and metadata

**Status: Implemented; further restart and edge-case tests remain.**

The watcher scans the experimental data root and detects immediate child
experiment directories.

The metadata manager works with two files:

-   `experiment_details.json`: researcher-owned scientific metadata.
-   `experiment_manifest.json`: watcher-owned operational metadata.

UUIDs are generated when needed and retained across initialization.
Conflicting UUID information is treated as an error rather than silently
overwritten.

### Milestone 3 --- SQLite database

**Status: Implemented.**

The database has two main tables:

-   `experiments`: experiment UUID, ID, local path, NOMAD upload ID,
    status, and timestamps.
-   `archive_state`: per-experiment/per-measurement archive path, source
    and generated fingerprints, checksum, file count, status, and
    timestamps.

The `WatcherDatabase` class provides connection management,
initialization, experiment registration/retrieval, and archive-state
methods.

### Milestone 4 --- Automatic measurement archive generation

**Status: Working.**

The archive manager:

-   Scans each configured measurement directory.
-   Excludes known temporary-file suffixes.
-   Collects source files and calculates a fingerprint.
-   Tracks changes in SQLite.
-   Waits for the quiet period and file-stability condition.
-   Creates a temporary ZIP archive.
-   Validates the archive.
-   Checks whether source files changed during packaging.
-   Replaces the destination archive after successful generation.
-   Records the checksum and generated fingerprint.

Original experimental files are preserved.

## 4. Debugging diary

### Issue 1 --- Missing archive-state database method

**Observed error:**

``` text
AttributeError:
'WatcherDatabase' object has no attribute 'get_archive_state'
```

**Cause:** The archive-state methods were not correctly included inside
the `WatcherDatabase` class.

**Resolution:** Rewrote `database.py` to include the `archive_state`
table and the required methods:

``` python
get_archive_state()
update_archive_state()
mark_archive_generated()
```

The table creation SQL was placed correctly inside
`connection.execute(...)`.

**Status:** Resolved.

### Issue 2 --- Windows bad file descriptor during `fsync`

**Observed error:**

``` text
OSError: [Errno 9] Bad file descriptor
```

**Location:** `archive_manager.py`, inside `generate_archive()`, at the
`os.fsync(file.fileno())` call.

**Cause:** The temporary ZIP file's file descriptor was not usable for
the attempted synchronization operation.

**Resolution:** Changed the file opening mode to `r+b` and made
`fsync()` failure non-fatal after the archive had been closed and
validated:

``` python
try:
    with open(temporary_path, "r+b") as file:
        file.flush()
        os.fsync(file.fileno())

except OSError as error:
    self.logger.warning(
        "Could not fsync temporary archive %s: %s",
        temporary_path,
        error
    )
```

**Status:** Resolved. The user confirmed that archive generation is
working.

## 5. Current implementation status

  -----------------------------------------------------------------------
  Component or capability Status                  Notes
  ----------------------- ----------------------- -----------------------
  Configuration loading   Implemented             Existing project
                                                  configuration

  Folder monitoring       Implemented             Watchdog and periodic
                                                  scanning

  Experiment discovery    Implemented             Immediate child
                                                  directories

  Metadata initialization Implemented             Researcher details
                                                  preserved

  UUID persistence        Implemented             Conflict checks
                                                  included

  SQLite experiment table Implemented             Local experiment state

  SQLite archive table    Implemented             Per-measurement archive
                                                  state

  File collection         Implemented             Temporary suffix
                                                  filtering

  Source fingerprint      Implemented             Currently
                                                  metadata-based

  Quiet-period logic      Implemented             Needs dedicated
                                                  edge-case tests

  File stability check    Implemented             Should be strengthened

  ZIP generation          Working                 Confirmed by user

  ZIP validation          Implemented             `testzip()` and source
                                                  re-check

  Atomic archive          Implemented             Uses `os.replace()`
  replacement                                     

  NOMAD authentication    Not started             Phase 2

  NOMAD upload creation   Not started             Phase 2

  Incremental upload      Not started             Must verify API
  replacement                                     behaviour

  NOMAD processing and    Not started             Phase 2
  entry retrieval                                 

  Parent experiment entry Not started             Later work
  -----------------------------------------------------------------------

## 6. Remaining Phase 1 validation

Before production use, test the following cases and record the outcomes
below:

-   [ ] New experiment directory is discovered.
-   [ ] Existing experiment is not registered twice.
-   [ ] UUID remains unchanged after watcher restart.
-   [ ] Researcher edits to `experiment_details.json` are preserved.
-   [ ] RTP archive is generated from multiple source files.
-   [ ] XRD archive is generated with the expected directory structure.
-   [ ] Sulfurization archive is generated.
-   [ ] Hyperspectral archive is generated with HDR/BIL and supporting
    files.
-   [ ] A late-arriving file causes the correct archive to be
    regenerated.
-   [ ] An actively written file is not packaged prematurely.
-   [ ] A source change during archive generation is detected.
-   [ ] A watcher restart resumes from existing SQLite state.
-   [ ] A valid existing archive is not regenerated unnecessarily.
-   [ ] A failed archive generation does not destroy the previous valid
    archive.
-   [ ] Large hyperspectral archives can be generated reliably.

### Robustness items to review

1.  **Stability detection:** strengthen the check by observing file size
    and modification time over a defined interval, rather than relying
    only on modification age.
2.  **Fingerprinting:** assess whether metadata-based fingerprints are
    sufficient or whether content hashing is needed for selected files
    or all files.
3.  **Deleted/empty sources:** define how the watcher should represent a
    measurement folder that becomes empty or loses files.
4.  **Restart recovery:** test interrupted generation and stale
    temporary ZIP cleanup.
5.  **Concurrency:** current design assumes one watcher process.
    Multi-process locking has not been implemented or tested.

## 7. Phase 2 plan --- NOMAD integration

**Status: Not started. Proceed slowly and test each step.**

### Phase 2.1 --- Verify the deployed NOMAD API and authentication

First task:

-   [ ] Confirm the remote NOMAD Oasis base URL.
-   [ ] Determine the deployed NOMAD version and available API
    endpoints.
-   [ ] Obtain/use a Personal Access Token securely.
-   [ ] Store the token outside source control, preferably in an
    environment variable or secret store.
-   [ ] Test one authenticated API request.
-   [ ] Confirm the response and record server/API details without
    logging the token.

Do not begin upload implementation until this test succeeds.

### Phase 2.2 --- Investigate upload behaviour

After authentication works:

-   [ ] Identify the upload creation endpoint.
-   [ ] Create a test upload.
-   [ ] Upload a small test file.
-   [ ] Determine how files are added to an existing upload.
-   [ ] Determine how an existing archive/file can be replaced.
-   [ ] Trigger processing.
-   [ ] Poll or query processing status.
-   [ ] Retrieve the generated entries and their identifiers.
-   [ ] Confirm how errors and retries should be handled.
-   [ ] Confirm unpublished-by-default behaviour.

All behaviour must be tested against the actual deployed NOMAD server.
Do not assume generic API documentation exactly matches the deployment.

### Phase 2.3 --- Implement a small NOMAD client

Planned package:

``` text
src/nomad_experiment_watcher/nomad/
├── __init__.py
├── client.py
├── uploader.py
└── processor.py
```

Responsibilities:

-   `client.py`: HTTP session, authentication, common API requests.
-   `uploader.py`: create upload and transfer/update files.
-   `processor.py`: trigger processing, inspect status, retrieve
    entries.

Keep NOMAD-specific code separate from the existing archive manager.

### Phase 2.4 --- Incremental upload workflow

Target behaviour:

-   One NOMAD upload per experiment.
-   Upload all available measurement archives to that upload.
-   Process the upload.
-   Retrieve and store measurement entry IDs.
-   When a measurement archive changes, update the same upload rather
    than creating a new upload.
-   Reprocess and reconcile the generated entries.
-   Preserve the unpublished state unless the user manually publishes.

The exact replacement/reprocessing strategy is not yet verified and must
be determined experimentally.

### Later --- Parent experiment entry

The intended final structure is a parent experiment entry referencing
the measurement entries generated by the existing plugin parsers.

This should be implemented only after the archive-to-parser processing
path is confirmed.

## 8. Phase 2 work log

Add a dated subsection here for each Phase 2 task. Include objective,
files inspected or changed, API endpoint or behaviour tested, test input
and output, result, decisions made, and remaining questions.

### Phase 2.1 --- Authentication test

**Status:** Not started.

-   **Server URL:** To be recorded.
-   **NOMAD version:** To be verified.
-   **Authenticated endpoint:** To be selected after inspecting the
    deployed API.
-   **Test result:** Pending.
-   **Notes:** Never record the PAT itself in this diary.

## 9. Development rules

-   Proceed in small steps; do not implement all of Phase 2 in one pass.
-   Inspect existing files before editing.
-   Avoid unnecessary rewrites of working modules.
-   Provide complete code for file replacements and clearly identify
    append-only changes.
-   Explain the purpose of each module and how to test it.
-   Preserve the existing SQLite database and records.
-   Do not commit secrets or PATs.
-   Verify deployed NOMAD behaviour before designing around it.
-   Keep original experimental data untouched.
-   Update this diary after meaningful milestones.

## 10. Latest handover state

**As of 13 November 2026:**

-   Phase 1 archive generation is working, confirmed by the user.
-   The `database.py` rewrite includes experiment and archive-state
    tracking.
-   The Windows `fsync()` issue has been addressed.
-   Phase 2 has not yet started.
-   The next action is to inspect the current configuration and
    establish a minimal authenticated connection to the remote NOMAD
    Oasis server.

### 17 November 2026 --- NOMAD configuration and manifest synchronisation

**Implemented in the current source revision:**

- NOMAD API base URL moved from `client.py` into `config/config.yaml`.
- PAT loaded from a local `.env` file through `python-dotenv`; `.env`
  remains excluded from Git. The PAT is not stored in YAML or logged.
- NOMAD upload creation moved to experiment discovery so each registered
  experiment gets one reusable upload ID before its first archive is ready.
- SQLite tracks experiment-level NOMAD upload status, last attempt,
  creation error, and processing status through additive migrations.
- `experiment_manifest.json` is synchronised from SQLite after discovery
  and after each periodic measurement scan. It records detection,
  source-file count, archive state, upload status, and upload timestamps.
- ArchiveManager reuses the upload ID created by ExperimentManager.
- Automatic publishing remains disabled.

**Important diagnostic from the supplied watcher log:** at 10:15 on
17 November, the watcher reported that `NOMAD_PAT` was not configured,
and explicitly disabled automatic uploads for that session. This was
the immediate cause of the no-upload behaviour in that run.

**Validation still required on the user's Windows machine:** install the
updated dependency, create the local `.env`, verify the configured
Cloudflare URL is still current, restart the watcher, and confirm that
the manifest receives an upload ID and that a test archive is uploaded.
