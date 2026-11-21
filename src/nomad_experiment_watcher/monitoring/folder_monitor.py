
import logging
import threading

from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class ExperimentFolderHandler(FileSystemEventHandler):

    def __init__(self, experiment_manager, root_directory):

        self.experiment_manager = experiment_manager
        self.root_directory = Path(root_directory).resolve()

    def process_directory(self, path):

        path = Path(path).resolve()

        if path.parent != self.root_directory:
            return

        if not path.is_dir():
            return

        if path.name.startswith("."):
            return

        try:

            self.experiment_manager.discover_experiment(path)

        except Exception:

            logger.exception(
                "Failed to initialize experiment: %s",
                path
            )

    def on_created(self, event):

        if event.is_directory:
            self.process_directory(event.src_path)

    def on_moved(self, event):

        if event.is_directory:
            self.process_directory(event.dest_path)


class FolderMonitor:

    def __init__(
        self,
        root_directory,
        experiment_manager,
        archive_manager=None,
        scan_interval_seconds=30,
        use_filesystem_events=True
    ):

        self.root_directory = Path(
            root_directory
        ).resolve()

        self.experiment_manager = experiment_manager
        self.archive_manager = archive_manager

        self.scan_interval_seconds = scan_interval_seconds
        self.use_filesystem_events = use_filesystem_events

        self.stop_event = threading.Event()
        self.observer = None

    def scan_directory(self):

        logger.info(
            "Scanning experimental directory: %s",
            self.root_directory
        )

        try:

            directories = sorted(
                path for path in self.root_directory.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )

        except OSError:

            logger.exception(
                "Unable to scan experimental directory."
            )

            return

        for directory in directories:

            try:

                self.experiment_manager.discover_experiment(
                    directory
                )

                # Automatically generate/update measurement archives.

                if self.archive_manager:
                    self.archive_manager.scan_experiment(directory)

                # The manifest is a local, human-readable projection
                # of the authoritative SQLite state. This updates it
                # immediately after source detection and upload attempts.
                self.experiment_manager.synchronize_manifest(directory)

            except Exception:

                logger.exception(
                    "Error processing experiment folder: %s",
                    directory
                )

    def start(self):

        logger.info("Starting experiment folder monitor.")

        if self.use_filesystem_events:

            handler = ExperimentFolderHandler(
                self.experiment_manager,
                self.root_directory
            )

            self.observer = Observer()

            self.observer.schedule(
                handler,
                str(self.root_directory),
                recursive=False
            )

            self.observer.start()

            logger.info(
                "Filesystem event monitoring enabled."
            )

        self.scan_directory()

        while not self.stop_event.wait(
            self.scan_interval_seconds
        ):

            self.scan_directory()

    def stop(self):

        logger.info("Stopping folder monitor.")

        self.stop_event.set()

        if self.observer:

            self.observer.stop()
            self.observer.join(timeout=10)
