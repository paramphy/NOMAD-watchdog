"""Manual NOMAD authentication diagnostic; never prints the PAT."""
from nomad_experiment_watcher.nomad.configured_client import create_configured_client

client = None
try:
    config, client = create_configured_client()
    print("NOMAD API:", client.base_url)
    print("Testing authenticated GET /users/me ...")
    user = client.get_current_user()
    print("Authentication successful.")
    print("Response type:", type(user).__name__)
    if isinstance(user, dict):
        print("Response keys:", list(user.keys()))
except Exception as error:
    print("NOMAD authentication diagnostic failed:", error)
    raise
finally:
    if client is not None:
        client.close()
