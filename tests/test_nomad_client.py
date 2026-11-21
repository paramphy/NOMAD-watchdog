"""Manual NOMAD client smoke test."""
from nomad_experiment_watcher.nomad import NomadAPIError
from nomad_experiment_watcher.nomad.configured_client import create_configured_client


def main():
    client = None
    try:
        config, client = create_configured_client()
        print("\n--- Current NOMAD User ---")
        print(client.get_current_user())

        print("\n--- Existing Uploads ---")
        print(client.list_uploads())

    except NomadAPIError as error:
        print("\nNOMAD API Error:", error)
        if error.status_code is not None:
            print("HTTP status:", error.status_code)
        raise
    except Exception as error:
        print("\nUnexpected error:", error)
        raise
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
