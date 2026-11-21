"""Manual authenticated NOMAD connection test."""
from nomad_experiment_watcher.nomad.configured_client import create_configured_client

config, client = create_configured_client()
try:
    print("NOMAD API:", client.base_url)
    print("Authenticated user:")
    print(client.get_current_user())
except Exception as error:
    print("NOMAD API request failed:", error)
    raise
finally:
    client.close()
