import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from nomad_experiment_watcher.experiments.metadata import (
    ExperimentMetadataManager,
)

logger = logging.getLogger(__name__)


class ExperimentManager:
    """Discovers experiments and coordinates their NOMAD identity."""

    def __init__(
        self,
        database,
        experiment_settings,
        nomad_uploader=None,
        upload_retry_interval_seconds: float = 300,
    ):
        self.database = database
        self.metadata_manager = ExperimentMetadataManager(experiment_settings)
        self.nomad_uploader = nomad_uploader
        self.upload_retry_interval_seconds = max(
            0.0, float(upload_retry_interval_seconds)
        )
        self._lock = threading.RLock()

    def _ensure_nomad_upload(self, experiment: dict) -> None:
        """Create one NOMAD upload for a registered experiment, if needed."""
        experiment_uuid = experiment["experiment_uuid"]
        experiment_id = experiment["experiment_id"]

        if experiment.get("nomad_upload_id"):
            return

        if self.nomad_uploader is None:
            current_status = experiment.get(
                "nomad_upload_status", "NOT_CREATED"
            )
            if current_status in ("NOT_CREATED", "NOT_CONFIGURED"):
                self.database.update_nomad_upload_state(
                    experiment_uuid,
                    "NOT_CONFIGURED",
                    error_message="NOMAD credentials/client are not configured.",
                )
            return

        # Avoid repeatedly creating remote uploads during a prolonged outage.
        attempted_at = experiment.get("nomad_upload_attempted_at")
        if attempted_at and self.upload_retry_interval_seconds > 0:
            try:
                previous = datetime.fromisoformat(attempted_at)
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                elapsed = (
                    datetime.now(timezone.utc) - previous
                ).total_seconds()
                if elapsed < self.upload_retry_interval_seconds:
                    return
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid NOMAD upload attempt timestamp for %s; "
                    "retrying creation.",
                    experiment_id,
                )

        self.database.update_nomad_upload_state(
            experiment_uuid,
            "CREATING",
            error_message=None,
            record_attempt=True,
        )

        try:
            logger.info(
                "Creating NOMAD upload for newly discovered experiment: %s",
                experiment_id,
            )
            upload_id = self.nomad_uploader.create_upload(
                upload_name=experiment_id,
                auto_decompress=False,
            )

            if not isinstance(upload_id, str) or not upload_id.strip():
                raise RuntimeError(
                    "NOMAD did not return a valid upload ID."
                )

            self.database.set_nomad_upload_id(
                experiment_uuid=experiment_uuid,
                upload_id=upload_id,
            )

            logger.info(
                "NOMAD upload created for %s. Upload ID saved locally.",
                experiment_id,
            )

        except Exception as error:
            self.database.update_nomad_upload_state(
                experiment_uuid,
                "FAILED",
                error_message=str(error),
            )
            logger.exception(
                "Could not create NOMAD upload for %s. "
                "The watcher will retry after the configured interval.",
                experiment_id,
            )

    def synchronize_manifest(self, experiment_directory: str | Path) -> None:
        """Refresh the local manifest from the current SQLite state."""
        experiment_directory = Path(experiment_directory).resolve()
        experiment = self.database.get_experiment_by_path(
            experiment_directory
        )
        if experiment is None:
            logger.warning(
                "Cannot synchronize manifest; experiment is not registered: %s",
                experiment_directory,
            )
            return

        archive_states = self.database.get_archive_states(
            experiment["experiment_uuid"]
        )
        self.metadata_manager.synchronize_manifest(
            experiment_directory=experiment_directory,
            experiment=experiment,
            archive_states=archive_states,
        )

    def discover_experiment(self, experiment_directory):
        with self._lock:
            experiment_directory = Path(
                experiment_directory
            ).resolve()

            if not experiment_directory.is_dir():
                return False

            logger.info(
                "Discovering experiment: %s",
                experiment_directory.name,
            )

            metadata = self.metadata_manager.initialize(
                experiment_directory
            )

            is_new = self.database.register_experiment(
                experiment_uuid=metadata["experiment_uuid"],
                experiment_id=metadata["experiment_id"],
                local_path=str(experiment_directory),
            )

            if is_new:
                logger.info(
                    "New experiment registered: %s",
                    metadata["experiment_id"],
                )
            else:
                logger.debug(
                    "Experiment already registered: %s",
                    metadata["experiment_id"],
                )

            experiment = self.database.get_experiment_by_path(
                experiment_directory
            )
            if experiment is None:
                raise RuntimeError(
                    "Experiment registration succeeded but the record "
                    f"could not be retrieved: {experiment_directory}"
                )

            # NOMAD upload creation happens at discovery, not when the
            # first measurement archive happens to become ready.
            self._ensure_nomad_upload(experiment)

            # Reflect registration/upload state immediately in the manifest.
            self.synchronize_manifest(experiment_directory)
            return is_new

    def get_experiments(self):
        return self.database.get_all_experiments()
