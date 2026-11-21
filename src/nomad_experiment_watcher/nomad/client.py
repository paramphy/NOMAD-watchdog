
"""
NOMAD REST API client.

Responsibilities:
- Authenticate using a NOMAD Personal Access Token (PAT).
- Send HTTP requests to the NOMAD REST API.
- Handle responses and API errors consistently.
- Provide convenience methods for common GET endpoints.

Upload creation and file transfer are implemented by NomadUploader.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlsplit

import requests


DEFAULT_NOMAD_URL = None


class NomadAPIError(Exception):
    """Raised when the NOMAD API returns an unsuccessful response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_data: Any = None,
    ):
        super().__init__(message)

        self.status_code = status_code
        self.response_data = response_data


class NomadClient:
    """
    Client for communicating with a NOMAD Oasis REST API.

    Parameters
    ----------
    base_url:
        NOMAD API base URL.

    token:
        Personal Access Token. If omitted, NOMAD_PAT is read
        from the environment.

    timeout:
        Maximum time in seconds to wait for an API response.

    verify_ssl:
        Whether to verify the HTTPS certificate.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30,
        verify_ssl: bool = True,
    ):

        # --------------------------------------------------
        # 1. Read configuration
        # --------------------------------------------------

        configured_url = base_url or os.getenv("NOMAD_URL") or DEFAULT_NOMAD_URL
        if not configured_url:
            raise ValueError(
                "NOMAD base_url is not configured. Set nomad.base_url "
                "in config/config.yaml."
            )

        self.base_url = configured_url.rstrip("/")
        self.token = token or os.getenv("NOMAD_PAT")

        self.timeout = timeout
        self.verify_ssl = verify_ssl

        # --------------------------------------------------
        # 2. Validate configuration
        # --------------------------------------------------

        if not self.token:
            raise ValueError(
                "NOMAD PAT is not configured. Check the configured "
                "token environment variable or the local .env file."
            )

        parsed_url = urlsplit(self.base_url)

        if parsed_url.scheme not in ("https", "http"):
            raise ValueError(
                "NOMAD URL must start with https:// or http://."
            )

        if not parsed_url.netloc:
            raise ValueError("Invalid NOMAD API base URL.")

        if parsed_url.scheme != "https":
            if parsed_url.hostname not in ("localhost", "127.0.0.1"):
                raise ValueError(
                    "HTTPS is required for remote NOMAD servers."
                )

        # --------------------------------------------------
        # 3. Create reusable HTTP session
        # --------------------------------------------------

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

        # --------------------------------------------------
        # 4. Logging-free configuration summary
        # --------------------------------------------------

        # Never print or log the token.
        self.api_version = "v1"

    # ======================================================
    # General request method
    # ======================================================

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        json: dict | list | None = None,
        data: Any = None,
        files: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """
        Send an HTTP request to NOMAD.

        Parameters
        ----------
        method:
            HTTP method: GET, POST, PUT, DELETE, etc.

        endpoint:
            API endpoint, for example '/users/me'.

        params:
            URL query parameters.

        json:
            JSON request body.

        data:
            Raw request data.

        files:
            Files for multipart uploads.

        timeout:
            Optional request-specific timeout.

        Returns
        -------
        Parsed JSON response, or response text if JSON
        decoding is not possible.
        """

        method = method.upper()

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        request_timeout = (
            timeout if timeout is not None else self.timeout
        )

        try:

            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                data=data,
                files=files,
                timeout=request_timeout,
                verify=self.verify_ssl,
            )

        except requests.exceptions.Timeout as error:

            raise NomadAPIError(
                f"NOMAD request timed out: {method} {endpoint}"
            ) from error

        except requests.exceptions.ConnectionError as error:

            raise NomadAPIError(
                f"Could not connect to NOMAD: {method} {endpoint}"
            ) from error

        except requests.exceptions.RequestException as error:

            raise NomadAPIError(
                f"NOMAD request failed: {method} {endpoint}"
            ) from error

        # --------------------------------------------------
        # Parse response
        # --------------------------------------------------

        if not response.content:
            response_data = None

        else:
            try:
                response_data = response.json()

            except ValueError:
                response_data = response.text

        # --------------------------------------------------
        # Handle unsuccessful HTTP responses
        # --------------------------------------------------

        if not response.ok:

            if isinstance(response_data, dict):
                detail = response_data.get(
                    "detail",
                    response_data
                )
            else:
                detail = response_data

            raise NomadAPIError(
                message=(
                    f"NOMAD API returned HTTP "
                    f"{response.status_code}: {detail}"
                ),
                status_code=response.status_code,
                response_data=response_data,
            )

        return response_data

    # ======================================================
    # Convenience GET methods
    # ======================================================

    def get_info(self) -> Any:
        """Retrieve NOMAD backend information."""

        return self.request(
            "GET",
            "/info"
        )

    def get_current_user(self) -> Any:
        """Retrieve information about the authenticated user."""

        return self.request(
            "GET",
            "/users/me"
        )

    def list_uploads(
        self,
        params: dict | None = None,
    ) -> Any:
        """Retrieve uploads belonging to the authenticated user."""

        return self.request(
            "GET",
            "/uploads",
            params=params,
        )

    def get_upload(
        self,
        upload_id: str,
    ) -> Any:
        """Retrieve information about a particular upload."""

        return self.request(
            "GET",
            f"/uploads/{quote(upload_id, safe='')}",
        )

    def get_upload_entries(
        self,
        upload_id: str,
        params: dict | None = None,
    ) -> Any:
        """Retrieve entries belonging to an upload."""

        return self.request(
            "GET",
            f"/uploads/{quote(upload_id, safe='')}/entries",
            params=params,
        )

    # ======================================================
    # Session management
    # ======================================================

    def close(self) -> None:
        """Close the underlying HTTP session."""

        self.session.close()

    def __enter__(self) -> "NomadClient":
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:

        self.close()
