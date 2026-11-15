
from __future__ import annotations

import logging
import signal
import sys

from pathlib import Path

from nomad_experiment_watcher.config import load_config
from nomad_experiment_watcher.database import WatcherDatabase
from nomad_experiment_watcher.experiments.manager import ExperimentManager
from nomad_experiment_watcher.experiments.archive_manager import ArchiveManager
from nomad_experiment_watcher.monitoring.folder_monitor import FolderMonitor
from nomad_experiment_watcher.nomad.client import NomadClient
from nomad_experiment_watcher.nomad.uploader import NomadUploader


# ==============================================================
# LOGGING
# ==============================================================

def setup_logging(config):
    """Configure file and console logging."""

    config.log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    logging.basicConfig(
        level=getattr(
            logging,
            config.log_level,
            logging.INFO,
        ),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=[
            logging.FileHandler(
                config.log_path,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ==============================================================
# NOMAD INITIALIZATION
# ==============================================================

def initialize_nomad(config, logger):
    """
    Initialize the NOMAD client and uploader.

    NOMAD is optional for local watcher operation.

    Returns:
        (nomad_client, nomad_uploader)

    If NOMAD credentials are unavailable, or initialization fails,
    returns (None, None).

    If the client initializes but the server is temporarily
    unreachable, the uploader is still returned. Upload attempts
    can then be retried by ArchiveManager during later scans.
    """

    nomad_client = None

    try:
        logger.info("Initializing NOMAD client...")

        # Reads NOMAD_PAT and optional NOMAD_URL from environment.
        nomad_client = NomadClient()

        logger.info("NOMAD client initialized.")

    except Exception as error:
        logger.warning(
            "NOMAD client could not be initialized: %s",
            error,
        )

        logger.warning(
            "Automatic uploading is disabled for this session. "
            "Local monitoring and archive generation will continue."
        )

        return None, None

    # ----------------------------------------------------------
    # Create uploader
    # ----------------------------------------------------------

    try:
        nomad_settings = config.nomad_settings or {}

        verification_settings = nomad_settings.get(
            "upload_verification",
            {},
        )

        nomad_uploader = NomadUploader(
            client=nomad_client,
            logger=logger,
            verification_settings=verification_settings,
        )

        logger.info("NOMAD uploader initialized.")

        logger.info(
            "Remote upload verification enabled: %s",
            verification_settings.get("enabled", True),
        )

    except Exception:
        logger.exception(
            "Could not initialize NOMAD uploader."
        )

        nomad_client.close()
        return None, None

    # ----------------------------------------------------------
    # Check server connectivity
    # ----------------------------------------------------------

    try:
        nomad_client.get_info()

        logger.info(
            "NOMAD server connection verified successfully."
        )

    except Exception as error:
        logger.warning(
            "NOMAD server is currently unreachable: %s",
            error,
        )

        logger.warning(
            "The local watcher will continue. "
            "NOMAD uploads will be attempted again during "
            "subsequent archive scans."
        )

    # Return uploader even if the connectivity check failed.
    # The same client session can make future requests.
    return nomad_client, nomad_uploader


# ==============================================================
# MAIN APPLICATION
# ==============================================================

def main():

    # ----------------------------------------------------------
    # 1. Project configuration
    # ----------------------------------------------------------

    project_root = Path(__file__).resolve().parents[2]

    config_path = (
        project_root / "config" / "config.yaml"
    )

    config = load_config(config_path)

    setup_logging(config)

    logger = logging.getLogger(__name__)

    logger.info("=" * 65)
    logger.info("NOMAD EXPERIMENT WATCHER")
    logger.info("=" * 65)

    logger.info(
        "Experimental directory: %s",
        config.experimental_data_directory,
    )

    # ----------------------------------------------------------
    # 2. Initialize application resources
    # ----------------------------------------------------------

    database = None
    nomad_client = None
    nomad_uploader = None
    monitor = None

    try:

        # ------------------------------------------------------
        # 3. Initialize database
        # ------------------------------------------------------

        logger.info("Initializing database...")

        database = WatcherDatabase(
            config.database_path
        )

        logger.info(
            "Database initialized successfully: %s",
            config.database_path,
        )

        # ------------------------------------------------------
        # 4. Initialize NOMAD
        # ------------------------------------------------------

        # NOMAD failure must not prevent local monitoring.
        nomad_client, nomad_uploader = initialize_nomad(
            config=config,
            logger=logger,
        )

        if nomad_uploader is not None:
            logger.info(
                "Automatic NOMAD uploading is enabled."
            )
        else:
            logger.warning(
                "Automatic NOMAD uploading is disabled."
            )

        # ------------------------------------------------------
        # 5. Initialize experiment manager
        # ------------------------------------------------------

        logger.info("Initializing experiment manager...")

        experiment_manager = ExperimentManager(
            database=database,
            experiment_settings=config.experiment_settings,
        )

        logger.info("Experiment manager initialized.")

        # ------------------------------------------------------
        # 6. Initialize archive manager
        # ------------------------------------------------------

        logger.info("Initializing archive manager...")

        archive_settings = config.archive_settings

        measurement_types = archive_settings.get(
            "measurement_types",
            {
                "RTP": ".rtpzip",
                "XRD": ".xrdzip",
                "Sulfurization": ".sulfurzip",
                "Hyperspectral": ".hyperzip",
            },
        )

        archive_manager = ArchiveManager(
            database=database,

            output_directory=config.archive_output_directory,

            measurement_types=measurement_types,

            quiet_period_seconds=archive_settings.get(
                "quiet_period_seconds",
                300,
            ),

            stability_period_seconds=archive_settings.get(
                "stability_period_seconds",
                60,
            ),

            compression_level=archive_settings.get(
                "compression_level",
                6,
            ),

            # This is the connection that enables automatic uploads.
            # If NOMAD is unavailable, this is None and local
            # archive generation still works.
            uploader=nomad_uploader,
        )

        logger.info(
            "Archive manager initialized."
        )

        logger.info(
            "Configured measurement types: %s",
            ", ".join(measurement_types.keys()),
        )

        # ------------------------------------------------------
        # 7. Initialize folder monitor
        # ------------------------------------------------------

        logger.info("Initializing folder monitor...")

        monitor = FolderMonitor(
            root_directory=config.experimental_data_directory,

            experiment_manager=experiment_manager,

            archive_manager=archive_manager,

            scan_interval_seconds=config.scan_interval_seconds,

            use_filesystem_events=config.use_filesystem_events,
        )

        logger.info("Folder monitor initialized.")

        # ------------------------------------------------------
        # 8. Shutdown handling
        # ------------------------------------------------------

        def shutdown_handler(signum, frame):
            logger.info(
                "Shutdown signal received: %s",
                signum,
            )

            if monitor is not None:
                monitor.stop()

        signal.signal(
            signal.SIGINT,
            shutdown_handler,
        )

        signal.signal(
            signal.SIGTERM,
            shutdown_handler,
        )

        # ------------------------------------------------------
        # 9. Start watcher
        # ------------------------------------------------------

        logger.info("=" * 65)
        logger.info("STARTING EXPERIMENT WATCHER")
        logger.info("=" * 65)

        logger.info(
            "Local archive generation: ENABLED"
        )

        logger.info(
            "Automatic NOMAD upload: %s",
            "ENABLED" if nomad_uploader else "DISABLED",
        )

        logger.info(
            "Automatic publishing: DISABLED"
        )

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

        # ------------------------------------------------------
        # 10. Stop folder monitor
        # ------------------------------------------------------

        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                logger.exception(
                    "Error while stopping folder monitor."
                )

        # ------------------------------------------------------
        # 11. Close NOMAD session
        # ------------------------------------------------------

        if nomad_client is not None:
            try:
                nomad_client.close()

                logger.info(
                    "NOMAD client connection closed."
                )

            except Exception:
                logger.exception(
                    "Error while closing NOMAD client."
                )

        logger.info("=" * 65)
        logger.info("WATCHER STOPPED")
        logger.info("=" * 65)


# ==============================================================
# ENTRY POINT
# ==============================================================

if __name__ == "__main__":
    main()
