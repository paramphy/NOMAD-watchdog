from __future__ import annotations

import logging
import signal
import sys

from pathlib import Path
from dotenv import load_dotenv

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

    Reads the base URL from config.yaml and the PAT
    from the environment.
    """

    nomad_client = None

    try:
        logger.info("Initializing NOMAD client...")

        # Read NOMAD configuration.
        nomad_settings = config.nomad_settings or {}

        base_url = nomad_settings.get("base_url")

        if not base_url or not str(base_url).strip():
            raise ValueError(
                "NOMAD base_url is missing. "
                "Check nomad.base_url in config/config.yaml."
            )

        base_url = str(base_url).strip().rstrip("/")

        # Do not log the PAT.
        logger.info(
            "Using configured NOMAD base URL: %s",
            base_url,
        )

        # IMPORTANT: Explicitly pass the configured URL.
        # The PAT is still read from NOMAD_PAT.
        nomad_client = NomadClient(
            base_url=base_url,
        )

        logger.info("NOMAD client initialized.")

        # --------------------------------------------------
        # Initialize uploader
        # --------------------------------------------------

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

        # --------------------------------------------------
        # Verify connectivity and authentication
        # --------------------------------------------------

        nomad_client.get_info()

        logger.info(
            "NOMAD server connection and authentication "
            "verified successfully."
        )

        return nomad_client, nomad_uploader

    except Exception:
        logger.exception(
            "NOMAD initialization failed."
        )

        if nomad_client is not None:
            try:
                nomad_client.close()
            except Exception:
                logger.exception(
                    "Could not close NOMAD client."
                )

        return None, None


    # ----------------------------------------------------------
    # Initialize uploader
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

        try:
            nomad_client.close()
        except Exception:
            logger.exception(
                "Could not close NOMAD client after initialization failure."
            )

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
            "The local watcher will continue. NOMAD operations "
            "will be retried during subsequent scans."
        )

    # Return the uploader even if the connectivity check failed.
    return nomad_client, nomad_uploader


# ==============================================================
# NOMAD GROUP SHARING CONFIGURATION
# ==============================================================

def get_coauthor_group_id(config, logger):
    """
    Read the Coauthor group ID from config.yaml.

    Coauthor and Reviewer permissions are deliberately kept
    separate. This function does not use reviewer_group_id
    as a fallback.
    """

    nomad_settings = config.nomad_settings or {}

    sharing_settings = nomad_settings.get(
        "sharing",
        {},
    )

    if not sharing_settings.get("enabled", False):
        logger.info(
            "Automatic NOMAD group sharing is disabled."
        )
        return None

    coauthor_group_id = sharing_settings.get(
        "coauthor_group_id"
    )

    if not coauthor_group_id:
        logger.warning(
            "Automatic group sharing is enabled, but "
            "coauthor_group_id is missing from config.yaml."
        )
        return None

    coauthor_group_id = str(coauthor_group_id).strip()

    if not coauthor_group_id:
        logger.warning(
            "The configured coauthor_group_id is empty."
        )
        return None

    logger.info(
        "Automatic NOMAD Coauthor group sharing is enabled."
    )

    logger.info(
        "Configured Coauthor group ID: %s",
        coauthor_group_id,
    )

    return coauthor_group_id


# ==============================================================
# MAIN APPLICATION
# ==============================================================

def main():

    # ----------------------------------------------------------
    # 1. Load project configuration
    # ----------------------------------------------------------

    project_root = Path(__file__).resolve().parents[2]

    # Load environment variables from the project root.
    env_path = project_root / ".env"
    
    if env_path.exists():
        load_dotenv(
            dotenv_path=env_path,
            override=False,
        )
    else:
        print(
            f"WARNING: .env file not found at {env_path}"
        )
    
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
        # 5. Configure automatic Coauthor group sharing
        # ------------------------------------------------------

        coauthor_group_id = get_coauthor_group_id(
            config=config,
            logger=logger,
        )

        if coauthor_group_id and not nomad_uploader:
            logger.warning(
                "A Coauthor group is configured, but the NOMAD "
                "uploader is unavailable. Group access cannot "
                "be assigned during this session."
            )

        # ------------------------------------------------------
        # 6. Initialize experiment manager
        # ------------------------------------------------------

        logger.info("Initializing experiment manager...")

        experiment_manager = ExperimentManager(
            database=database,

            experiment_settings=config.experiment_settings,

            # Required for creating and managing NOMAD uploads.
            nomad_uploader=nomad_uploader,

            # Assign Coauthor access immediately after an upload
            # is created, and retry for existing uploads when
            # an earlier assignment attempt failed.
            coauthor_group_id=coauthor_group_id,
        )

        logger.info(
            "Experiment manager initialized."
        )

        # ------------------------------------------------------
        # 7. Initialize archive manager
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

            # NOMAD uploader handles automatic measurement
            # archive uploads.
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
        # 8. Initialize folder monitor
        # ------------------------------------------------------

        logger.info("Initializing folder monitor...")

        monitor = FolderMonitor(
            root_directory=config.experimental_data_directory,

            experiment_manager=experiment_manager,

            archive_manager=archive_manager,

            scan_interval_seconds=config.scan_interval_seconds,

            use_filesystem_events=config.use_filesystem_events,
        )

        logger.info(
            "Folder monitor initialized."
        )

        # ------------------------------------------------------
        # 9. Shutdown handling
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
        # 10. Start watcher
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
            "Automatic NOMAD Coauthor sharing: %s",
            "ENABLED"
            if coauthor_group_id and nomad_uploader
            else "DISABLED",
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
        # 11. Stop folder monitor
        # ------------------------------------------------------

        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                logger.exception(
                    "Error while stopping folder monitor."
                )

        # ------------------------------------------------------
        # 12. Close NOMAD session
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
