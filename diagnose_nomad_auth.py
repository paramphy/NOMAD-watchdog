
import hashlib

from nomad_experiment_watcher.nomad import NomadClient, NomadAPIError


def main():
    client = None

    try:
        client = NomadClient()

        print("\n--- NOMAD Authentication Diagnostics ---")

        print("API URL:", client.base_url)

        fingerprint = hashlib.sha256(
            client.token.encode("utf-8")
        ).hexdigest()[:12]

        print("PAT fingerprint:", fingerprint)
        print("PAT length:", len(client.token))

        print("\nTesting GET /users/me ...")

        user = client.get_current_user()

        print("Authentication successful!")
        print("User response type:", type(user).__name__)

        if isinstance(user, dict):
            print("User response keys:", list(user.keys()))

    except NomadAPIError as error:
        print("\nNOMAD API error:", error)
        print("HTTP status:", error.status_code)

    except Exception as error:
        print("\nUnexpected error:", error)

    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
