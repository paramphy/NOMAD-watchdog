
import logging
import threading

from pathlib import Path

from nomad_experiment_watcher.database import WatcherDatabase
from nomad_experiment_watcher.experiments.metadata import (
    ExperimentMetadataManager
)

logger = logging.getLogger(__name__)


class ExperimentManager:

    def __init__(self, database, experiment_settings):

        self.database = database

        self.metadata_manager = ExperimentMetadataManager(
            experiment_settings
        )

        self._lock = threading.RLock()

    def discover_experiment(self, experiment_directory):

        with self._lock:

            experiment_directory = Path(
                experiment_directory
            ).resolve()

            if not experiment_directory.is_dir():
                return False

            logger.info(
                "Discovering experiment: %s",
                experiment_directory.name
            )

            metadata = self.metadata_manager.initialize(
                experiment_directory
            )

            is_new = self.database.register_experiment(
                experiment_uuid=metadata["experiment_uuid"],
                experiment_id=metadata["experiment_id"],
                local_path=str(experiment_directory)
            )

            if is_new:
                logger.info(
                    "New experiment registered: %s",
                    metadata["experiment_id"]
                )
            else:
                logger.info(
                    "Experiment already registered: %s",
                    metadata["experiment_id"]
                )

            return is_new

    def get_experiments(self):

        return self.database.get_all_experiments()
