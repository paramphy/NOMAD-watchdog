"""Manual test of NOMAD raw-file upload and remote verification."""
from pathlib import Path

from nomad_experiment_watcher.nomad import NomadUploader
from nomad_experiment_watcher.nomad.configured_client import create_configured_client


UPLOAD_ID = "oUKCnYO7RMyE_ccJj2aTYQ"
TEST_FILE = Path("watcher_verification_test.txt")


def main():
    config, client = create_configured_client()
    try:
        verification_settings = config.nomad_settings.get(
            "upload_verification", {}
        )
        content = (
            "NOMAD Experiment Watcher verification test.\n"
            "Testing asynchronous file upload and verification.\n"
        )
        TEST_FILE.write_text(content, encoding="utf-8")
        local_size = TEST_FILE.stat().st_size

        uploader = NomadUploader(
            client=client,
            verification_settings=verification_settings,
        )
        result = uploader.upload_file(
            upload_id=UPLOAD_ID,
            local_path=TEST_FILE,
            remote_directory="",
        )

        print("Upload accepted: YES")
        print("Remote file verified:", result["verified"])
        print("Local size:", local_size)
        print(
            "Remote size:",
            (result["remote_metadata"] or {}).get("size"),
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
