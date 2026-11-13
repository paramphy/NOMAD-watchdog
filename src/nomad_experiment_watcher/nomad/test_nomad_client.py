
from nomad_experiment_watcher.nomad import (
    NomadClient,
    NomadAPIError,
)


def main():

    try:

        with NomadClient() as client:

            # Test 1: Current user
            print("\n--- Current NOMAD User ---")

            user = client.get_current_user()
            print(user)

            # Test 2: Server information
            print("\n--- NOMAD Server Information ---")

            #info = client.get_info()
            #print(info)

            # Test 3: Existing uploads
            print("\n--- Existing Uploads ---")

            uploads = client.list_uploads()
            print(uploads)

    except NomadAPIError as error:

        print("\nNOMAD API Error:")
        print(error)

        if error.status_code is not None:
            print("HTTP status:", error.status_code)

    except Exception as error:

        print("\nUnexpected error:")
        print(error)


if __name__ == "__main__":
    main()
