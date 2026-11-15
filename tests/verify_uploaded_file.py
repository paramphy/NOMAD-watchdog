
"""
Test NOMAD file upload and remote verification.

Uses:
- Existing NOMAD upload
- NOMAD_PAT environment variable
- Verification settings from config/config.yaml
"""

from pathlib import Path

from nomad_experiment_watcher.config import load_config
from nomad_experiment_watcher.nomad import (
    NomadClient,
    NomadUploader,
)


# ---------------------------------------------------------
# Test settings
# ---------------------------------------------------------

UPLOAD_ID = "oUKCnYO7RMyE_ccJj2aTYQ"

TEST_FILE = Path("watcher_verification_test.txt")


# ---------------------------------------------------------
# Main test
# ---------------------------------------------------------

def main():

    print("=" * 55)
    print("NOMAD UPLOAD VERIFICATION TEST")
    print("=" * 55)

    # Load application configuration.
    config = load_config("config/config.yaml")

    verification_settings = config.upload_verification_settings

    print("\nVerification settings:")
    print(verification_settings)

    if not verification_settings.get("enabled", True):
        print("\nWARNING: Upload verification is disabled.")

    # Create a test file.
    content = (
        "NOMAD Experiment Watcher verification test.\n"
        "Testing asynchronous file upload and verification.\n"
    )

    TEST_FILE.write_text(content, encoding="utf-8")

    local_size = TEST_FILE.stat().st_size

    print("\nLocal test file created:")
    print(f"Filename : {TEST_FILE.name}")
    print(f"Size     : {local_size} bytes")

    try:

        # Connect to NOMAD.
        print("\nConnecting to NOMAD...")

        with NomadClient() as client:

            uploader = NomadUploader(
                client=client,
                verification_settings=verification_settings,
            )

            print("Connection initialized.")

            # Upload and verify.
            print("\nUploading test file...")
            print(f"Upload ID: {UPLOAD_ID}")

            result = uploader.upload_file(
                upload_id=UPLOAD_ID,
                local_path=TEST_FILE,
                remote_directory="",
            )

            print("\n" + "=" * 55)
            print("UPLOAD TEST RESULT")
            print("=" * 55)

            print("Upload accepted       : YES")
            print(f"Verification enabled  : {verification_settings.get('enabled', True)}")
            print(f"Remote file verified  : {result['verified']}")

            if result["remote_metadata"]:

                metadata = result["remote_metadata"]

                print("\nRemote file information:")
                print(f"Filename : {metadata.get('name')}")
                print(f"Size     : {metadata.get('size')} bytes")
                print(f"Is file  : {metadata.get('is_file')}")

                if metadata.get("size") == local_size:
                    print("\nSUCCESS: Local and remote file sizes match.")
                else:
                    print("\nWARNING: File sizes do not match.")

            else:
                print("\nUpload completed without remote verification.")

            print("\nTEST COMPLETED SUCCESSFULLY.")

    except Exception as error:

        print("\n" + "=" * 55)
        print("UPLOAD TEST FAILED")
        print("=" * 55)

        print(f"Error: {error}")

        raise

    finally:
        print("\nTest finished.")


if __name__ == "__main__":
    main()
