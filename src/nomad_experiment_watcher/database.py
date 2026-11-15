from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    """Return the current UTC time as an ISO-formatted string."""
    return datetime.now(timezone.utc).isoformat()


class WatcherDatabase:
    """
    SQLite database for the NOMAD experiment watcher.

    Stores:
      - Registered experiments and their NOMAD upload IDs.
      - Archive generation state for each measurement type.
      - NOMAD upload state for each generated archive.

    Existing databases are migrated in place. Existing records are
    not deleted or replaced during initialization.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.initialize_database()

    # ------------------------------------------------------------------
    # CONNECTION MANAGEMENT
    # ------------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")

        return connection

    @contextmanager
    def connection(self):
        """
        Open a connection and commit on success.

        Roll back the transaction if an exception occurs.
        Always close the connection.
        """
        connection = self.connect()

        try:
            yield connection
            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    # ------------------------------------------------------------------
    # SCHEMA HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _table_columns(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> set[str]:
        """Return the column names for an existing SQLite table."""
        rows = connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()

        return {row["name"] for row in rows}

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        """Add a column only when it does not already exist."""
        columns = WatcherDatabase._table_columns(
            connection,
            table_name,
        )

        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column_name} {column_definition}"
            )

    # ------------------------------------------------------------------
    # DATABASE INITIALIZATION AND MIGRATIONS
    # ------------------------------------------------------------------

    def initialize_database(self) -> None:
        """
        Create missing tables and apply safe schema migrations.

        This method does not drop tables or delete existing rows.
        """
        with self.connection() as connection:

            connection.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_uuid TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    local_path TEXT NOT NULL UNIQUE,
                    nomad_upload_id TEXT,
                    experiment_status TEXT NOT NULL DEFAULT 'DISCOVERED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS archive_state (
                    experiment_uuid TEXT NOT NULL,
                    measurement_type TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    source_fingerprint TEXT,
                    generated_fingerprint TEXT,
                    archive_checksum TEXT,
                    source_file_count INTEGER DEFAULT 0,
                    archive_status TEXT NOT NULL DEFAULT 'NOT_CREATED',
                    last_source_change REAL,
                    last_generated TEXT,
                    updated_at TEXT NOT NULL,

                    PRIMARY KEY (
                        experiment_uuid,
                        measurement_type
                    ),

                    FOREIGN KEY (experiment_uuid)
                        REFERENCES experiments(experiment_uuid)
                        ON DELETE CASCADE
                )
            """)

            # Older database versions may not contain every column.
            # These migrations add missing columns without touching rows.

            self._add_column_if_missing(
                connection,
                "experiments",
                "nomad_upload_id",
                "TEXT",
            )

            self._add_column_if_missing(
                connection,
                "experiments",
                "experiment_status",
                "TEXT NOT NULL DEFAULT 'DISCOVERED'",
            )

            self._add_column_if_missing(
                connection,
                "archive_state",
                "upload_status",
                "TEXT NOT NULL DEFAULT 'NOT_UPLOADED'",
            )

            self._add_column_if_missing(
                connection,
                "archive_state",
                "uploaded_checksum",
                "TEXT",
            )

            self._add_column_if_missing(
                connection,
                "archive_state",
                "upload_error",
                "TEXT",
            )

            self._add_column_if_missing(
                connection,
                "archive_state",
                "last_uploaded",
                "TEXT",
            )

            # Indexes.
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_experiment_id
                ON experiments(experiment_id)
            """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_archive_status
                ON archive_state(archive_status)
            """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_upload_status
                ON archive_state(upload_status)
            """)

    # ------------------------------------------------------------------
    # EXPERIMENT MANAGEMENT
    # ------------------------------------------------------------------

    def register_experiment(
        self,
        experiment_uuid: str,
        experiment_id: str,
        local_path: str | Path,
    ) -> bool:
        """
        Register an experiment.

        Returns:
            True  - newly registered.
            False - already registered with matching identity.

        Raises:
            ValueError - if the path is already registered with a
                         different UUID or experiment ID.
        """
        now = utc_now()
        resolved_path = str(Path(local_path).resolve())

        with self.connection() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE local_path = ?
                """,
                (resolved_path,),
            ).fetchone()

            if existing:
                if existing["experiment_uuid"] != experiment_uuid:
                    raise ValueError(
                        f"Experiment UUID mismatch for {resolved_path}"
                    )

                if existing["experiment_id"] != experiment_id:
                    raise ValueError(
                        f"Experiment ID mismatch for {resolved_path}"
                    )

                return False

            connection.execute(
                """
                INSERT INTO experiments (
                    experiment_uuid,
                    experiment_id,
                    local_path,
                    nomad_upload_id,
                    experiment_status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, NULL, 'DISCOVERED', ?, ?)
                """,
                (
                    experiment_uuid,
                    experiment_id,
                    resolved_path,
                    now,
                    now,
                ),
            )

        return True

    def get_experiment(self, experiment_uuid: str) -> dict | None:
        """Retrieve an experiment by UUID."""
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE experiment_uuid = ?
                """,
                (experiment_uuid,),
            ).fetchone()

        return dict(row) if row else None

    def get_experiment_by_path(
        self,
        local_path: str | Path,
    ) -> dict | None:
        """Retrieve an experiment by its local directory."""
        resolved_path = str(Path(local_path).resolve())

        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE local_path = ?
                """,
                (resolved_path,),
            ).fetchone()

        return dict(row) if row else None

    def get_all_experiments(self) -> list[dict]:
        """Return all registered experiments."""
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM experiments
                ORDER BY created_at
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def set_nomad_upload_id(
        self,
        experiment_uuid: str,
        upload_id: str,
    ) -> None:
        """
        Store the NOMAD upload ID for an experiment.

        The same upload ID may be stored again. A different existing
        upload ID is rejected to avoid silently switching uploads.
        """
        if not upload_id or not str(upload_id).strip():
            raise ValueError("upload_id must be a non-empty string.")

        now = utc_now()

        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT nomad_upload_id
                FROM experiments
                WHERE experiment_uuid = ?
                """,
                (experiment_uuid,),
            ).fetchone()

            if row is None:
                raise ValueError(
                    f"Experiment not found: {experiment_uuid}"
                )

            existing_id = row["nomad_upload_id"]

            if existing_id and existing_id != upload_id:
                raise ValueError(
                    "This experiment already has a different NOMAD "
                    f"upload ID stored: {existing_id}"
                )

            connection.execute(
                """
                UPDATE experiments
                SET nomad_upload_id = ?,
                    updated_at = ?
                WHERE experiment_uuid = ?
                """,
                (upload_id, now, experiment_uuid),
            )

    # ------------------------------------------------------------------
    # ARCHIVE STATE MANAGEMENT
    # ------------------------------------------------------------------

    def get_archive_state(
        self,
        experiment_uuid: str,
        measurement_type: str,
    ) -> dict | None:
        """Retrieve the state of one measurement archive."""
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM archive_state
                WHERE experiment_uuid = ?
                  AND measurement_type = ?
                """,
                (experiment_uuid, measurement_type),
            ).fetchone()

        return dict(row) if row else None

    def update_archive_state(
        self,
        experiment_uuid: str,
        measurement_type: str,
        archive_path: str | Path,
        source_fingerprint: str,
        archive_status: str,
        last_source_change: float,
        source_file_count: int,
    ) -> None:
        """
        Insert or update source-side archive state.

        When the source fingerprint changes, the previous remote upload
        no longer represents the current source data. Therefore its
        upload status is reset to NOT_UPLOADED.

        The previous generated archive checksum/fingerprint are retained
        until a new archive is successfully generated.
        """
        now = utc_now()
        resolved_archive_path = str(Path(archive_path).resolve())

        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO archive_state (
                    experiment_uuid,
                    measurement_type,
                    archive_path,
                    source_fingerprint,
                    generated_fingerprint,
                    archive_checksum,
                    source_file_count,
                    archive_status,
                    last_source_change,
                    last_generated,
                    updated_at,
                    upload_status,
                    uploaded_checksum,
                    upload_error,
                    last_uploaded
                )
                VALUES (
                    ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?,
                    'NOT_UPLOADED', NULL, NULL, NULL
                )
                ON CONFLICT(experiment_uuid, measurement_type)
                DO UPDATE SET
                    archive_path = excluded.archive_path,

                    upload_status = CASE
                        WHEN archive_state.source_fingerprint
                             IS NOT excluded.source_fingerprint
                        THEN 'NOT_UPLOADED'
                        ELSE archive_state.upload_status
                    END,

                    uploaded_checksum = CASE
                        WHEN archive_state.source_fingerprint
                             IS NOT excluded.source_fingerprint
                        THEN NULL
                        ELSE archive_state.uploaded_checksum
                    END,

                    upload_error = CASE
                        WHEN archive_state.source_fingerprint
                             IS NOT excluded.source_fingerprint
                        THEN NULL
                        ELSE archive_state.upload_error
                    END,

                    source_fingerprint = excluded.source_fingerprint,
                    source_file_count = excluded.source_file_count,
                    archive_status = excluded.archive_status,
                    last_source_change = excluded.last_source_change,
                    updated_at = excluded.updated_at
                """,
                (
                    experiment_uuid,
                    measurement_type,
                    resolved_archive_path,
                    source_fingerprint,
                    source_file_count,
                    archive_status,
                    last_source_change,
                    now,
                ),
            )

    def mark_archive_generated(
        self,
        experiment_uuid: str,
        measurement_type: str,
        archive_checksum: str,
        generated_fingerprint: str,
    ) -> None:
        """
        Record successful archive generation.

        If the newly generated ZIP has a different checksum from the
        previous ZIP, its upload status is reset.
        """
        now = utc_now()

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE archive_state
                SET
                    upload_status = CASE
                        WHEN archive_checksum IS NOT ?
                        THEN 'NOT_UPLOADED'
                        ELSE upload_status
                    END,

                    uploaded_checksum = CASE
                        WHEN archive_checksum IS NOT ?
                        THEN NULL
                        ELSE uploaded_checksum
                    END,

                    upload_error = CASE
                        WHEN archive_checksum IS NOT ?
                        THEN NULL
                        ELSE upload_error
                    END,

                    archive_checksum = ?,
                    generated_fingerprint = ?,
                    archive_status = 'GENERATED',
                    last_generated = ?,
                    updated_at = ?
                WHERE experiment_uuid = ?
                  AND measurement_type = ?
                """,
                (
                    archive_checksum,
                    archive_checksum,
                    archive_checksum,
                    archive_checksum,
                    generated_fingerprint,
                    now,
                    now,
                    experiment_uuid,
                    measurement_type,
                ),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot mark archive as generated: no archive "
                    f"state exists for {experiment_uuid} / "
                    f"{measurement_type}"
                )

    # ------------------------------------------------------------------
    # NOMAD UPLOAD STATE MANAGEMENT
    # ------------------------------------------------------------------

    def mark_archive_upload_started(
        self,
        experiment_uuid: str,
        measurement_type: str,
    ) -> None:
        """Mark an archive as currently being uploaded."""
        now = utc_now()

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE archive_state
                SET
                    upload_status = 'UPLOADING',
                    upload_error = NULL,
                    updated_at = ?
                WHERE experiment_uuid = ?
                  AND measurement_type = ?
                """,
                (now, experiment_uuid, measurement_type),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot mark upload as started: archive state "
                    f"not found for {experiment_uuid} / "
                    f"{measurement_type}"
                )

    def mark_archive_uploaded(
        self,
        experiment_uuid: str,
        measurement_type: str,
        uploaded_checksum: str,
    ) -> None:
        """Mark an archive as uploaded and remotely verified."""
        now = utc_now()

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE archive_state
                SET
                    upload_status = 'UPLOADED',
                    uploaded_checksum = ?,
                    upload_error = NULL,
                    last_uploaded = ?,
                    updated_at = ?
                WHERE experiment_uuid = ?
                  AND measurement_type = ?
                """,
                (
                    uploaded_checksum,
                    now,
                    now,
                    experiment_uuid,
                    measurement_type,
                ),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot mark archive as uploaded: archive state "
                    f"not found for {experiment_uuid} / "
                    f"{measurement_type}"
                )

    def mark_archive_upload_failed(
        self,
        experiment_uuid: str,
        measurement_type: str,
        error_message: str,
    ) -> None:
        """Record a failed upload attempt."""
        now = utc_now()

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE archive_state
                SET
                    upload_status = 'FAILED',
                    upload_error = ?,
                    updated_at = ?
                WHERE experiment_uuid = ?
                  AND measurement_type = ?
                """,
                (
                    str(error_message)[:4000],
                    now,
                    experiment_uuid,
                    measurement_type,
                ),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot mark archive upload as failed: archive "
                    f"state not found for {experiment_uuid} / "
                    f"{measurement_type}"
                )