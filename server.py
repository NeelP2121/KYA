"""
KYC MCP Server — HTTP/SSE Transport
=====================================
Exposes 8 MCP tools for KYC registration and verification.

Transport : HTTP + Server-Sent Events (SSE)
Port      : 8000 (configurable via KYC_MCP_PORT env var)
Storage   : SQLite (kyc_store.db in project root)
"""

import os
import sys
import json
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from db.database import init_db
import kyc_service as svc

# ─────────────────────────────────────────────────────────
# Initialise
# ─────────────────────────────────────────────────────────

init_db()

mcp = FastMCP(
    name="kyc-mcp-server",
    instructions=(
        "This server provides KYC (Know Your Customer) verification tools. "
        "Typical flow: register_user → initiate_kyc → confirm_kyc_otp → fetch_verified_profile. "
        "Use re_verify_kyc to update or replace documents for an existing user. "
        "Use list_supported_document_types to see all verifiable document types."
    ),
)

# ─────────────────────────────────────────────────────────
# Tool 1 — register_user
# ─────────────────────────────────────────────────────────

@mcp.tool()
def register_user(full_name: str, email: str, phone: str = "") -> str:
    """
    Register a new user in the KYC system.

    Args:
        full_name: User's full legal name (as it appears on government ID).
        email:     Unique email address for the user.
        phone:     Optional phone number (10-digit Indian mobile, e.g. 9876543210).

    Returns:
        JSON with user_id and next steps.
    """
    result = svc.register_user(
        full_name=full_name,
        email=email,
        phone=phone or None,
    )
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 2 — initiate_kyc
# ─────────────────────────────────────────────────────────

@mcp.tool()
def initiate_kyc(user_id: str, documents_json: str) -> str:
    """
    Step 1 of KYC: Submit documents and initiate a KYC session.
    An OTP will need to be confirmed before documents are verified.

    Args:
        user_id:        The user_id returned by register_user.
        documents_json: JSON string mapping doc_type to its fields. Examples:
                        {
                          "AADHAAR": {"aadhaar_number": "999999999999"},
                          "PAN":     {"pan_number": "ABCDE1234F"},
                          "MOBILE":  {"mobile_number": "9876543210"}
                        }
                        Supported doc types: AADHAAR, PAN, MOBILE.
                        Call list_supported_document_types for full details.

    Returns:
        JSON with session_id and OTP instructions.
    """
    try:
        documents = json.loads(documents_json)
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"Invalid documents_json: {e}"})

    result = svc.initiate_kyc(user_id=user_id, documents=documents)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 3 — confirm_kyc_otp
# ─────────────────────────────────────────────────────────

@mcp.tool()
def confirm_kyc_otp(user_id: str, session_id: str, otp: str) -> str:
    """
    Step 2 of KYC: Confirm OTP to trigger document verification.
    This is the final step — KYC status will be set to VERIFIED or FAILED.

    Args:
        user_id:    The user's ID.
        session_id: The session_id returned by initiate_kyc or re_verify_kyc.
        otp:        The 6-digit OTP provided to the user.

    Returns:
        JSON with KYC status and per-document verification results.
    """
    result = svc.confirm_kyc_otp(user_id=user_id, session_id=session_id, otp=otp)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 4 — check_kyc_status
# ─────────────────────────────────────────────────────────

@mcp.tool()
def check_kyc_status(user_id: str) -> str:
    """
    Check the current KYC status of a user.

    KYC status values:
      PENDING   — Registered but KYC not started.
      INITIATED — KYC in progress (OTP pending or docs being verified).
      VERIFIED  — All documents verified successfully.
      FAILED    — One or more documents failed verification.
      BLOCKED   — Account blocked by admin (reserved for future use).

    Args:
        user_id: The user's ID.

    Returns:
        JSON with kyc_status and latest session summary.
    """
    result = svc.check_kyc_status(user_id=user_id)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 5 — fetch_verified_profile
# ─────────────────────────────────────────────────────────

@mcp.tool()
def fetch_verified_profile(user_id: str) -> str:
    """
    Fetch the full verified profile for a KYC-verified user.
    Includes all successfully verified document data (masked sensitive fields).
    Only available when kyc_status == VERIFIED.

    Args:
        user_id: The user's ID.

    Returns:
        JSON with user details and verified document data.
    """
    result = svc.fetch_verified_profile(user_id=user_id)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 6 — re_verify_kyc
# ─────────────────────────────────────────────────────────

@mcp.tool()
def re_verify_kyc(user_id: str, documents_json: str) -> str:
    """
    Initiate a re-verification session for an existing user.
    Use this to:
      - Fix a failed KYC by correcting document details.
      - Add new document types (e.g. add Passport to an Aadhaar-only profile).
      - Replace a document (e.g. update to a new PAN number).

    The full OTP + document verification flow runs again.

    Args:
        user_id:        The user's ID (must have previously initiated KYC).
        documents_json: JSON string with documents to verify. Same format as initiate_kyc.

    Returns:
        JSON with new session_id and OTP instructions.
    """
    try:
        documents = json.loads(documents_json)
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"Invalid documents_json: {e}"})

    result = svc.re_verify_kyc(user_id=user_id, documents=documents)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 7 — list_registered_users
# ─────────────────────────────────────────────────────────

@mcp.tool()
def list_registered_users(kyc_status_filter: str = "") -> str:
    """
    List all registered users. Optionally filter by KYC status.

    Args:
        kyc_status_filter: Optional. One of: PENDING, INITIATED, VERIFIED, FAILED, BLOCKED.
                           Leave empty to list all users.

    Returns:
        JSON with user list and total count.
    """
    result = svc.list_registered_users(
        kyc_status_filter=kyc_status_filter.strip() or None
    )
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 8 — list_supported_document_types
# ─────────────────────────────────────────────────────────

@mcp.tool()
def list_supported_document_types() -> str:
    """
    List all document types supported by this KYC server,
    along with their required input fields.

    Use this to discover what documents can be submitted in
    initiate_kyc or re_verify_kyc.

    Returns:
        JSON with supported document types and required fields.
    """
    result = svc.list_supported_document_types()
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from otp_service import OTP_VALIDITY_MINUTES

    port = int(os.environ.get("KYC_MCP_PORT", 8000))
    host = os.environ.get("KYC_MCP_HOST", "0.0.0.0")

    print(f"🚀 KYC MCP Server starting on http://{host}:{port}")
    print(f"   SSE endpoint : http://{host}:{port}/sse")
    print(f"   Tools        : 8 tools registered")
    print(f"   Storage      : SQLite → kyc_store.db")
    print(f"   OTP          : Fixed (421596), valid {OTP_VALIDITY_MINUTES} min")

    asyncio.run(mcp.run_sse_async(host=host, port=port))
