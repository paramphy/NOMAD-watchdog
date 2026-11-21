
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml
from dotenv import load_dotenv


# ============================================================
# PROJECT CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

# Add src to Python's import path.
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from nomad_experiment_watcher.nomad.client import (
    NomadClient,
    NomadAPIError,
)

from nomad_experiment_watcher.nomad.uploader import (
    NomadUploader,
)


# Existing Uppsala group UUID.
GROUP_ID = "XkD92ekBRHyPLX8vD3gPdA"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("group_sharing_test")


# ============================================================
# CONFIGURATION HELPERS
# ============================================================

def normalize_base_url(base_url: str) -> str:
    """
    Normalize a NOMAD API URL.

    Removes duplicate slashes from the URL path while
    preserving the https:// scheme.
    """

    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("NOMAD base_url is missing or invalid.")

    parsed = urlsplit(base_url.strip())

    normalized_path = re.sub(
        r"/{2,}",
        "/",
        parsed.path,
    ).rstrip("/")

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.query,
            parsed.fragment,
        )
    )


def load_settings() -> tuple[str, str]:
    """
    Load NOMAD URL from config.yaml and PAT from .env.
    """

    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {CONFIG_PATH}"
        )

    # Load environment variables without displaying secrets.
    load_dotenv(ENV_PATH, override=False)

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    nomad_settings = config.get("nomad", {})

    base_url = nomad_settings.get("base_url")

    if not base_url:
        raise ValueError(
            "NOMAD base_url is not configured in config/config.yaml."
        )

    base_url = normalize_base_url(base_url)

    token = os.getenv("NOMAD_PAT")

    if not token:
        raise ValueError(
            "NOMAD_PAT is unavailable. Check the project .env file "
            "or your Windows environment variables."
        )

    return base_url, token


# ============================================================
# RESPONSE HELPERS
# ============================================================

def find_upload_id(data, target_id: str) -> bool:
    """
    Recursively search a NOMAD response for an upload ID.
    """

    if isinstance(data, dict):

        if data.get("upload_id") == target_id:
            return True

        for value in data.values():
            if find_upload_id(value, target_id):
                return True

    elif isinstance(data, list):

        for item in data:
            if find_upload_id(item, target_id):
                return True

    return False


def extract_reviewer_groups(response) -> list[str] | None:
    """
    Extract reviewer_groups from common NOMAD response layouts.
    """

    if not isinstance(response, dict):
        return None

    data = response.get("data", response)

    if not isinstance(data, dict):
        return None

    metadata = data.get("metadata", data)

    if not isinstance(metadata, dict):
        return None

    groups = metadata.get("reviewer_groups")

    if groups is None:
        return []

    if isinstance(groups, list):
        return groups

    return None


