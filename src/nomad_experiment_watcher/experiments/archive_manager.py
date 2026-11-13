
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
        compression_level=6
    ):

        self.database = database

        self.output_directory = Path(
            output_directory
        ).resolve()

        self.measurement_types = measurement_types

        self.quiet_period_seconds = quiet_period_seconds
        self.stability_period_seconds = stability_period_seconds

        self.compression_level = compression_level

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
                    "Archive generation failed for %s / %s",
                    experiment_directory.name,
                    measurement_type
                )
