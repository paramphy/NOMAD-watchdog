
import os

import requests

BASE_URL = (
    "https://enter-hay-gel-magazine.trycloudflare.com"
    "/nomad-oasis/api/v1"
)

# Read the PAT from the environment
token = os.getenv("NOMAD_PAT")

headers = {
    "Authorization": f"Bearer {token}"
}
    
# Send a GET request
response = requests.get(
    f"{BASE_URL}/info",
    headers=headers,
    timeout=30
)

# Display the response status
print("Status code:", response.status_code)

# Display the response content
print("Response:")

try:
    print(response.json())
except ValueError:
    print(response.text)
