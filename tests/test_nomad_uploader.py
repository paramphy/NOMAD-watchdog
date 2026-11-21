"""Manual NOMAD upload creation smoke test."""
from nomad_experiment_watcher.nomad import NomadUploader, NomadAPIError
from nomad_experiment_watcher.nomad.configured_client import create_configured_client


def main():
    client = None
    try:
        config, client = create_configured_client()
        uploader = NomadUploader(
            client,
            verification_settings=config.nomad_settings.get(
                "upload_verification", {}
            ),
        )

        upload_id = uploader.create_upload(
            upload_name="Cu_186_Uploader_Test",
            auto_decompress=False,
        )
        print("\nUpload created successfully!")
        print("Upload ID:", upload_id)
        print("\nUpload information:")
        print(uploader.get_upload_information(upload_id))

    except NomadAPIError as error:
        print("\nNOMAD API error:", error)
        if error.status_code is not None:
            print("Status code:", error.status_code)
        raise
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
