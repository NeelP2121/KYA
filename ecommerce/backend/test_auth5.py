import urllib.request
import base64
import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

client_id = "e4212815-589d-4075-839a-c2c9911f0823"
client_secret = "3f7a7103badf4c518a0c918621b969af"

url = "https://api.pluralpay.in/api/v2/oauth2/token"  # Using PROD instead of UAT
print(f"\n--- Testing PROD API Auth Endpoint ---")

try:
    print("\nMethod 1: PROD Basic Auth + Form Encoded")
    b64_creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {b64_creds}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data=b"grant_type=client_credentials",
        method="POST"
    )
    with urllib.request.urlopen(req) as response:
        print("SUCCESS")
        data = json.loads(response.read().decode())
        print(f"Token: {data.get('access_token')[:10]}...")
except Exception as e:
    body = e.read().decode() if hasattr(e, 'read') else str(e)
    print(f"FAILED: {body}")
