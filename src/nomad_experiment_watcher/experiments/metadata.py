
import json
import os
import tempfile
import uuid

from pathlib import Path
from datetime import datetime, timezone


def utc_now():

    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: dict):

    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temporary file in the same directory.
    # os.replace() then performs an atomic replacement.

    fd, temporary_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp"
    )

    try:

        with os.fdopen(fd, "w", encoding="utf-8") as file:

            json.dump(
                data,
                file,
                indent=4,
                ensure_ascii=False
            )

            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    except Exception:

        if os.path.exists(temporary_path):
            os.unlink(temporary_path)

        raise


def load_json(path: Path):

    with path.open("r", encoding="utf-8") as file:

        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a JSON object in {path}"
        )

    return data


class ExperimentMetadataManager:

    def __init__(self, experiment_settings: dict):

        self.settings = experiment_settings

    def initialize(self, experiment_directory: Path):

        details_path = (
            experiment_directory / "experiment_details.json"
        )

        manifest_path = (
            experiment_directory / "experiment_manifest.json"
        )

        details_exists = details_path.exists()
        manifest_exists = manifest_path.exists()

        # Load existing files without discarding researcher data.

        details = (
            load_json(details_path)
            if details_exists
            else {}
        )

        manifest = (
            load_json(manifest_path)
            if manifest_exists
            else {}
        )

        experiment_id = experiment_directory.name

        # Existing metadata must agree with its folder name.

        existing_id = details.get("experiment_id")

        if existing_id and existing_id != experiment_id:
            raise ValueError(
                f"Experiment ID '{existing_id}' does not match "
                f"folder name '{experiment_id}'."
            )

        manifest_id = manifest.get("experiment_id")

        if manifest_id and manifest_id != experiment_id:
            raise ValueError(
                f"Manifest experiment ID '{manifest_id}' "
                f"does not match folder name '{experiment_id}'."
            )

        # Resolve UUID from existing metadata.
        # Never generate a new UUID on every restart.

        details_uuid = details.get("experiment_uuid")
        manifest_uuid = manifest.get("experiment_uuid")

        if (
            details_uuid
            and manifest_uuid
            and details_uuid != manifest_uuid
        ):
            raise ValueError(
                "UUID mismatch between experiment_details.json "
                "and experiment_manifest.json."
            )

        experiment_uuid = (
            details_uuid
            or manifest_uuid
            or str(uuid.uuid4())
        )

        # Create missing scientific metadata fields.
        # Existing researcher values are preserved.

        details.setdefault("experiment_id", experiment_id)
        details.setdefault("experiment_uuid", experiment_uuid)
        details.setdefault("sample_id", experiment_id)
        details.setdefault(
            "title",
            f"Combinatorial thin film experiment {experiment_id}"
        )
        details.setdefault("description", "")
        details.setdefault("researcher", "")
        details.setdefault(
            "material_system",
            self.settings.get(
                "default_material_system",
                ""
            )
        )
        details.setdefault(
            "substrate",
            self.settings.get("default_substrate", "")
        )
        details.setdefault(
            "deposition_method",
            self.settings.get(
                "default_deposition_method",
                ""
            )
        )
        details.setdefault("deposition_date", "")
        details.setdefault(
            "measurements",
            self.settings.get("expected_measurements", [])
        )

        # Operational manifest.

        manifest.setdefault("schema_version", "1.0")
        manifest.setdefault("created_at", utc_now())
        manifest.setdefault("local_path", str(experiment_directory))

        manifest["experiment_id"] = experiment_id
        manifest["experiment_uuid"] = experiment_uuid

        manifest.setdefault(
            "nomad",
            {
                "upload_id": None,
                "upload_status": "NOT_CREATED",
                "processing_status": "NOT_STARTED"
            }
        )

        manifest.setdefault(
            "experiment_status",
            "DISCOVERED"
        )

        expected_measurements = details["measurements"]

        manifest.setdefault(
            "expected_measurements",
            expected_measurements
        )

        manifest.setdefault("measurements", {})

        for measurement in expected_measurements:

            manifest["measurements"].setdefault(
                measurement,
                {
                    "detected": False,
                    "uploaded": False,
                    "processed": False,
                    "entry_id": None
                }
            )

        # Only write when the files are new or have been
        # extended with missing identity/default fields.

        if not details_exists or details != load_json(details_path):
            atomic_write_json(details_path, details)

        if not manifest_exists or manifest != load_json(manifest_path):
            atomic_write_json(manifest_path, manifest)

        return {
            "experiment_id": experiment_id,
            "experiment_uuid": experiment_uuid,
            "details_path": details_path,
            "manifest_path": manifest_path
        }
