
"""
NOMAD uploader for NOMAD Experiment Watcher.

Responsibilities:
- Create NOMAD uploads.
- Upload individual files.
- Verify uploaded files.
- Upload multiple files sequentially.
- Retrieve upload information and entries.

Authentication and HTTP communication are handled by NomadClient.
"""

from __future__ import annotations

import logging
import time

from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


class NomadUploadError(Exception):
    """Raised when a NOMAD upload operation fails."""


class NomadUploader:
    """Handles file uploads to a NOMAD Oasis."""

    def __init__(
        self,
        client,
        logger=None,
        verification_settings=None,
    ):
        self.client = client
        self.logger = logger or logging.getLogger(__name__)

        self.verification_settings = verification_settings or {}

    # ---------------------------------------------------------
    # Validation helpers
    # ---------------------------------------------------------

    @staticmethod
    def _validate_upload_id(upload_id: str) -> str:
        if not isinstance(upload_id, str) or not upload_id.strip():
            raise ValueError("upload_id must be a non-empty string.")

        return upload_id.strip()

    @staticmethod
    def _validate_upload_name(upload_name: str) -> str:
        if not isinstance(upload_name, str) or not upload_name.strip():
            raise ValueError("upload_name must be a non-empty string.")

        return upload_name.strip()

    @staticmethod
    def _validate_remote_directory(remote_directory: str) -> str:

        if remote_directory is None:
            return ""

        if not isinstance(remote_directory, str):
            raise ValueError("remote_directory must be a string.")

        normalized = remote_directory.replace("\\", "/").strip("/")

        if not normalized:
            return ""

        path = PurePosixPath(normalized)

        if path.is_absolute() or any(
            part in (".", "..") for part in path.parts
        ):
            raise ValueError("Invalid remote directory.")

        return path.as_posix()

    @staticmethod
    def _extract_upload_id(response: Any) -> str:

        if not isinstance(response, dict):
            raise NomadUploadError(
                f"Unexpected NOMAD response: {response!r}"
            )

        upload_id = response.get("upload_id")

        if not upload_id and isinstance(response.get("data"), dict):
            upload_id = response["data"].get("upload_id")

        if not upload_id:
            raise NomadUploadError(
                "NOMAD response did not contain an upload_id."
            )

        return str(upload_id)

    # ---------------------------------------------------------
    # Create upload
    # ---------------------------------------------------------

    def create_upload(
        self,
        upload_name: str,
        auto_decompress: bool = False,
    ) -> str:

        upload_name = self._validate_upload_name(upload_name)

        self.logger.info(
            "Creating NOMAD upload: %s",
            upload_name,
        )

        response = self.client.request(
            method="POST",
            endpoint="/uploads",
            params={
                "upload_name": upload_name,
                "auto_decompress": auto_decompress,
            },
        )

        upload_id = self._extract_upload_id(response)

        self.logger.info(
            "NOMAD upload created. Upload ID: %s",
            upload_id,
        )

        return upload_id

    # ---------------------------------------------------------
    # Verify uploaded file
    # ---------------------------------------------------------

    def verify_uploaded_file(
        self,
        upload_id: str,
        filename: str,
        remote_directory: str = "",
        expected_size: int | None = None,
        initial_delay_seconds: float = 2,
        retry_interval_seconds: float = 3,
        timeout_seconds: float = 60,
    ) -> dict:

        upload_id = self._validate_upload_id(upload_id)

        remote_directory = self._validate_remote_directory(
            remote_directory
        )

        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty string.")

        if Path(filename).name != filename:
            raise ValueError("filename must contain only a filename.")

        if initial_delay_seconds < 0:
            raise ValueError("Initial delay cannot be negative.")

        if retry_interval_seconds <= 0:
            raise ValueError("Retry interval must be positive.")

        if timeout_seconds <= 0:
            raise ValueError("Timeout must be positive.")

        if expected_size is not None and expected_size < 0:
            raise ValueError("Expected file size cannot be negative.")

        encoded_upload_id = quote(upload_id, safe="")

        if remote_directory:
            encoded_directory = quote(remote_directory, safe="/")
            endpoint = (
                f"/uploads/{encoded_upload_id}"
                f"/rawdir/{encoded_directory}"
            )
        else:
            endpoint = (
                f"/uploads/{encoded_upload_id}/rawdir/"
            )

        self.logger.info(
            "Waiting for remote file verification: %s",
            filename,
        )

        start_time = time.monotonic()
        deadline = start_time + timeout_seconds

        time.sleep(
            min(initial_delay_seconds, timeout_seconds)
        )

        attempt = 0

        while time.monotonic() < deadline:

            attempt += 1

            response = self.client.request(
                method="GET",
                endpoint=endpoint,
            )

            directory_metadata = response.get(
                "directory_metadata", {}
            )

            contents = directory_metadata.get("content", [])

            for item in contents:

                if (
                    item.get("name") == filename
                    and item.get("is_file") is True
                ):

                    remote_size = item.get("size")

                    if (
                        expected_size is not None
                        and remote_size != expected_size
                    ):
                        self.logger.warning(
                            "Remote file size does not match. "
                            "Expected: %s, received: %s",
                            expected_size,
                            remote_size,
                        )
                        break

                    self.logger.info(
                        "Remote file verified: %s (%s bytes)",
                        filename,
                        remote_size,
                    )

                    return item

            elapsed = time.monotonic() - start_time

            self.logger.info(
                "File not visible yet. Attempt %d. "
                "Elapsed: %.1f seconds.",
                attempt,
                elapsed,
            )

            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            time.sleep(
                min(retry_interval_seconds, remaining)
            )

        raise NomadUploadError(
            f"File verification timed out after "
            f"{timeout_seconds} seconds: {filename}"
        )

    # ---------------------------------------------------------
    # Upload one file
    # ---------------------------------------------------------

    def upload_file(
        self,
        upload_id: str,
        local_path: str | Path,
        remote_directory: str = "",
        auto_decompress: bool = False,
    ) -> dict:

        upload_id = self._validate_upload_id(upload_id)
        local_path = Path(local_path)

        if not local_path.exists():
            raise FileNotFoundError(
                f"Local file does not exist: {local_path}"
            )

        if not local_path.is_file():
            raise ValueError(
                f"Expected a file: {local_path}"
            )

        remote_directory = self._validate_remote_directory(
            remote_directory
        )

        file_size_before = local_path.stat().st_size
        modified_before = local_path.stat().st_mtime_ns

        encoded_upload_id = quote(upload_id, safe="")

        if remote_directory:
            encoded_directory = quote(remote_directory, safe="/")
        else:
            encoded_directory = ""

        endpoint = (
            f"/uploads/{encoded_upload_id}/raw/{encoded_directory}"
        )

        self.logger.info(
            "Uploading file: %s (%d bytes)",
            local_path.name,
            file_size_before,
        )

        self.logger.info(
            "NOMAD destination: %s",
            remote_directory or "/",
        )

        try:

            with local_path.open("rb") as file_handle:

                response = self.client.request(
                    method="PUT",
                    endpoint=endpoint,
                    params={
                        "auto_decompress": auto_decompress,
                    },
                    files={
                        "file": (
                            local_path.name,
                            file_handle,
                            "application/octet-stream",
                        )
                    },
                )

            # Ensure the local file did not change during transfer.
            stat_after = local_path.stat()

            if (
                stat_after.st_size != file_size_before
                or stat_after.st_mtime_ns != modified_before
            ):
                raise NomadUploadError(
                    f"Local file changed during upload: {local_path}"
                )

            self.logger.info(
                "NOMAD accepted upload request: %s",
                local_path.name,
            )

            remote_metadata = None

            if self.verification_settings.get("enabled", True):

                remote_metadata = self.verify_uploaded_file(
                    upload_id=upload_id,
                    filename=local_path.name,
                    remote_directory=remote_directory,
                    expected_size=file_size_before,
                    initial_delay_seconds=self.verification_settings.get(
                        "initial_delay_seconds", 2
                    ),
                    retry_interval_seconds=self.verification_settings.get(
                        "retry_interval_seconds", 3
                    ),
                    timeout_seconds=self.verification_settings.get(
                        "timeout_seconds", 60
                    ),
                )

            return {
                "upload_response": response,
                "verified": remote_metadata is not None,
                "remote_metadata": remote_metadata,
            }

        except Exception:
            self.logger.exception(
                "Failed to upload file: %s",
                local_path,
            )
            raise

    # ---------------------------------------------------------
    # Upload multiple files
    # ---------------------------------------------------------

    def upload_files(
        self,
        upload_id: str,
        local_paths: Iterable[str | Path],
        remote_directory: str = "",
        auto_decompress: bool = False,
        fail_fast: bool = True,
    ) -> list[dict[str, Any]]:

        paths = [Path(path) for path in local_paths]

        if not paths:
            self.logger.warning("No files supplied for upload.")
            return []

        results = []

        self.logger.info(
            "Starting batch upload: %d files",
            len(paths),
        )

        for index, local_path in enumerate(paths, start=1):

            self.logger.info(
                "Uploading file %d/%d: %s",
                index,
                len(paths),
                local_path.name,
            )

            try:

                response = self.upload_file(
                    upload_id=upload_id,
                    local_path=local_path,
                    remote_directory=remote_directory,
                    auto_decompress=auto_decompress,
                )

                results.append({
                    "file": str(local_path),
                    "status": "success",
                    "response": response,
                    "error": None,
                })

            except Exception as error:

                self.logger.error(
                    "Upload failed for %s: %s",
                    local_path,
                    error,
                )

                results.append({
                    "file": str(local_path),
                    "status": "failed",
                    "response": None,
                    "error": str(error),
                })

                if fail_fast:
                    raise

        successful = sum(
            result["status"] == "success"
            for result in results
        )

        failed = len(results) - successful

        self.logger.info(
            "Batch upload finished. Successful: %d, Failed: %d",
            successful,
            failed,
        )

        return results

    # ---------------------------------------------------------
    # Retrieve upload information
    # ---------------------------------------------------------

    def get_upload_information(self, upload_id: str) -> dict:

        upload_id = self._validate_upload_id(upload_id)

        return self.client.request(
            method="GET",
            endpoint=f"/uploads/{quote(upload_id, safe='')}",
        )

    # ---------------------------------------------------------
    # Retrieve upload entries
    # ---------------------------------------------------------

    def get_upload_entries(self, upload_id: str) -> dict:

        upload_id = self._validate_upload_id(upload_id)

        return self.client.request(
            method="GET",
            endpoint=(
                f"/uploads/{quote(upload_id, safe='')}/entries"
            ),
        )
