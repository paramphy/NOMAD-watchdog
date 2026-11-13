
import os
import requests

BASE_URL = (
    "https://enter-hay-gel-magazine.trycloudflare.com"
    "/nomad-oasis/api/v1"
)

# Read the PAT from the environment
token = os.getenv("NOMAD_PAT")

if not token:
    raise RuntimeError(
        "NOMAD_PAT environment variable is not configured."
    )

# Authentication header
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
}

# Test authenticated user endpoint
url = f"{BASE_URL}/users/me"

try:
    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    print("Status code:", response.status_code)
    print("Response:")

    try:
        print(response.json())
    except ValueError:
        print(response.text)

    response.raise_for_status()

except requests.exceptions.RequestException as error:
    print("NOMAD API request failed:")
    print(error)
