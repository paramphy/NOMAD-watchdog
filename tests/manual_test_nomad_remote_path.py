
from pathlib import Path
from tempfile import TemporaryDirectory

from nomad_experiment_watcher.nomad.client import NomadClient
from nomad_experiment_watcher.nomad.uploader import NomadUploader


def main():

    client = NomadClient()

    try:

        uploader = NomadUploader(
            client=client,
            verification_settings={
                "enabled": True,
                "initial_delay_seconds": 2,
                "retry_interval_seconds": 3,
                "timeout_seconds": 60,
            },
        )

        # Create a separate test upload.
        upload_id = uploader.create_upload(
            upload_name="experiment-watcher-integration-test",
            auto_decompress=False,
        )

        print(f"Test upload created: {upload_id}")

        with TemporaryDirectory() as temporary_directory:

            test_directory = Path(temporary_directory)

            test_file = test_directory / "replacement_test.txt"

            # -----------------------------------------
            # First upload
            # -----------------------------------------

            first_content = "First version of the test file.\n"

            test_file.write_text(
                first_content,
                encoding="utf-8",
            )

            first_size = test_file.stat().st_size

            print("\nUploading first version...")

            first_result = uploader.upload_file(
                upload_id=upload_id,
                local_path=test_file,
                remote_directory="RTP",
                auto_decompress=False,
            )

            print("First upload verified:", first_result["verified"])

            print(
                "First remote size:",
                first_result["remote_metadata"]["size"],
            )

            # -----------------------------------------
            # Second upload to exactly the same path
            # -----------------------------------------

            second_content = (
                "Second version replacing the first version. "
                "This content is deliberately longer.\n"
            )

            test_file.write_text(
                second_content,
                encoding="utf-8",
            )

            second_size = test_file.stat().st_size

            print("\nUploading second version to the same path...")

            second_result = uploader.upload_file(
                upload_id=upload_id,
                local_path=test_file,
                remote_directory="RTP",
                auto_decompress=False,
            )

            print("Second upload verified:", second_result["verified"])

            second_remote_size = (
                second_result["remote_metadata"]["size"]
            )

            print("Expected second size:", second_size)
            print("Second remote size:", second_remote_size)

            # -----------------------------------------
            # Final result
            # -----------------------------------------

            if (
                first_result["verified"]
                and second_result["verified"]
                and second_remote_size == second_size
                and second_size != first_size
            ):

                print(
                    "\nSUCCESS: NOMAD accepted both uploads "
                    "at the same remote path."
                )

                print(
                    "The remote metadata now reports the "
                    "second file size."
                )

            else:

                print(
                    "\nWARNING: Replacement behaviour "
                    "could not be confirmed."
                )

        print("\nTest upload ID:", upload_id)

        print(
            "This test upload has not been published. "
            "You can inspect it in NOMAD."
        )

    finally:

        client.close()


if __name__ == "__main__":
    main()
