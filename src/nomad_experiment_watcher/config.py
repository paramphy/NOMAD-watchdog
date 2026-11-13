
from pathlib import Path
from dataclasses import dataclass

import yaml


@dataclass
class WatcherConfig:

    project_root: Path
    experimental_data_directory: Path
    database_path: Path
    log_path: Path

    scan_interval_seconds: int
    use_filesystem_events: bool
    log_level: str

    experiment_settings: dict

    archive_output_directory: Path
    archive_settings: dict


def load_config(config_path: str | Path) -> WatcherConfig:

    config_path = Path(config_path).resolve()
    project_root = config_path.parent.parent

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    monitoring = data.get("monitoring", {})
    database = data.get("database", {})
    logging_config = data.get("logging", {})
    archive = data.get("archive", {})

    experimental_directory = Path(
        monitoring["experimental_data_directory"]
    ).expanduser()

    if not experimental_directory.is_absolute():
        experimental_directory = (
            project_root / experimental_directory
        )

    database_path = Path(
        database.get("path", "database/watcher.db")
    )

    if not database_path.is_absolute():
        database_path = project_root / database_path

    log_path = Path(
        logging_config.get("path", "logs/watcher.log")
    )

    if not log_path.is_absolute():
        log_path = project_root / log_path

    archive_output_directory = Path(
        archive.get("output_directory", "archive_cache")
    )

    if not archive_output_directory.is_absolute():
        archive_output_directory = (
            project_root / archive_output_directory
        )

    scan_interval = int(
        monitoring.get("scan_interval_seconds", 30)
    )

    if scan_interval < 1:
        raise ValueError(
            "scan_interval_seconds must be at least 1."
        )

    if not experimental_directory.is_dir():
        raise NotADirectoryError(
            f"Experimental directory does not exist: "
            f"{experimental_directory}"
        )

    return WatcherConfig(

        project_root=project_root,

        experimental_data_directory=experimental_directory,

        database_path=database_path,

        log_path=log_path,

        scan_interval_seconds=scan_interval,

        use_filesystem_events=bool(
            monitoring.get("use_filesystem_events", True)
        ),

        log_level=logging_config.get(
            "level", "INFO"
        ).upper(),

        experiment_settings=data.get(
            "experiment", {}
        ),

        archive_output_directory=archive_output_directory,

        archive_settings=archive
    )
