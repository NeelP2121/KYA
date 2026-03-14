import urllib.request
import base64
import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

client_id = "e4212815-589d-4075-839a-c2c9911f0823"
client_secret = "3f7a7103badf4c518a0c918621b969af"
b64_creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

url = "https://pluraluat.v2.pinepg.in/api/v1/orders"

print(f"\n--- Testing API Order Endpoint directly with Basic Auth ---")

try:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {b64_creds}",
            "Content-Type": "application/json"
        },
        data=json.dumps({"merchant_data": {"merchant_order_reference": "test1234"}, "payment_data": {"amount": 100, "currency_code": "INR"}}).encode(),
        method="POST"
    )
    with urllib.request.urlopen(req) as response:
        print("SUCCESS")
        print(response.read().decode())
except Exception as e:
    body = e.read().decode() if hasattr(e, 'read') else str(e)
    print(f"FAILED: {body}")
