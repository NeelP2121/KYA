"""
Pine Labs Payment Integration Service — UAT Environment
=========================================================
Wraps all Pine Labs UAT API calls for the SoleSpace ecommerce server.
This module is used internally — NOT exposed as MCP tools.

UAT Endpoints:
  Token:    POST https://pluraluat.v2.pinepg.in/api/auth/v1/token
  Checkout: POST https://pluraluat.v2.pinepg.in/api/checkout/v1/orders
  Status:   GET  https://pluraluat.v2.pinepg.in/api/pay/v1/orders/{id}

Test Card Details:
  VISA:       4012 0010 3714 1112, CVV: 065, any future expiry
  MASTERCARD: 5200 0000 0000 1096, CVV: 123, any future expiry
"""

import time
import uuid
import logging
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

# ─── UAT Credentials (test only) ──────────────────────────
CLIENT_ID = "e4212815-589d-4075-839a-c2c9911f0823"
CLIENT_SECRET = "3f7a7103badf4c518a0c918621b969af"
BASE_URL = "https://pluraluat.v2.pinepg.in"

# ─── Token Cache ──────────────────────────────────────────
_cached_token: str | None = None
_token_expiry: float = 0


def _request_headers(token: str | None = None) -> dict:
    """Common headers for Pine Labs API calls."""
    ist = timezone(timedelta(hours=5, minutes=30))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Request-ID": str(uuid.uuid4()),
        "Request-Timestamp": datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def get_access_token() -> str:
    """
    Obtain a Bearer token from Pine Labs UAT.

    Caches the token and re-uses it until 100 seconds before expiry.
    Endpoint: POST {BASE}/api/auth/v1/token
    """
    global _cached_token, _token_expiry

    if _cached_token and time.time() < _token_expiry:
        return _cached_token

    url = f"{BASE_URL}/api/auth/v1/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    headers = _request_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            body = resp.text
            logger.error("Token request failed [%s]: %s", resp.status_code, body)
            raise RuntimeError(
                f"Pine Labs token acquisition failed ({resp.status_code}): {body}"
            )

        data = resp.json()
        _cached_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        _token_expiry = time.time() + expires_in - 100  # refresh 100s early
        logger.info("Pine Labs token acquired, expires in %ss", expires_in)
        return _cached_token


async def create_checkout_order(
    amount_inr: float,
    merchant_reference: str,
    product_details: list[dict] | None = None,
    customer_email: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    callback_url: str = "http://localhost:5173/payment/callback",
    failure_callback_url: str = "http://localhost:5173/payment/failure",
) -> dict:
    """
    Create a Pine Labs hosted checkout order.

    Returns:
        {
            "plural_order_id": str,
            "redirect_url": str,
            "token": str,
            "merchant_reference": str
        }
    """
    token = await get_access_token()
    url = f"{BASE_URL}/api/checkout/v1/orders"

    amount_paise = int(amount_inr * 100)

    first_name = ""
    last_name = ""
    if customer_name:
        parts = customer_name.strip().split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    body: dict = {
        "merchant_order_reference": merchant_reference,
        "order_amount": {
            "value": amount_paise,
            "currency": "INR",
        },
        "pre_auth": False,
        "callback_url": callback_url,
        "failure_callback_url": failure_callback_url,
        "purchase_details": {
            "customer": {
                "email_id": customer_email,
                "first_name": first_name,
                "last_name": last_name,
                "customer_id": merchant_reference,
                "mobile_number": customer_phone,
            }
        },
    }

    headers = _request_headers(token)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)

        if resp.status_code not in (200, 201):
            error_body = resp.text
            logger.error(
                "Checkout order failed [%s]: %s", resp.status_code, error_body
            )
            raise ValueError(
                f"Pine Labs checkout failed ({resp.status_code}): {error_body}"
            )

        data = resp.json()
        logger.info("Checkout order created: %s", data.get("order_id"))

        redirect_url = data.get("redirect_url")
        if not redirect_url:
            logger.error("No redirect_url in response: %s", data)
            raise ValueError(
                f"Pine Labs response missing redirect_url. Full response: {data}"
            )

        return {
            "plural_order_id": data.get("order_id", ""),
            "redirect_url": redirect_url,
            "token": data.get("token", ""),
            "merchant_reference": merchant_reference,
        }


async def get_order_status(plural_order_id: str) -> dict:
    """
    Get the status of a Pine Labs checkout order.

    Returns:
        {
            "plural_order_id": str,
            "status": str,           # CREATED | PROCESSED | FAILED | AUTHORIZED
            "amount": float,         # in INR (converted from paise)
            "merchant_reference": str,
            "raw_response": dict
        }
    """
    try:
        token = await get_access_token()
        url = f"{BASE_URL}/api/pay/v1/orders/{plural_order_id}"
        headers = _request_headers(token)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                logger.error(
                    "Order status failed [%s]: %s", resp.status_code, resp.text
                )
                return {
                    "status": "UNKNOWN",
                    "error": f"HTTP {resp.status_code}: {resp.text}",
                    "raw_response": resp.text,
                }

            payload = resp.json()
            data = payload.get("data", payload)
            amount_paise = 0
            if "order_amount" in data:
                amount_paise = data["order_amount"].get("value", 0)

            return {
                "plural_order_id": plural_order_id,
                "status": data.get("status", "UNKNOWN"),
                "amount": amount_paise / 100.0,
                "merchant_reference": data.get("merchant_order_reference", ""),
                "raw_response": payload,
            }

    except Exception as e:
        logger.error("get_order_status error: %s", e)
        return {"status": "UNKNOWN", "error": str(e)}
