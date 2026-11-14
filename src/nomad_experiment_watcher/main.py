
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

from nomad_experiment_watcher.nomad.client import (
    NomadClient
)

from nomad_experiment_watcher.nomad.uploader import (
    NomadUploader
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
    # Initialize resources
    # -----------------------------------------------

    nomad_client = None
    monitor = None

    try:

        # -------------------------------------------
        # Database
        # -------------------------------------------

        database = WatcherDatabase(
            config.database_path
        )

        logger.info("Database initialized.")

        # -------------------------------------------
        # NOMAD client
        # -------------------------------------------

        logger.info("Initializing NOMAD client...")

        nomad_client = NomadClient()

        # Check that NOMAD is reachable and the
        # authentication token is accepted.

        nomad_info = nomad_client.get_info()

        logger.info(
            "Connected to NOMAD successfully."
        )

        if isinstance(nomad_info, dict):

            logger.info(
                "NOMAD server information retrieved."
            )

        # -------------------------------------------
        # NOMAD uploader
        # -------------------------------------------

        nomad_settings = config.nomad_settings

        verification_settings = nomad_settings.get(
            "upload_verification",
            {}
        )

        nomad_uploader = NomadUploader(

            client=nomad_client,

            logger=logger,

            verification_settings=verification_settings
        )

        logger.info(
            "NOMAD uploader initialized."
        )

        logger.info(
            "Upload verification enabled: %s",
            verification_settings.get("enabled", True)
        )

        # -------------------------------------------
        # Experiment manager
        # -------------------------------------------

        experiment_manager = ExperimentManager(

            database=database,

            experiment_settings=config.experiment_settings
        )

        logger.info("Experiment manager initialized.")

        # -------------------------------------------
        # Archive manager
        # -------------------------------------------

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
            ),

            # Connect NOMAD uploader to ArchiveManager.
            uploader=nomad_uploader
        )

        logger.info(
            "Archive manager initialized with NOMAD uploader."
        )

        # -------------------------------------------
        # Folder monitor
        # -------------------------------------------

        monitor = FolderMonitor(

            root_directory=config.experimental_data_directory,

            experiment_manager=experiment_manager,

            archive_manager=archive_manager,

            scan_interval_seconds=config.scan_interval_seconds,

            use_filesystem_events=config.use_filesystem_events
        )

        logger.info(
            "Folder monitor initialized."
        )

        # -------------------------------------------
        # Shutdown handler
        # -------------------------------------------

        def shutdown_handler(signum, frame):

            logger.info(
                "Shutdown signal received."
            )

            if monitor is not None:
                monitor.stop()

        signal.signal(
            signal.SIGINT,
            shutdown_handler
        )

        signal.signal(
            signal.SIGTERM,
            shutdown_handler
        )

        # -------------------------------------------
        # Start watcher
        # -------------------------------------------

        logger.info("=" * 60)
        logger.info("Starting experiment watcher...")
        logger.info("=" * 60)

        monitor.start()

    except KeyboardInterrupt:

        logger.info(
            "Keyboard interruption received."
        )

    except Exception:

        logger.exception(
            "Experiment watcher encountered a fatal error."
        )

        raise

    finally:

        # -------------------------------------------
        # Stop folder monitor
        # -------------------------------------------

        if monitor is not None:

            monitor.stop()

        # -------------------------------------------
        # Close NOMAD HTTP session
        # -------------------------------------------

        if nomad_client is not None:

            nomad_client.close()

            logger.info(
                "NOMAD client connection closed."
            )

        logger.info(
            "Watcher stopped."
        )


if __name__ == "__main__":
    main()