# ============================================================
# MAIN TEST
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Test NOMAD reviewer-group sharing."
    )

    parser.add_argument(
        "--upload-id",
        required=True,
        help="Existing NOMAD upload ID.",
    )

    args = parser.parse_args()

    upload_id = args.upload_id.strip()

    if not upload_id:
        parser.error("Upload ID cannot be empty.")

    client = None

    try:

        logger.info("=" * 60)
        logger.info("Testing NOMAD group sharing")
        logger.info("=" * 60)

        # ----------------------------------------------------
        # 1. Load configuration
        # ----------------------------------------------------

        base_url, token = load_settings()

        logger.info("Configuration loaded.")
        logger.info("API base URL: %s", base_url)
        logger.info("Upload ID: %s", upload_id)
        logger.info("Upload ID length: %d", len(upload_id))
        logger.info("Reviewer group ID: %s", GROUP_ID)

        # ----------------------------------------------------
        # 2. Initialize NOMAD client
        # ----------------------------------------------------

        client = NomadClient(
            base_url=base_url,
            token=token,
            timeout=45,
        )

        # ----------------------------------------------------
        # 3. Test server connectivity
        # ----------------------------------------------------

        logger.info("Checking NOMAD server connectivity...")

        info = client.get_info()

        logger.info("NOMAD server responded successfully.")

        # ----------------------------------------------------
        # 4. Test authentication
        # ----------------------------------------------------

        logger.info("Checking authentication...")

        user_info = client.get_current_user()

        logger.info("Authentication successful.")

        if isinstance(user_info, dict):
            user_data = user_info.get("data", user_info)

            if isinstance(user_data, dict):
                username = (
                    user_data.get("username")
                    or user_data.get("name")
                )

                if username:
                    logger.info(
                        "Authenticated NOMAD user: %s",
                        username,
                    )

        # ----------------------------------------------------
        # 5. Initialize uploader
        # ----------------------------------------------------

        uploader = NomadUploader(
            client=client,
            logger=logger,
        )

        # ----------------------------------------------------
        # 6. Check upload listing
        # ----------------------------------------------------

        logger.info("Checking authenticated user's upload list...")

        upload_list = client.request(
            method="GET",
            endpoint="/uploads",
        )

        upload_found_in_list = find_upload_id(
            upload_list,
            upload_id,
        )

        if upload_found_in_list:
            logger.info(
                "Upload ID was found in the upload listing."
            )
        else:
            logger.warning(
                "Upload ID was not found in the returned upload list."
            )

        # ----------------------------------------------------
        # 7. Retrieve the specific upload
        # ----------------------------------------------------

        logger.info(
            "Retrieving upload information for %s...",
            upload_id,
        )

        try:

            upload_information = uploader.get_upload_information(
                upload_id
            )

        except NomadAPIError as error:

            logger.error(
                "Could not retrieve the specified upload."
            )

            logger.error("HTTP status: %s", error.status_code)
            logger.error("API error: %s", error)

            if error.status_code == 404:

                logger.error(
                    "The upload endpoint returned 404. "
                    "Check the NOMAD instance and upload ID."
                )

                if upload_found_in_list:
                    logger.error(
                        "The upload appeared in the listing but "
                        "direct retrieval failed. This suggests "
                        "an endpoint or server-routing problem."
                    )
                else:
                    logger.error(
                        "The upload was also absent from the "
                        "returned listing. Confirm that this upload "
                        "belongs to the configured NOMAD instance."
                    )

            raise

        logger.info("Upload successfully retrieved.")

        # ----------------------------------------------------
        # 8. Check current reviewer groups
        # ----------------------------------------------------

        existing_groups = extract_reviewer_groups(
            upload_information
        )

        if existing_groups is None:
            logger.warning(
                "Could not identify reviewer_groups in the "
                "upload response. The sharing method will still "
                "perform its own metadata check."
            )
        else:
            logger.info(
                "Current reviewer groups: %s",
                existing_groups,
            )

        # ----------------------------------------------------
        # 9. Assign reviewer group
        # ----------------------------------------------------

        logger.info(
            "Assigning reviewer access to the Uppsala group..."
        )

        uploader.ensure_reviewer_group(
            upload_id=upload_id,
            group_id=GROUP_ID,
        )

        # ----------------------------------------------------
        # 10. Verify assignment
        # ----------------------------------------------------

        logger.info(
            "Checking reviewer-group assignment..."
        )

        updated_information = uploader.get_upload_information(
            upload_id
        )

        updated_groups = extract_reviewer_groups(
            updated_information
        )

        if updated_groups is None:
            raise RuntimeError(
                "Could not verify reviewer_groups in the "
                "updated NOMAD upload response."
            )

        if GROUP_ID not in updated_groups:
            raise RuntimeError(
                "NOMAD did not confirm the reviewer-group assignment."
            )

        logger.info("=" * 60)
        logger.info("GROUP SHARING TEST SUCCESSFUL")
        logger.info("=" * 60)

        logger.info(
            "Upload ID: %s",
            upload_id,
        )

        logger.info(
            "Reviewer group successfully assigned: %s",
            GROUP_ID,
        )

        logger.info(
            "Confirmed reviewer groups: %s",
            updated_groups,
        )

    except Exception:

        logger.exception(
            "NOMAD group-sharing test failed."
        )

        raise

    finally:

        if client is not None:
            client.close()

        logger.info("NOMAD client session closed.")


if __name__ == "__main__":
    main()
