
import logging
import signal
import sys

from pathlib import Path

from nomad_experiment_watcher.config import load_config

from nomad_experiment_watcher.database import (
    WatcherDatabase
)

from nomad_experiment_watcher.experiments.manager import (
    ExperimentManager
)

from nomad_experiment_watcher.experiments.archive_manager import (
    ArchiveManager
)

from nomad_experiment_watcher.monitoring.folder_monitor import (
    FolderMonitor
)


def setup_logging(config):

    config.log_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logging.basicConfig(
        level=getattr(
            logging,
            config.log_level,
            logging.INFO
        ),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=[
            logging.FileHandler(
                config.log_path,
                encoding="utf-8"
            ),
            logging.StreamHandler(sys.stdout)
        ]
    )


def main():

    project_root = Path(__file__).resolve().parents[2]

    config_path = (
        project_root / "config" / "config.yaml"
    )

    config = load_config(config_path)

    setup_logging(config)

    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("NOMAD Experiment Watcher")
    logger.info("=" * 60)

    logger.info(
        "Experimental directory: %s",
        config.experimental_data_directory
    )

    # -----------------------------------------------
    # Database
    # -----------------------------------------------

    database = WatcherDatabase(
        config.database_path
    )

    # -----------------------------------------------
    # Experiment manager
    # -----------------------------------------------

    experiment_manager = ExperimentManager(
        database=database,
        experiment_settings=config.experiment_settings
    )

    # -----------------------------------------------
    # Archive manager
    # -----------------------------------------------

    archive_settings = config.archive_settings

    archive_manager = ArchiveManager(

        database=database,

        output_directory=config.archive_output_directory,

        measurement_types=archive_settings.get(
            "measurement_types",
            {
                "RTP": ".rtpzip",
                "XRD": ".xrdzip",
                "Sulfurization": ".sulfurzip",
                "Hyperspectral": ".hyperzip"
            }
        ),

        quiet_period_seconds=archive_settings.get(
            "quiet_period_seconds",
            300
        ),

        stability_period_seconds=archive_settings.get(
            "stability_period_seconds",
            60
        ),

        compression_level=archive_settings.get(
            "compression_level",
            6
        )
    )

    # -----------------------------------------------
    # Folder monitor
    # -----------------------------------------------

    monitor = FolderMonitor(

        root_directory=config.experimental_data_directory,

        experiment_manager=experiment_manager,

        archive_manager=archive_manager,

        scan_interval_seconds=config.scan_interval_seconds,

        use_filesystem_events=config.use_filesystem_events
    )

    def shutdown_handler(signum, frame):

        logger.info("Shutdown signal received.")

        monitor.stop()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:

        monitor.start()

    except KeyboardInterrupt:

        logger.info("Keyboard interruption received.")

    finally:

        monitor.stop()

        logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()
