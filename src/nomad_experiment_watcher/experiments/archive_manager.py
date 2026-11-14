
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
    ".crdownload"
}


class ArchiveManager:

    def __init__(
        self,
        database,
        output_directory,
        measurement_types,
        quiet_period_seconds=300,
        stability_period_seconds=60,
        compression_level=6,
        uploader=None
    ):

        self.database = database

        self.output_directory = Path(
            output_directory
        ).resolve()

        self.measurement_types = measurement_types

        self.quiet_period_seconds = quiet_period_seconds
        self.stability_period_seconds = stability_period_seconds

        self.compression_level = compression_level

        # Optional NOMAD uploader.
        # Local archive generation remains functional
        # even when no uploader is configured.
        self.uploader = uploader

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True
        )

    # --------------------------------------------------
    # Collect source files
    # --------------------------------------------------

    def collect_files(self, measurement_directory):

        files = []

        if not measurement_directory.is_dir():
            return files

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
            ).as_posix()
        )

    # --------------------------------------------------
    # Generate source fingerprint
    # --------------------------------------------------

    def calculate_fingerprint(
        self,
        measurement_directory,
        files
    ):

        digest = hashlib.sha256()

        for path in files:

            relative_path = path.relative_to(
                measurement_directory
            ).as_posix()

            stat = path.stat()

            record = (
                f"{relative_path}|"
                f"{stat.st_size}|"
                f"{stat.st_mtime_ns}\n"
            )

            digest.update(record.encode("utf-8"))

        return digest.hexdigest()

    # --------------------------------------------------
    # Check file stability
    # --------------------------------------------------

    def files_are_stable(self, files):

        now = time.time()

        for path in files:

            try:
                modified_time = path.stat().st_mtime

            except OSError:
                return False

            if now - modified_time < self.stability_period_seconds:
                return False

        return True

    # --------------------------------------------------
    # Calculate archive checksum
    # --------------------------------------------------

    def calculate_checksum(self, archive_path):

        digest = hashlib.sha256()

        with archive_path.open("rb") as file:

            for chunk in iter(
                lambda: file.read(1024 * 1024),
                b""
            ):

                digest.update(chunk)

        return digest.hexdigest()

    # --------------------------------------------------
    # Generate ZIP archive
    # --------------------------------------------------

    def generate_archive(
        self,
        measurement_directory,
        files,
        archive_path,
        expected_fingerprint
    ):

        archive_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        temporary_fd, temporary_name = tempfile.mkstemp(
            dir=archive_path.parent,
            prefix=f".{archive_path.name}.",
            suffix=".tmp"
        )

        os.close(temporary_fd)

        temporary_path = Path(temporary_name)

        try:

            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=self.compression_level
            ) as archive:

                for source_file in files:

                    relative_path = source_file.relative_to(
                        measurement_directory
                    )

                    archive.write(
                        source_file,
                        arcname=relative_path.as_posix()
                    )

            # Verify the generated ZIP.

            with zipfile.ZipFile(
                temporary_path,
                mode="r"
            ) as archive:

                bad_file = archive.testzip()

                if bad_file is not None:
                    raise ValueError(
                        f"Corrupted archive member: {bad_file}"
                    )

                if len(archive.namelist()) != len(files):
                    raise ValueError(
                        "Archive file count does not match "
                        "source file count."
                    )

            # Recheck source files after packaging.

            current_files = self.collect_files(
                measurement_directory
            )

            current_fingerprint = self.calculate_fingerprint(
                measurement_directory,
                current_files
            )

            if current_fingerprint != expected_fingerprint:

                logger.warning(
                    "Source files changed during packaging. "
                    "Keeping the previous archive: %s",
                    archive_path.name
                )

                return None

            # Attempt to flush archive data to disk.

            try:

                with open(temporary_path, "r+b") as file:
                    file.flush()
                    os.fsync(file.fileno())

            except OSError as error:

                logger.warning(
                    "Could not fsync temporary archive %s: %s",
                    temporary_path,
                    error
                )

            checksum = self.calculate_checksum(
                temporary_path
            )

            # Atomic replacement:
            # The old archive remains available until this point.

            os.replace(
                temporary_path,
                archive_path
            )

            return checksum

        finally:

            if temporary_path.exists():
                temporary_path.unlink()

    # --------------------------------------------------
    # NOMAD upload management
    # --------------------------------------------------

    def upload_archive_if_needed(
        self,
        experiment_uuid,
        measurement_type,
        archive_path
    ):
        """
        Upload a generated archive to NOMAD if necessary.

        One NOMAD upload is reused for all measurement types
        belonging to the same experiment.

        A previously uploaded archive is skipped when its
        checksum matches the current local archive checksum.

        Failed uploads are retried on subsequent scans.
        """

        if self.uploader is None:

            return

        archive_path = Path(archive_path)

        if not archive_path.is_file():

            logger.warning(
                "Cannot upload archive because it does not exist: %s",
                archive_path
            )

            return

        state = self.database.get_archive_state(
            experiment_uuid,
            measurement_type
        )

        if state is None:

            logger.warning(
                "Cannot upload archive: database state not found "
                "for %s / %s",
                experiment_uuid,
                measurement_type
            )

            return

        # ----------------------------------------------
        # Calculate current local archive checksum
        # ----------------------------------------------

        current_checksum = self.calculate_checksum(
            archive_path
        )

        stored_checksum = state.get("archive_checksum")

        if not stored_checksum:

            logger.error(
                "Archive checksum missing in database. "
                "Upload skipped: %s",
                archive_path
            )

            return

        if current_checksum != stored_checksum:

            error_message = (
                "Local archive checksum does not match "
                "the checksum stored in the database."
            )

            logger.error(
                "%s Archive: %s",
                error_message,
                archive_path
            )

            self.database.mark_archive_upload_failed(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                error_message=error_message
            )

            return

        # ----------------------------------------------
        # Skip if this exact archive was already uploaded
        # ----------------------------------------------

        if (
            state.get("upload_status") == "UPLOADED"
            and state.get("uploaded_checksum") == current_checksum
        ):

            logger.debug(
                "Archive already uploaded and verified. "
                "Skipping: %s",
                archive_path.name
            )

            return

        # ----------------------------------------------
        # Retrieve experiment information
        # ----------------------------------------------

        experiment = self.database.get_experiment(
            experiment_uuid
        )

        if experiment is None:

            logger.error(
                "Experiment not found in database: %s",
                experiment_uuid
            )

            return

        upload_id = experiment.get("nomad_upload_id")

        try:

            # ------------------------------------------
            # Create NOMAD upload if one does not exist
            # ------------------------------------------

            if not upload_id:

                logger.info(
                    "Creating NOMAD upload for experiment: %s",
                    experiment["experiment_id"]
                )

                upload_id = self.uploader.create_upload(
                    upload_name=experiment["experiment_id"],
                    auto_decompress=False
                )

                self.database.set_nomad_upload_id(
                    experiment_uuid=experiment_uuid,
                    upload_id=upload_id
                )

                logger.info(
                    "NOMAD upload ID saved for %s",
                    experiment["experiment_id"]
                )

            # ------------------------------------------
            # Mark upload as started
            # ------------------------------------------

            self.database.mark_archive_upload_started(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type
            )

            logger.info(
                "Uploading %s archive for %s",
                measurement_type,
                experiment["experiment_id"]
            )

            # ------------------------------------------
            # Upload archive
            # ------------------------------------------

            result = self.uploader.upload_file(

                upload_id=upload_id,

                local_path=archive_path,

                remote_directory=measurement_type,

                auto_decompress=False
            )

            # ------------------------------------------
            # Require successful remote verification
            # ------------------------------------------

            if result.get("verified") is not True:

                raise RuntimeError(
                    "NOMAD upload completed without successful "
                    "remote file verification."
                )

            # ------------------------------------------
            # Recalculate checksum after upload
            # ------------------------------------------

            final_checksum = self.calculate_checksum(
                archive_path
            )

            if final_checksum != current_checksum:

                raise RuntimeError(
                    "Local archive changed during NOMAD upload."
                )

            # ------------------------------------------
            # Record successful upload
            # ------------------------------------------

            self.database.mark_archive_uploaded(

                experiment_uuid=experiment_uuid,

                measurement_type=measurement_type,

                uploaded_checksum=current_checksum
            )

            logger.info(
                "NOMAD upload verified successfully: %s / %s",
                experiment["experiment_id"],
                measurement_type
            )

        except Exception as error:

            # ------------------------------------------
            # Record upload failure
            # ------------------------------------------

            try:

                self.database.mark_archive_upload_failed(

                    experiment_uuid=experiment_uuid,

                    measurement_type=measurement_type,

                    error_message=str(error)
                )

            except Exception:

                logger.exception(
                    "Could not update upload failure state "
                    "for %s / %s",
                    experiment_uuid,
                    measurement_type
                )

            logger.exception(
                "NOMAD upload failed for %s / %s. "
                "It will be retried during a future scan.",
                experiment["experiment_id"],
                measurement_type
            )

    # --------------------------------------------------
    # Process one measurement directory
    # --------------------------------------------------

    def process_measurement(
        self,
        experiment_uuid,
        experiment_directory,
        measurement_type,
        extension
    ):

        measurement_directory = (
            experiment_directory / measurement_type
        )

        if not measurement_directory.is_dir():
            return

        files = self.collect_files(
            measurement_directory
        )

        if not files:
            return

        fingerprint = self.calculate_fingerprint(
            measurement_directory,
            files
        )

        archive_path = (
            self.output_directory
            / experiment_directory.name
            / f"{experiment_directory.name}{extension}"
        )

        state = self.database.get_archive_state(
            experiment_uuid,
            measurement_type
        )

        now = time.time()

        # ----------------------------------------------
        # First discovery or source modification
        # ----------------------------------------------

        if (
            state is None
            or state["source_fingerprint"] != fingerprint
        ):

            status = (
                "OUTDATED"
                if archive_path.exists()
                else "WAITING_FOR_FILES"
            )

            self.database.update_archive_state(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                archive_path=archive_path,
                source_fingerprint=fingerprint,
                archive_status=status,
                last_source_change=now,
                source_file_count=len(files)
            )

            logger.info(
                "%s archive requires generation for %s",
                measurement_type,
                experiment_directory.name
            )

            return

        # ----------------------------------------------
        # Archive already up to date
        # ----------------------------------------------

        if (
            state["generated_fingerprint"] == fingerprint
            and archive_path.exists()
        ):

            # Important:
            # This also handles retries of failed NOMAD uploads.
            # Do not return before checking upload status.

            self.upload_archive_if_needed(
                experiment_uuid=experiment_uuid,
                measurement_type=measurement_type,
                archive_path=archive_path
            )

            return

        # ----------------------------------------------
        # Wait for quiet period
        # ----------------------------------------------

        last_change = state["last_source_change"]

        if last_change is None:
            return

        quiet_period = now - last_change

        if quiet_period < self.quiet_period_seconds:
            return

        # ----------------------------------------------
        # Check individual file stability
        # ----------------------------------------------

        if not self.files_are_stable(files):
            return

        logger.info(
            "Generating %s archive for %s",
            measurement_type,
            experiment_directory.name
        )

        # ----------------------------------------------
        # Generate and validate archive
        # ----------------------------------------------

        checksum = self.generate_archive(
            measurement_directory=measurement_directory,
            files=files,
            archive_path=archive_path,
            expected_fingerprint=fingerprint
        )

        if checksum is None:
            return

        # ----------------------------------------------
        # Update database
        # ----------------------------------------------

        self.database.mark_archive_generated(
            experiment_uuid=experiment_uuid,
            measurement_type=measurement_type,
            archive_checksum=checksum,
            generated_fingerprint=fingerprint
        )

        logger.info(
            "Archive generated successfully: %s",
            archive_path
        )

        # ----------------------------------------------
        # Upload newly generated archive
        # ----------------------------------------------

        self.upload_archive_if_needed(
            experiment_uuid=experiment_uuid,
            measurement_type=measurement_type,
            archive_path=archive_path
        )

    # --------------------------------------------------
    # Process all measurement directories
    # --------------------------------------------------

    def scan_experiment(self, experiment_directory):

        experiment_directory = Path(
            experiment_directory
        ).resolve()

        experiment = self.database.get_experiment_by_path(
            str(experiment_directory)
        )

        if experiment is None:
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
                    extension=extension
                )

            except Exception:

                logger.exception(
                    "Archive processing failed for %s / %s",
                    experiment_directory.name,
                    measurement_type
                )
