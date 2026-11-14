
import sqlite3

from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


def utc_now():
    """Return the current UTC time as an ISO-formatted string."""
    return datetime.now(timezone.utc).isoformat()


class WatcherDatabase:
    """
    Manages the local SQLite database used by the experiment watcher.

    The database stores:
    1. Experiment registration and NOMAD upload information.
    2. Archive generation state for each measurement type.
    3. NOMAD upload status for each generated archive.
    """

    def __init__(self, database_path: str | Path):

        self.database_path = Path(database_path)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.initialize_database()

    # ---------------------------------------------------------
    # DATABASE CONNECTION
    # ---------------------------------------------------------

    def connect(self):
        """Create and return a SQLite connection."""

        connection = sqlite3.connect(
            self.database_path,
            timeout=30
        )

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        return connection

    @contextmanager
    def connection(self):
        """
        Context manager for database connections.

        Automatically commits successful transactions,
        rolls back failed transactions and closes connections.
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

    # ---------------------------------------------------------
    # DATABASE INITIALIZATION
    # ---------------------------------------------------------

    def initialize_database(self):
        """
        Create database tables and indexes if they do not exist.

        Also performs safe schema migrations for existing databases.
        Existing experiment and archive records are preserved.
        """

        with self.connection() as connection:

            # ---------------------------------------------
            # EXPERIMENTS TABLE
            # ---------------------------------------------

            connection.execute("""
                CREATE TABLE IF NOT EXISTS experiments (

                    experiment_uuid TEXT PRIMARY KEY,

                    experiment_id TEXT NOT NULL,

                    local_path TEXT NOT NULL UNIQUE,

                    nomad_upload_id TEXT,

                    experiment_status TEXT NOT NULL
                        DEFAULT 'DISCOVERED',

                    created_at TEXT NOT NULL,

                    updated_at TEXT NOT NULL
                )
            """)

            # ---------------------------------------------
            # ARCHIVE STATE TABLE
            # ---------------------------------------------

            connection.execute("""
                CREATE TABLE IF NOT EXISTS archive_state (

                    experiment_uuid TEXT NOT NULL,

                    measurement_type TEXT NOT NULL,

                    archive_path TEXT NOT NULL,

                    source_fingerprint TEXT,

                    generated_fingerprint TEXT,

                    archive_checksum TEXT,

                    source_file_count INTEGER DEFAULT 0,

                    archive_status TEXT NOT NULL
                        DEFAULT 'NOT_CREATED',

                    last_source_change REAL,

                    last_generated TEXT,

                    updated_at TEXT NOT NULL,

                    upload_status TEXT NOT NULL
                        DEFAULT 'NOT_UPLOADED',

                    uploaded_checksum TEXT,

                    upload_error TEXT,

                    last_uploaded TEXT,

                    PRIMARY KEY (
                        experiment_uuid,
                        measurement_type
                    ),

                    FOREIGN KEY (experiment_uuid)
                        REFERENCES experiments(experiment_uuid)
                        ON DELETE CASCADE
                )
            """)

            # ---------------------------------------------
            # DATABASE MIGRATION
            # ---------------------------------------------
            # Add upload-tracking columns to databases
            # created before NOMAD upload integration.
            #
            # This does not delete or reset existing records.
            # ---------------------------------------------

            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(archive_state)"
                ).fetchall()
            }

            migrations = {

                "upload_status": (
                    "ALTER TABLE archive_state "
                    "ADD COLUMN upload_status TEXT "
                    "NOT NULL DEFAULT 'NOT_UPLOADED'"
                ),

                "uploaded_checksum": (
                    "ALTER TABLE archive_state "
                    "ADD COLUMN uploaded_checksum TEXT"
                ),

                "upload_error": (
                    "ALTER TABLE archive_state "
                    "ADD COLUMN upload_error TEXT"
                ),

                "last_uploaded": (
                    "ALTER TABLE archive_state "
                    "ADD COLUMN last_uploaded TEXT"
                ),
            }

            for column_name, migration_sql in migrations.items():

                if column_name not in existing_columns:
                    connection.execute(migration_sql)

            # ---------------------------------------------
            # INDEXES
            # ---------------------------------------------

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

    # ---------------------------------------------------------
    # EXPERIMENT MANAGEMENT
    # ---------------------------------------------------------

    def register_experiment(
        self,
        experiment_uuid,
        experiment_id,
        local_path
    ):
        """
        Register a newly discovered experiment.

        Returns:
            True  - experiment was newly registered.
            False - experiment was already registered.

        Raises:
            ValueError - if an existing experiment has conflicting
                         UUID or experiment ID information.
        """

        now = utc_now()

        local_path = str(Path(local_path).resolve())

        with self.connection() as connection:

            existing = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE local_path = ?
                """,
                (local_path,)
            ).fetchone()

            if existing:

                if existing["experiment_uuid"] != experiment_uuid:
                    raise ValueError(
                        f"Experiment UUID mismatch: {local_path}"
                    )

                if existing["experiment_id"] != experiment_id:
                    raise ValueError(
                        f"Experiment ID mismatch: {local_path}"
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
                    local_path,
                    now,
                    now
                )
            )

        return True

    def get_experiment(self, experiment_uuid):
        """Retrieve an experiment using its UUID."""

        with self.connection() as connection:

            result = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE experiment_uuid = ?
                """,
                (experiment_uuid,)
            ).fetchone()

        return dict(result) if result else None

    def get_experiment_by_path(self, local_path):
        """Retrieve an experiment using its local directory."""

        local_path = str(Path(local_path).resolve())

        with self.connection() as connection:

            result = connection.execute(
                """
                SELECT *
                FROM experiments
                WHERE local_path = ?
                """,
                (local_path,)
            ).fetchone()

        return dict(result) if result else None

    def get_all_experiments(self):
        """Return all registered experiments."""

        with self.connection() as connection:

            results = connection.execute(
                """
                SELECT *
                FROM experiments
                ORDER BY created_at
                """
            ).fetchall()

        return [dict(row) for row in results]

    # ---------------------------------------------------------
    # ARCHIVE STATE MANAGEMENT
    # ---------------------------------------------------------

    def get_archive_state(
        self,
        experiment_uuid,
        measurement_type
    ):
        """Retrieve archive state for an experiment and measurement."""

        with self.connection() as connection:

            result = connection.execute(
                """
                SELECT *
                FROM archive_state
                WHERE experiment_uuid = ?
                AND measurement_type = ?
                """,
                (
                    experiment_uuid,
                    measurement_type
                )
            ).fetchone()

        return dict(result) if result else None

    def update_archive_state(
        self,
        experiment_uuid,
        measurement_type,
        archive_path,
        source_fingerprint,
        archive_status,
        last_source_change,
        source_file_count
    ):
        """
        Insert or update the source-side archive state.

        Existing generated fingerprint and checksum are preserved
        during an update. They describe the last successfully
        generated archive, not the current source files.

        Existing NOMAD upload information is also preserved.
        """

        now = utc_now()

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
                    updated_at

                )

                VALUES (
                    ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?
                )

                ON CONFLICT(
                    experiment_uuid,
                    measurement_type
                )

                DO UPDATE SET

                    archive_path = excluded.archive_path,

                    source_fingerprint =
                        excluded.source_fingerprint,

                    source_file_count =
                        excluded.source_file_count,

                    archive_status =
                        excluded.archive_status,

                    last_source_change =
                        excluded.last_source_change,

                    updated_at =
                        excluded.updated_at
                """,
                (
                    experiment_uuid,
                    measurement_type,
                    str(archive_path),
                    source_fingerprint,
                    source_file_count,
                    archive_status,
                    last_source_change,
                    now
                )
            )

    def mark_archive_generated(
        self,
        experiment_uuid,
        measurement_type,
        archive_checksum,
        generated_fingerprint
    ):
        """
        Mark an archive as successfully generated.

        Stores its checksum and the source fingerprint used
        to generate it.
        """

        now = utc_now()

        with self.connection() as connection:

            cursor = connection.execute(
                """
                UPDATE archive_state

                SET

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
                    generated_fingerprint,
                    now,
                    now,
                    experiment_uuid,
                    measurement_type
                )
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot mark archive as generated: "
                    f"no archive state exists for "
                    f"{experiment_uuid} / {measurement_type}"
                )

    # ---------------------------------------------------------
    # NOMAD UPLOAD MANAGEMENT
    # ---------------------------------------------------------

    def set_nomad_upload_id(
        self,
        experiment_uuid,
        upload_id
    ):
        """
        Store the NOMAD upload ID for an experiment.

        One NOMAD upload is reused for all measurement archives
        belonging to the same experiment.
        """

        now = utc_now()

        with self.connection() as connection:

            cursor = connection.execute(
                """
                UPDATE experiments

                SET
                    nomad_upload_id = ?,
                    updated_at = ?

                WHERE experiment_uuid = ?
                """,
                (
                    upload_id,
                    now,
                    experiment_uuid
                )
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    f"Experiment not found: {experiment_uuid}"
                )

    def mark_archive_upload_started(
        self,
        experiment_uuid,
        measurement_type
    ):
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
                (
                    now,
                    experiment_uuid,
                    measurement_type
                )
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot start upload: archive state not found for "
                    f"{experiment_uuid} / {measurement_type}"
                )

    def mark_archive_uploaded(
        self,
        experiment_uuid,
        measurement_type,
        uploaded_checksum
    ):
        """
        Mark an archive as successfully uploaded and verified.

        The uploaded checksum records the exact local archive
        version that was successfully uploaded.
        """

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
                    measurement_type
                )
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot record upload: archive state not found for "
                    f"{experiment_uuid} / {measurement_type}"
                )

    def mark_archive_upload_failed(
        self,
        experiment_uuid,
        measurement_type,
        error_message
    ):
        """Record a failed NOMAD upload without losing prior success."""

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
                    str(error_message),
                    now,
                    experiment_uuid,
                    measurement_type
                )
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Cannot record upload failure: archive state "
                    "not found for "
                    f"{experiment_uuid} / {measurement_type}"
                )
