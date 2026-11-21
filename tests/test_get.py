"""Manual NOMAD /info connectivity test using project configuration."""
from nomad_experiment_watcher.nomad.configured_client import create_configured_client

config, client = create_configured_client()
try:
    print("NOMAD API:", client.base_url)
    print("Server information:")
    print(client.get_info())
finally:
    client.close()
