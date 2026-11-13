
"""
NOMAD uploader for NOMAD Experiment Watcher.

Responsibilities:
- Create an upload in NOMAD.
- Upload individual files into an existing upload.
- Upload multiple files sequentially.
- Retrieve upload information.
- Validate local files and remote paths.
- Provide useful logging and error messages.

Authentication and HTTP communication are handled by NomadClient.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


class NomadUploadError(Exception):
    """Raised when a NOMAD upload operation fails."""


class NomadUploader:
    """
    Handles file uploads to a NOMAD Oasis.

    Parameters
    ----------
    client:
        An initialized NomadClient instance.
    logger:
        Optional logger instance.
    """

    def __init__(self, client, logger=None):
        self.client = client
        self.logger = logger or logging.getLogger(__name__)

    # ---------------------------------------------------------
    # Internal validation helpers
    # ---------------------------------------------------------

    @staticmethod
    def _validate_upload_id(upload_id: str) -> str:
        """Validate and normalize a NOMAD upload ID."""

        if not isinstance(upload_id, str) or not upload_id.strip():
            raise ValueError("upload_id must be a non-empty string.")

        return upload_id.strip()

    @staticmethod
    def _validate_upload_name(upload_name: str) -> str:
        """Validate the upload name."""

        if not isinstance(upload_name, str) or not upload_name.strip():
            raise ValueError("upload_name must be a non-empty string.")

        return upload_name.strip()

    @staticmethod
    def _validate_remote_directory(remote_directory: str) -> str:
        """
        Validate a directory path inside a NOMAD upload.

        NOMAD uses forward slashes for paths, including on Windows.
        Empty string means the root directory.
        """

        if remote_directory is None:
            return ""

        if not isinstance(remote_directory, str):
            raise ValueError("remote_directory must be a string.")

        normalized = remote_directory.replace("\\", "/").strip("/")

        if not normalized:
            return ""

        path = PurePosixPath(normalized)

        if path.is_absolute():
            raise ValueError("Absolute remote paths are not allowed.")

        if any(part in ("..", ".") for part in path.parts):
            raise ValueError("Remote directory cannot contain '.' or '..'.")

        return path.as_posix()

    @staticmethod
    def _extract_upload_id(response: Any) -> str:
        """
        Extract upload_id from different possible NOMAD response layouts.
        """

        if not isinstance(response, dict):
            raise NomadUploadError(
                f"Unexpected response while creating upload: {response!r}"
            )

        upload_id = response.get("upload_id")

        if not upload_id and isinstance(response.get("data"), dict):
            upload_id = response["data"].get("upload_id")

        if not upload_id:
            raise NomadUploadError(
                "NOMAD response did not contain an upload_id. "
                f"Response: {response!r}"
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
        """
        Create a new empty NOMAD upload.

        Parameters
        ----------
        upload_name:
            Name assigned to the NOMAD upload.

        auto_decompress:
            Whether NOMAD should automatically decompress uploaded
            ZIP or TAR files. Defaults to False.

        Returns
        -------
        str
            Newly created NOMAD upload ID.
        """

        upload_name = self._validate_upload_name(upload_name)

        self.logger.info(
            "Creating NOMAD upload: %s",
            upload_name,
        )

        try:
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
                "NOMAD upload created successfully. Upload ID: %s",
                upload_id,
            )

            return upload_id

        except Exception:
            self.logger.exception(
                "Failed to create NOMAD upload: %s",
                upload_name,
            )
            raise

    # ---------------------------------------------------------
    # Upload one file
    # ---------------------------------------------------------

    def upload_file(
        self,
        upload_id: str,
        local_path: str | Path,
        remote_directory: str = "",
        auto_decompress: bool = False,
    ) -> Any:
        """
        Upload one local file into an existing NOMAD upload.

        Uses:
            POST /uploads/{upload_id}/raw/{remote_directory}

        Multipart field:
            file

        Parameters
        ----------
        upload_id:
            Existing NOMAD upload ID.

        local_path:
            Path to the local file.

        remote_directory:
            Destination directory inside the NOMAD upload.
            Empty string means root directory.

        auto_decompress:
            Whether NOMAD should decompress ZIP/TAR files.
            Defaults to False to preserve the archive itself.

        Returns
        -------
        dict or str
            NOMAD API response.
        """

        upload_id = self._validate_upload_id(upload_id)
        local_path = Path(local_path)

        if not local_path.exists():
            raise FileNotFoundError(
                f"Local file does not exist: {local_path}"
            )

        if not local_path.is_file():
            raise ValueError(
                f"Expected a file, received: {local_path}"
            )

        remote_directory = self._validate_remote_directory(
            remote_directory
        )

        file_size_before = local_path.stat().st_size
        modified_before = local_path.stat().st_mtime_ns

        # Construct the NOMAD endpoint.
        encoded_upload_id = quote(upload_id, safe="")

        if remote_directory:
            encoded_directory = quote(remote_directory, safe="/")
        else:
            encoded_directory = ""

        endpoint = (
            f"/uploads/{encoded_upload_id}/raw/{encoded_directory}"
        )

        params = {
            "auto_decompress": auto_decompress,
        }

        self.logger.info(
            "Uploading file: %s (%s bytes)",
            local_path.name,
            file_size_before,
        )

        self.logger.info(
            "NOMAD destination: upload=%s, directory=%s",
            upload_id,
            remote_directory or "/",
        )

        try:
            with local_path.open("rb") as file_handle:

                response = self.client.request(
                    method="PUT",
                    endpoint=endpoint,
                    params=params,
                    files={
                        "file": (
                            local_path.name,
                            file_handle,
                            "application/octet-stream",
                        )
                    },
                )

            # Check whether the local file changed during transfer.
            stat_after = local_path.stat()

            if (
                stat_after.st_size != file_size_before
                or stat_after.st_mtime_ns != modified_before
            ):
                raise NomadUploadError(
                    f"Local file changed during upload: {local_path}. "
                    "The remote copy may need to be replaced."
                )

            self.logger.info(
                "File uploaded successfully: %s",
                local_path.name,
            )

            return response

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
        """
        Upload multiple files sequentially.

        Parameters
        ----------
        upload_id:
            Existing NOMAD upload ID.

        local_paths:
            Iterable of local file paths.

        remote_directory:
            Destination directory inside NOMAD.

        auto_decompress:
            Whether NOMAD should decompress ZIP/TAR files.

        fail_fast:
            If True, stop immediately when one upload fails.
            If False, continue and record failures.

        Returns
        -------
        list of dict
            Result for each file.
        """

        paths = [Path(path) for path in local_paths]

        if not paths:
            self.logger.warning("No files supplied for upload.")
            return []

        self.logger.info(
            "Starting batch upload: %d files",
            len(paths),
        )

        results = []

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
                    self.logger.error(
                        "Stopping batch upload because fail_fast=True."
                    )
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
        """
        Retrieve metadata and status for an existing NOMAD upload.
        """

        upload_id = self._validate_upload_id(upload_id)

        self.logger.info(
            "Retrieving NOMAD upload information: %s",
            upload_id,
        )

        try:
            return self.client.request(
                method="GET",
                endpoint=f"/uploads/{quote(upload_id, safe='')}",
            )

        except Exception:
            self.logger.exception(
                "Could not retrieve upload information: %s",
                upload_id,
            )
            raise

    # ---------------------------------------------------------
    # Retrieve upload entries
    # ---------------------------------------------------------

    def get_upload_entries(self, upload_id: str) -> dict:
        """
        Retrieve entries associated with an existing upload.
        """

        upload_id = self._validate_upload_id(upload_id)

        try:
            return self.client.request(
                method="GET",
                endpoint=(
                    f"/uploads/{quote(upload_id, safe='')}/entries"
                ),
            )

        except Exception:
            self.logger.exception(
                "Could not retrieve entries for upload: %s",
                upload_id,
            )
            raise
