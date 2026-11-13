
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
import hashlib
import json

from nomad_experiment_watcher.nomad import (
    NomadClient,
    NomadUploader,
)


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

# Paste the upload ID returned by your successful test.
UPLOAD_ID = "oUKCnYO7RMyE_ccJj2aTYQ"

REMOTE_DIRECTORY = ""

# Create a uniquely named test file.
filename = (
    f"watcher_test_{datetime.now():%Y%m%d_%H%M%S}.txt"
)

test_file = Path(filename)

content = (
    "NOMAD Experiment Watcher test upload\n"
    "Experiment: Cu_186\n"
    "Measurement: Test\n"
    "Purpose: Verify raw file transfer to NOMAD.\n"
)

# ---------------------------------------------------------
# CREATE LOCAL TEST FILE
# ---------------------------------------------------------

test_file.write_text(content, encoding="utf-8")

local_size = test_file.stat().st_size

local_checksum = hashlib.sha256(
    test_file.read_bytes()
).hexdigest()

print("\nLocal test file created.")
print("Filename:", test_file.name)
print("Size:", local_size, "bytes")
print("SHA256:", local_checksum)


# ---------------------------------------------------------
# CONNECT TO NOMAD
# ---------------------------------------------------------

with NomadClient() as client:

    uploader = NomadUploader(client)

    print("\nConnected to NOMAD.")

    # -----------------------------------------------------
    # UPLOAD FILE
    # -----------------------------------------------------

    print("\nUploading test file...")

    response = uploader.upload_file(
        upload_id=UPLOAD_ID,
        local_path=test_file,
        remote_directory=REMOTE_DIRECTORY,
        auto_decompress=False,
    )

    print("\nUpload response:")
    print(json.dumps(response, indent=4, default=str))

    print("\nFile transfer request completed.")

    # -----------------------------------------------------
    # VERIFY REMOTE FILE METADATA
    # -----------------------------------------------------

    print("\nChecking remote raw-file directory...")

    encoded_upload_id = quote(UPLOAD_ID, safe="")

    listing = client.request(
        method="GET",
        endpoint=f"/uploads/{encoded_upload_id}/rawdir/",
    )

    print("\nRemote directory response:")
    print(json.dumps(listing, indent=4, default=str))

    # NOMAD rawdir endpoint returns directory_metadata.
    directory = listing.get("directory_metadata", {})

    remote_files = directory.get("content", [])

    matching_file = next(
        (
            item for item in remote_files
            if item.get("name") == test_file.name
        ),
        None,
    )

    if matching_file is None:
        raise RuntimeError(
            "Upload request succeeded, but the test file "
            "was not found in the NOMAD raw directory."
        )

    remote_size = matching_file.get("size")

    if remote_size != local_size:
        raise RuntimeError(
            f"File size mismatch! Local: {local_size}, "
            f"Remote: {remote_size}"
        )

    print("\n====================================")
    print("UPLOAD VERIFICATION SUCCESSFUL")
    print("====================================")
    print("Filename:", matching_file.get("name"))
    print("Local size:", local_size)
    print("Remote size:", remote_size)
    print("SHA256 (local):", local_checksum)
    print("====================================")
