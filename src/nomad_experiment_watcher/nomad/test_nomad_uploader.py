
from nomad_experiment_watcher.nomad import (
    NomadClient,
    NomadUploader,
    NomadAPIError,
)


def main():

    try:

        with NomadClient() as client:

            uploader = NomadUploader(client)

            # Create a test upload
            upload_id = uploader.create_upload(
                upload_name="Cu_186_Uploader_Test"
            )

            print("\nUpload created successfully!")
            print("Upload ID:", upload_id)

            # Retrieve upload information
            information = uploader.get_upload_information(
                upload_id
            )

            print("\nUpload information:")
            print(information)

    except NomadAPIError as error:

        print("\nNOMAD API error:")
        print(error)

        if error.status_code is not None:
            print("Status code:", error.status_code)

    except Exception as error:

        print("\nUnexpected error:")
        print(error)


if __name__ == "__main__":
    main()
