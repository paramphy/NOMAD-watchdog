from __future__ import annotations

import logging
import os
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
from dotenv import load_dotenv

def setup_logging(config):
    """Configure file and console logging."""
    config.log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(config.log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def initialize_nomad(config, logger):
    """
    Initialize NOMAD using the server URL in config.yaml and PAT from
    the configured environment variable (normally loaded from .env).
    """
    settings = config.nomad_settings or {}
    base_url = settings.get("base_url")
    token_env_var = settings.get("token_env_var", "NOMAD_PAT")
    token = os.getenv(token_env_var)

    if not token:
        logger.warning(
            "NOMAD token is not configured. Expected environment variable "
            "%s. Local monitoring and archive generation will continue; "
            "automatic upload is disabled until the watcher is restarted "
            "with the secret configured.",
            token_env_var,
        )
        return None, None

    nomad_client = None
    try:
        logger.info("Initializing NOMAD client for %s", base_url)
        nomad_client = NomadClient(
            base_url=base_url,
            token=token,
            timeout=float(settings.get("request_timeout_seconds", 30)),
            verify_ssl=bool(settings.get("verify_ssl", True)),
        )

        uploader = NomadUploader(
            client=nomad_client,
            logger=logger,
            verification_settings=settings.get(
                "upload_verification", {}
            ),
        )

        try:
            nomad_client.get_info()
            logger.info("NOMAD server connection verified.")
        except Exception as error:
            # Keep the client: individual operations can retry when the
            # server/tunnel becomes available again.
            logger.warning(
                "NOMAD server is currently unreachable: %s. "
                "Uploads will be retried during later scans.",
                error,
            )

        return nomad_client, uploader

    except Exception:
        logger.exception("Could not initialize NOMAD client/uploader.")
        if nomad_client is not None:
            nomad_client.close()
        return None, None


def main():
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "config" / "config.yaml"
    config = load_config(config_path)

    # Load local secrets without overriding variables already supplied
    # by the shell or the Windows environment.
    nomad_settings = config.nomad_settings or {}
    env_file = Path(nomad_settings.get("env_file", ".env"))
    if not env_file.is_absolute():
        env_file = project_root / env_file
    load_dotenv(dotenv_path=env_file, override=False)

    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("=" * 65)
    logger.info("NOMAD EXPERIMENT WATCHER")
    logger.info("=" * 65)
    logger.info("Experimental directory: %s", config.experimental_data_directory)

    database = None
    nomad_client = None
    monitor = None

    try:
        logger.info("Initializing database...")
        database = WatcherDatabase(config.database_path)
        logger.info("Database initialized: %s", config.database_path)

        nomad_client, nomad_uploader = initialize_nomad(config, logger)
        logger.info(
            "Automatic NOMAD uploading: %s",
            "ENABLED" if nomad_uploader else "DISABLED",
        )

        experiment_manager = ExperimentManager(
            database=database,
            experiment_settings=config.experiment_settings,
            nomad_uploader=nomad_uploader,
            upload_retry_interval_seconds=nomad_settings.get(
                "upload_retry_interval_seconds", 300
            ),
        )

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
        nomad_settings = config.nomad_settings or {}

        sharing_settings = nomad_settings.get(
            "sharing",
            {},
        )

        reviewer_group_id = (
            sharing_settings.get("reviewer_group_id")
            if sharing_settings.get("enabled", False)
            else None
        )

        archive_manager = ArchiveManager(
            database=database,
            output_directory=config.archive_output_directory,
            measurement_types=measurement_types,
            quiet_period_seconds=archive_settings.get(
                "quiet_period_seconds", 300
            ),
            stability_period_seconds=archive_settings.get(
                "stability_period_seconds", 60
            ),
            compression_level=archive_settings.get(
                "compression_level", 6
            ),
            uploader=nomad_uploader,
            reviewer_group_id=reviewer_group_id,
        )
        logger.info(
            "Automatic NOMAD group sharing: %s",
            "ENABLED" if reviewer_group_id else "DISABLED",
        )       

        if reviewer_group_id:
            logger.info(
                "Configured reviewer group: %s",
                reviewer_group_id,
            )       

        monitor = FolderMonitor(
            root_directory=config.experimental_data_directory,
            experiment_manager=experiment_manager,
            archive_manager=archive_manager,
            scan_interval_seconds=config.scan_interval_seconds,
            use_filesystem_events=config.use_filesystem_events,
        )

        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received: %s", signum)
            if monitor is not None:
                monitor.stop()

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        logger.info("Local archive generation: ENABLED")
        logger.info(
            "Automatic NOMAD upload: %s",
            "ENABLED" if nomad_uploader else "DISABLED",
        )
        logger.info("Automatic publishing: DISABLED")
        monitor.start()

    except KeyboardInterrupt:
        logger.info("Keyboard interruption received.")

    except Exception:
        logger.exception("Experiment watcher encountered a fatal error.")
        raise

    finally:
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                logger.exception("Error while stopping folder monitor.")

        if nomad_client is not None:
            try:
                nomad_client.close()
                logger.info("NOMAD client connection closed.")
            except Exception:
                logger.exception("Error while closing NOMAD client.")

        logger.info("=" * 65)
        logger.info("WATCHER STOPPED")
        logger.info("=" * 65)


if __name__ == "__main__":
    main()
