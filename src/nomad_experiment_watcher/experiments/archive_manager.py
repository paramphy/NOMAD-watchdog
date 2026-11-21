from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
import zipfile

from pathlib import Path


logger = logging.getLogger(__name__)


TEMPORARY_SUFFIXES = {
    ".tmp",
    ".part",
    ".partial",
    ".crdownload",
}


class ArchiveManager:
    """
    Generates validated ZIP archives for experiment measurement
    directories and optionally uploads them to NOMAD.

    Local archive generation does not depend on NOMAD availability.

    NOMAD behaviour:
      - One NOMAD upload is created per experiment.
      - Its upload ID is stored in the SQLite database.
      - Each measurement archive is uploaded into a directory
        named after its measurement type.
      - A verified archive is not uploaded again unless its
        checksum changes.
      - Failed uploads are retried during subsequent scans.

    File stability:
      - Source changes must first remain quiet for the configured
        quiet period.
      - Individual files must also remain unchanged across scans
        for the configured stability period.
    """

    def __init__(
        self,
        database,
        output_directory: str | Path,
        measurement_types: dict[str, str],
        quiet_period_seconds: float = 300,
        stability_period_seconds: float = 60,
        compression_level: int = 6,
        uploader=None,
        coauthor_group_id: str | None = None,
    ):
        self.database = database

        self.output_directory = Path(
            output_directory
        ).resolve()

        self.measurement_types = dict(measurement_types)

        self.quiet_period_seconds = float(
            quiet_period_seconds
        )

        self.stability_period_seconds = float(
            stability_period_seconds
        )

        self.compression_level = int(compression_level)

        self.uploader = uploader

        self.coauthor_group_id = (
            coauthor_group_id.strip()
            if isinstance(coauthor_group_id, str)
            and coauthor_group_id.strip()
            else None
        )

        # Remember uploads whose group permissions were verified
        # during this application session.
        self._shared_upload_ids = set()

        if self.quiet_period_seconds < 0:
            raise ValueError(
                "quiet_period_seconds cannot be negative."
            )

        if self.stability_period_seconds < 0:
            raise ValueError(
                "stability_period_seconds cannot be negative."
            )

        if not 0 <= self.compression_level <= 9:
            raise ValueError(
                "compression_level must be between 0 and 9."
            )

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        # In-memory observation history:
        #
        # {
        #   "/path/to/file": {
        #       "size": 1234,
        #       "mtime_ns": 123456789,
        #       "unchanged_since": 12345.6
        #   }
        # }
        #
        # This is deliberately not stored in SQLite. After a watcher
        # restart, files must be observed again before being treated
        # as stable.
        self._file_observations: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # SOURCE FILE COLLECTION
    # ------------------------------------------------------------------

    def collect_files(
        self,
        measurement_directory: str | Path,
    ) -> list[Path]:
        """
        Recursively collect source files.

        Temporary/incomplete files are excluded.
        Returned paths are sorted by relative path for consistency.
        """
        measurement_directory = Path(
            measurement_directory
        )

        if not measurement_directory.is_dir():
            return []

        files = []

        for path in measurement_directory.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() in TEMPORARY_SUFFIXES:
                continue

            files.append(path)

        return sorted(
            files,
            key=lambda path: path.relative_to(
                measurement_directory
            ).as_posix(),
        )

    # ------------------------------------------------------------------
    # SOURCE FINGERPRINT
    # ------------------------------------------------------------------

    def calculate_fingerprint(
        self,
        measurement_directory: str | Path,
        files: list[Path],
    ) -> str:
        """
        Calculate a SHA-256 fingerprint from:

          - Relative file path
          - File size
          - File modification time in nanoseconds

        This is a metadata fingerprint, not a content checksum.
        It avoids reading every byte of potentially large source files
        during every scan.
        """
        measurement_directory = Path(
            measurement_directory
        )

        digest = hashlib.sha256()

        for path in files:
            stat = path.stat()

            relative_path = path.relative_to(
                measurement_directory
            ).as_posix()

            record = (
                f"{relative_path}|"
                f"{stat.st_size}|"
                f"{stat.st_mtime_ns}\n"
            )

            digest.update(
                record.encode("utf-8")
            )

        return digest.hexdigest()

    # ------------------------------------------------------------------
    # FILE STABILITY
    # ------------------------------------------------------------------

    def files_are_stable(
        self,
        files: list[Path],
        scope_directory: str | Path | None = None,
    ) -> bool:
        """
        Return True only when every file has been observed unchanged
        for at least stability_period_seconds.

        Cleanup is restricted to the supplied measurement directory,
        so observations belonging to other measurement types survive.
        """
        now = time.monotonic()

        all_stable = True
        active_paths = set()

        for path in files:
            path_key = str(path.resolve())
            active_paths.add(path_key)

            try:
                stat = path.stat()
            except OSError:
                self._file_observations.pop(path_key, None)
                all_stable = False
                continue

            current_size = stat.st_size
            current_mtime_ns = stat.st_mtime_ns

            previous = self._file_observations.get(path_key)

            if previous is None:
                self._file_observations[path_key] = {
                    "size": current_size,
                    "mtime_ns": current_mtime_ns,
                    "unchanged_since": now,
                }
                all_stable = False
                continue

            unchanged = (
                previous["size"] == current_size
                and previous["mtime_ns"] == current_mtime_ns
            )

            if not unchanged:
                self._file_observations[path_key] = {
                    "size": current_size,
                    "mtime_ns": current_mtime_ns,
                    "unchanged_since": now,
                }
                all_stable = False
                continue

            unchanged_duration = (
                now - previous["unchanged_since"]
            )

            if unchanged_duration < self.stability_period_seconds:
                all_stable = False

        # Remove stale observations only inside this measurement folder.
        if scope_directory is not None:
            scope = Path(scope_directory).resolve()

            for old_path in list(self._file_observations):
                try:
                    Path(old_path).relative_to(scope)
                except ValueError:
                    continue

                if old_path not in active_paths:
                    self._file_observations.pop(old_path, None)

        return all_stable

    # ------------------------------------------------------------------
    # ARCHIVE CHECKSUM
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_checksum(
        archive_path: str | Path,
    ) -> str:
        """Calculate the SHA-256 checksum of a generated archive."""
        archive_path = Path(archive_path)

        digest = hashlib.sha256()

        with archive_path.open("rb") as file_handle:
            for chunk in iter(
                lambda: file_handle.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)

        return digest.hexdigest()

    # ------------------------------------------------------------------
    # GENERATE AND VALIDATE ZIP ARCHIVE
    # ------------------------------------------------------------------

    def generate_archive(
        self,
        measurement_directory: str | Path,
        files: list[Path],
        archive_path: str | Path,
        expected_fingerprint: str,
    ) -> str | None:
        """
        Generate a ZIP archive safely.

        Steps:
          1. Write to a temporary file in the destination directory.
          2. Validate the ZIP structure and member count.
          3. Recalculate the source fingerprint.
          4. Keep the old archive if source files changed.
          5. Calculate the ZIP checksum.
          6. Atomically replace the previous archive.

        Returns:
            SHA-256 checksum on success.
            None if source files changed during packaging.
        """
        measurement_directory = Path(
            measurement_directory
        )

        archive_path = Path(archive_path)

        archive_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_fd, temporary_name = tempfile.mkstemp(
            dir=archive_path.parent,
            prefix=f".{archive_path.name}.",
            suffix=".tmp",
        )

        os.close(temporary_fd)

        temporary_path = Path(temporary_name)

        try:
            # ------------------------------------------------------
            # 1. Write temporary ZIP
            # ------------------------------------------------------

            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=self.compression_level,
            ) as archive:

                for source_file in files:
                    relative_path = source_file.relative_to(
                        measurement_directory
                    )

                    archive.write(
                        source_file,
                        arcname=relative_path.as_posix(),
                    )

            # ------------------------------------------------------
            # 2. Validate temporary ZIP
            # ------------------------------------------------------

            with zipfile.ZipFile(
                temporary_path,
                mode="r",
            ) as archive:

                bad_file = archive.testzip()

                if bad_file is not None:
                    raise ValueError(
                        "ZIP validation failed for member: "
                        f"{bad_file}"
                    )

                archive_members = archive.namelist()

                if len(archive_members) != len(files):
                    raise ValueError(
                        "Archive member count does not match "
                        f"source file count: {len(archive_members)} "
                        f"versus {len(files)}"
                    )

            # ------------------------------------------------------
            # 3. Recheck source fingerprint
            # ------------------------------------------------------

            current_files = self.collect_files(
                measurement_directory
            )

            current_fingerprint = self.calculate_fingerprint(
                measurement_directory,
                current_files,
            )

            if current_fingerprint != expected_fingerprint:
                logger.warning(
                    "Source files changed while packaging %s. "
                    "Temporary archive will be discarded.",
                    archive_path.name,
                )

                return None

            # ------------------------------------------------------
            # 4. Flush temporary archive to disk
            # ------------------------------------------------------

            try:
                with temporary_path.open("r+b") as file_handle:
                    file_handle.flush()
                    os.fsync(file_handle.fileno())

            except OSError as error:
                logger.warning(
                    "Could not fsync temporary archive %s: %s",
                    temporary_path,
                    error,
                )

            # ------------------------------------------------------
            # 5. Calculate checksum before replacement
            # ------------------------------------------------------

            checksum = self.calculate_checksum(
                temporary_path
            )

            # ------------------------------------------------------
            # 6. Atomic replacement
            # ------------------------------------------------------

            os.replace(
                temporary_path,
                archive_path,
            )

            logger.info(
                "Archive replaced successfully: %s",
                archive_path,
            )

            return checksum

        finally:
            # Remove the temporary file if it still exists.
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    logger.warning(
                        "Could not remove temporary archive: %s",
                        temporary_path,
                    )
    def ensure_upload_group_access(
        self,
        upload_id: str,
    ) -> None:
        """
        Ensure the configured research group has coauthor access
        to the specified NOMAD upload.
        """

        if not self.coauthor_group_id:
            return

        if upload_id in self._shared_upload_ids:
            return

        self.uploader.ensure_coauthor_group(
            upload_id=upload_id,
            group_id=self.coauthor_group_id,
        )

        self._shared_upload_ids.add(upload_id)

        logger.info(
            "Group coauthor access verified for upload %s.",
            upload_id,
        )
    # ------------------------------------------------------------------
    # NOMAD UPLOAD MANAGEMENT
    # ------------------------------------------------------------------

    def upload_archive_if_needed(
        self,
        experiment_uuid: str,
        measurement_type: str,
        archive_path: str | Path,
    ) -> None:
        """
        Upload an archive to NOMAD when required.

        Upload is skipped when:
          - No uploader is configured.
          - The local archive does not exist.
          - The database has no matching archive state.
          - The local checksum does not match the generated checksum
            recorded in the database (in this case the state is marked
            FAILED).
          - The archive has already been uploaded and verified with
            the same checksum.

        The experiment's existing NOMAD upload ID is reused.
        """
        if self.uploader is None:
            return

        archive_path = Path(archive_path)

        try:
            # ------------------------------------------------------
            # 1. Confirm local archive exists
            # ------------------------------------------------------

            if not archive_path.is_file():
                logger.warning(
                    "Cannot upload missing archive: %s",
                    archive_path,
                )
                return

            # ------------------------------------------------------
            # 2. Retrieve archive state
            # ------------------------------------------------------

            state = self.database.get_archive_state(
                experiment_uuid,
                measurement_type,
            )

            if state is None:
                logger.warning(
                    "Archive state not found. Upload skipped for "
                    "%s / %s",
                    experiment_uuid,
                    measurement_type,
                )
                return

            # ------------------------------------------------------
            # 3. Confirm local archive matches generated checksum
            # ------------------------------------------------------

            current_checksum = self.calculate_checksum(
                archive_path
            )

            stored_checksum = state.get(
                "archive_checksum"
            )

            if not stored_checksum:
                logger.error(
                    "Database archive checksum is missing. "
                    "Upload skipped: %s",
                    archive_path,
                )
                return

            if current_checksum != stored_checksum:
                error_message = (
                    "Local archive checksum does not match the "
                    "checksum stored in the database."
                )

                logger.error(
                    "%s Archive: %s",
                    error_message,
                    archive_path,
                )

                self.database.mark_archive_upload_failed(
                    experiment_uuid=experiment_uuid,
                    measurement_type=measurement_type,
                    error_message=error_message,
                )

                return

            # ------------------------------------------------------
            # 5. Retrieve experiment
            # ------------------------------------------------------

            experiment = self.database.get_experiment(
                experiment_uuid
            )

            if experiment is None:
                raise ValueError(
                    "Experiment not found in database: "
                    f"{experiment_uuid}"
                )

            experiment_id = experiment.get(
                "experiment_id",
                experiment_uuid,
            )

            upload_id = experiment.get(
                "nomad_upload_id"
            )

            # ------------------------------------------------------
            # 6. Require the upload created during experiment discovery
            # ------------------------------------------------------

            if not upload_id:
                logger.warning(
                    "NOMAD upload ID is not available for experiment %s. "
                    "Archive upload is deferred until experiment discovery "
                    "successfully creates the upload.",
                    experiment_id,
                )
                return

            # Ensure access before uploading the archive. This also runs
            # for previously uploaded archives, so a failed group assignment
            # can be retried on a later scan without retransferring the file.
            try:
                self.ensure_upload_group_access(upload_id)
            except Exception:
                logger.exception(
                    "Could not assign coauthor group to NOMAD upload %s. "
                    "This archive will be retried during a future scan.",
                    upload_id,
                )
                return

            # Skip the transfer only after confirming the configured group
            # has access (when sharing is enabled).
            if (
                state.get("upload_status") == "UPLOADED"
                and state.get("uploaded_checksum") == current_checksum
            ):
                logger.debug(
                    "Archive is already uploaded and verified. Skipping: %s",
                    archive_path.name,
                )
                return

            # ------------------------------------------------------
            # 7. Mark upload as started
            # ------------------------------------------------------

            self.database.mark_archive_upload_started(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
            )

            # ------------------------------------------------------
            # 8. Upload into the measurement-specific directory
            # ------------------------------------------------------

            logger.info(
                "Uploading %s archive for experiment %s",
                measurement_type,
                experiment_id,
            )

            result = self.uploader.upload_file(
                upload_id=upload_id,
                local_path=archive_path,
                remote_directory=measurement_type,
                auto_decompress=False,
            )

            # ------------------------------------------------------
            # 9. Require successful remote verification
            # ------------------------------------------------------

            if not isinstance(result, dict):
                raise RuntimeError(
                    "NOMAD uploader returned an unexpected result."
                )

            if result.get("verified") is not True:
                raise RuntimeError(
                    "NOMAD accepted the upload request, but remote "
                    "file verification did not succeed."
                )

            # ------------------------------------------------------
            # 10. Confirm local archive did not change during upload
            # ------------------------------------------------------

            final_checksum = self.calculate_checksum(
                archive_path
            )

            if final_checksum != current_checksum:
                raise RuntimeError(
                    "Local archive changed during NOMAD upload."
                )

            # ------------------------------------------------------
            # 11. Record successful upload
            # ------------------------------------------------------

            self.database.mark_archive_uploaded(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                uploaded_checksum=current_checksum,
            )

            logger.info(
                "NOMAD upload verified successfully: %s / %s",
                experiment_id,
                measurement_type,
            )

        except Exception as error:
            # Upload errors must not stop local archive monitoring.
            logger.exception(
                "NOMAD upload failed for %s / %s. "
                "The watcher will retry during a future scan.",
                experiment_uuid,
                measurement_type,
            )

            try:
                self.database.mark_archive_upload_failed(
                    experiment_uuid=experiment_uuid,
                    measurement_type=measurement_type,
                    error_message=str(error),
                )

            except Exception:
                logger.exception(
                    "Could not record upload failure in the database "
                    "for %s / %s",
                    experiment_uuid,
                    measurement_type,
                )

    # ------------------------------------------------------------------
    # PROCESS ONE MEASUREMENT DIRECTORY
    # ------------------------------------------------------------------

    def process_measurement(
        self,
        experiment_uuid: str,
        experiment_directory: str | Path,
        measurement_type: str,
        extension: str,
    ) -> None:
        """
        Process one measurement directory.

        This method:
          - Detects source changes.
          - Updates database state.
          - Waits for quiet and file stability periods.
          - Generates and validates the archive.
          - Attempts NOMAD upload.
          - Retries failed uploads on later scans.
        """
        experiment_directory = Path(
            experiment_directory
        ).resolve()

        measurement_directory = (
            experiment_directory / measurement_type
        )

        if not measurement_directory.is_dir():
            return

        # ----------------------------------------------------------
        # 1. Collect source files
        # ----------------------------------------------------------

        files = self.collect_files(
            measurement_directory
        )

        if not files:
            # Do not delete an existing archive merely because a
            # directory is temporarily empty or unavailable.
            return

        # ----------------------------------------------------------
        # 2. Calculate source fingerprint
        # ----------------------------------------------------------

        fingerprint = self.calculate_fingerprint(
            measurement_directory,
            files,
        )

        archive_path = (
            self.output_directory
            / experiment_directory.name
            / f"{experiment_directory.name}{extension}"
        )

        # ----------------------------------------------------------
        # 3. Retrieve current state
        # ----------------------------------------------------------

        state = self.database.get_archive_state(
            experiment_uuid,
            measurement_type,
        )

        now = time.time()

        # ----------------------------------------------------------
        # 4. First discovery or source modification
        # ----------------------------------------------------------

        if (
            state is None
            or state.get("source_fingerprint") != fingerprint
        ):
            archive_status = (
                "OUTDATED"
                if archive_path.exists()
                else "WAITING_FOR_FILES"
            )

            self.database.update_archive_state(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                archive_path=archive_path,
                source_fingerprint=fingerprint,
                archive_status=archive_status,
                last_source_change=now,
                source_file_count=len(files),
            )

            logger.info(
                "%s archive requires generation for %s",
                measurement_type,
                experiment_directory.name,
            )

            # Wait for the quiet period before generating.
            return

        # ----------------------------------------------------------
        # 5. Archive is current: retry upload if necessary
        # ----------------------------------------------------------

        archive_is_current = (
            state.get("generated_fingerprint") == fingerprint
            and archive_path.is_file()
        )

        if archive_is_current:
            # This method checks the checksum and upload status.
            # It returns immediately if the same archive is already
            # uploaded and verified.
            self.upload_archive_if_needed(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                archive_path=archive_path,
            )

            return

        # ----------------------------------------------------------
        # 6. Wait for quiet period
        # ----------------------------------------------------------

        last_change = state.get(
            "last_source_change"
        )

        if last_change is None:
            return

        quiet_duration = now - float(last_change)

        if quiet_duration < self.quiet_period_seconds:
            return

        # ----------------------------------------------------------
        # 7. Wait until every file is observed stable
        # ----------------------------------------------------------

        if not self.files_are_stable(
            files,
            scope_directory=measurement_directory,
        ):
            return

        logger.info(
            "Generating %s archive for %s",
            measurement_type,
            experiment_directory.name,
        )

        # ----------------------------------------------------------
        # 8. Generate and validate archive
        # ----------------------------------------------------------

        checksum = self.generate_archive(
            measurement_directory=measurement_directory,
            files=files,
            archive_path=archive_path,
            expected_fingerprint=fingerprint,
        )

        if checksum is None:
            # Source changed during packaging. A future scan will
            # detect the new fingerprint and restart the quiet period.
            return

        # ----------------------------------------------------------
        # 9. Record successful archive generation
        # ----------------------------------------------------------

        self.database.mark_archive_generated(
            experiment_uuid=experiment_uuid,
            measurement_type=measurement_type,
            archive_checksum=checksum,
            generated_fingerprint=fingerprint,
        )

        logger.info(
            "Archive generated successfully: %s",
            archive_path,
        )

        # ----------------------------------------------------------
        # 10. Upload the newly generated archive
        # ----------------------------------------------------------

        self.upload_archive_if_needed(
            experiment_uuid=experiment_uuid,
            measurement_type=measurement_type,
            archive_path=archive_path,
        )

    # ------------------------------------------------------------------
    # SCAN ALL MEASUREMENT DIRECTORIES FOR ONE EXPERIMENT
    # ------------------------------------------------------------------

    def scan_experiment(
        self,
        experiment_directory: str | Path,
    ) -> None:
        """
        Process every configured measurement type for one experiment.

        An error in one measurement does not stop the other
        measurement types from being processed.
        """
        experiment_directory = Path(
            experiment_directory
        ).resolve()

        experiment = self.database.get_experiment_by_path(
            experiment_directory
        )

        if experiment is None:
            logger.warning(
                "Experiment is not registered in the database: %s",
                experiment_directory,
            )
            return

        experiment_uuid = experiment["experiment_uuid"]

        for measurement_type, extension in (
            self.measurement_types.items()
        ):
            try:
                self.process_measurement(
                    experiment_uuid=experiment_uuid,
                    experiment_directory=experiment_directory,
                    measurement_type=measurement_type,
                    extension=extension,
                )

            except Exception:
                logger.exception(
                    "Archive processing failed for %s / %s",
                    experiment_directory.name,
                    measurement_type,
                )
