"""
KYC MCP Server — HTTP/SSE Transport
=====================================
Exposes MCP tools for KYC registration, agent registration, and routing checks.

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
from db.database import DB_PATH, init_db
import kyc_service as svc

# ─────────────────────────────────────────────────────────
# Initialise
# ─────────────────────────────────────────────────────────

init_db()

MCP_HOST = os.environ.get("KYC_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("KYC_MCP_PORT", 8000))

mcp = FastMCP(
    name="kyc-mcp-server",
    instructions=(
        "This server provides KYC (Know Your Customer) verification tools. "
        "Typical flow: register_user(phone) → initiate_kyc → confirm_kyc_otp → fetch_verified_profile. "
        "The simplified mock flow only requires a phone number and accepts any OTP. "
        "After KYC is VERIFIED, use register_agent with the same phone number or user_id to issue an AR-managed agent_id for a customer-controlled agent. "
        "For ecommerce via Claude Desktop, register a shopping agent first and then pass that agent_id to the ecommerce MCP. "
        "Use verify_agent_capability before routing any request to an ecommerce MCP. "
        "Use re_verify_kyc to update or replace documents for an existing user. "
        "Use list_supported_document_types to see all verifiable document types. "
        "Use register_service to register an ecommerce service with AR. "
        "Use verify_traffic to verify agent traffic before allowing access to a service."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
)

# ─────────────────────────────────────────────────────────
# Tool 1 — register_user
# ─────────────────────────────────────────────────────────

@mcp.tool()
def register_user(phone: str, full_name: str = "", email: str = "") -> str:
    """
    Register a new user in the simplified KYC system.

    Args:
        phone:     Required phone number.
        full_name: Optional display name. Auto-generated from phone when omitted.
        email:     Optional email. Auto-generated when omitted.

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
def initiate_kyc(user_id: str, documents_json: str = "") -> str:
    """
    Step 1 of KYC: start the simplified phone-based OTP flow.

    Args:
        user_id:        The user_id returned by register_user.
        documents_json: Ignored in the simplified flow. Optional for backward compatibility.

    Returns:
        JSON with session_id and OTP instructions.
    """
    result = svc.initiate_kyc(user_id=user_id, documents={})
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 3 — confirm_kyc_otp
# ─────────────────────────────────────────────────────────

@mcp.tool()
def confirm_kyc_otp(user_id: str, session_id: str, otp: str) -> str:
    """
    Step 2 of KYC: Confirm any non-empty OTP to complete verification.

    Args:
        user_id:    The user's ID.
        session_id: The session_id returned by initiate_kyc or re_verify_kyc.
        otp:        Any non-empty OTP value.

    Returns:
        JSON with KYC status and generated registry agent_id.
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
# Tool 4A — verify_and_generate_id
# ─────────────────────────────────────────────────────────

@mcp.tool()
def verify_and_generate_id(user_id: str, session_id: str, otp: str) -> str:
    """
    Convenience extension tool that confirms the KYC OTP and, upon successful verification,
    immediately generates and returns the new unique agent ID in the same response.

    Args:
        user_id:    The user's ID.
        session_id: The session_id returned by initiate_kyc or re_verify_kyc.
        otp:        The 6-digit OTP provided to the user.

    Returns:
        JSON with KYC status and generated registry agent_id on success.
        For ecommerce access, call register_agent afterwards to create the AR-managed agent_id.
    """
    result = svc.verify_and_generate_id(user_id=user_id, session_id=session_id, otp=otp)
    return json.dumps(result, indent=2)

# ─────────────────────────────────────────────────────────
# Tool 4B — get_registered_agent_id
# ─────────────────────────────────────────────────────────

@mcp.tool()
def get_registered_agent_id(user_id: str) -> str:
    """
    Fetch the unique Agent ID for a fully verified user.
    The unique ID follows the format: name_agent-[uuid_with_salt]@pinelabsUPAI
    This is the registry agent ID, not the AR-managed ecommerce routing agent ID.

    Args:
        user_id: The user's ID. User must have VERIFIED kyc_status.

    Returns:
        JSON with the user's generated registry agent_id.
    """
    import registry_service
    result = registry_service.get_registered_agent_id(user_id=user_id)
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
# Tool 9 — register_agent
# ─────────────────────────────────────────────────────────

@mcp.tool()
def register_agent(
    agent_name: str,
    user_id: str = "",
    phone: str = "",
    description: str = "",
    capabilities_json: str = "",
) -> str:
    """
    Register a customer-controlled agent with AR.

    Args:
        agent_name:        Display name for the agent.
        user_id:           Optional AR user_id. If omitted, phone can be used instead.
        phone:             Optional phone number to look up the verified user.
        description:       Optional plain-language description.
        capabilities_json: Optional JSON array of allowed capabilities, for example:
                           ["ECOMMERCE_ACCESS", "CHECKOUT", "PAYMENT"]
                           Defaults to shopping-ready capabilities when omitted.

    Returns:
        JSON with the AR-managed agent_id and granted capabilities.
    """
    capabilities = None
    if capabilities_json.strip():
        try:
            capabilities = json.loads(capabilities_json)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid capabilities_json: {e}"})

        if not isinstance(capabilities, list):
            return json.dumps(
                {"success": False, "error": "capabilities_json must decode to a JSON array."}
            )

    result = svc.register_agent(
        user_id=user_id,
        agent_name=agent_name,
        description=description,
        capabilities=capabilities,
        phone=phone,
    )
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 10 — verify_agent_capability
# ─────────────────────────────────────────────────────────

@mcp.tool()
def verify_agent_capability(agent_id: str, capability: str = "ECOMMERCE_ACCESS") -> str:
    """
    Verify that an agent is registered with AR and has the capability needed
    before traffic is forwarded to an ecommerce MCP.

    Args:
        agent_id:    The AR-managed agent_id returned by register_agent.
        capability:  Capability required for the next routed action.

    Returns:
        JSON with allow/block decision and next steps for the MCP client.
    """
    result = svc.verify_agent_capability(agent_id=agent_id, capability=capability)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Tool 11 — register_service
# ─────────────────────────────────────────────────────────

@mcp.tool()
def register_service(
    service_name: str,
    service_url: str,
    description: str = "",
    capabilities_json: str = "",
) -> str:
    """
    Register an ecommerce or other service with AR for traffic verification.

    Args:
        service_name:      Unique name (e.g. "solespace", "payment-gateway").
        service_url:       Base URL of the service (e.g. "http://localhost:8001").
        description:       What this service does.
        capabilities_json: JSON array of capabilities offered, e.g. '["ECOMMERCE"]'

    Returns:
        JSON with service registration details.
    """
    capabilities = None
    if capabilities_json.strip():
        try:
            capabilities = json.loads(capabilities_json)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid capabilities_json: {e}"})
    result = svc.register_service(service_name, service_url, description, capabilities)
    return json.dumps(result, indent=2)

@mcp.tool()
def verify_traffic(agent_id: str, service_name: str) -> str:
    """
    Verify that an agent's traffic is legitimate before allowing access to a service.
    Called by ecommerce or other services to gate incoming requests.

    Args:
        agent_id:     The agent_id to verify.
        service_name: The service the agent is trying to access (e.g. "solespace").

    Returns:
        JSON with allow/block decision.
    """
    result = svc.verify_traffic(agent_id, service_name)
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    from otp_service import OTP_VALIDITY_MINUTES
    import sys as _sys

    tool_count = len(getattr(mcp, "_tool_manager")._tools)

    if "--sse" in _sys.argv:
        print(f"🚀 KYC MCP Server starting on http://{MCP_HOST}:{MCP_PORT}")
        print(f"   SSE endpoint : http://{MCP_HOST}:{MCP_PORT}/sse")
        print(f"   Tools        : {tool_count} tools registered")
        print(f"   Storage      : SQLite → {DB_PATH}")
        print(f"   OTP          : Dummy (any non-empty value), valid {OTP_VALIDITY_MINUTES} min")
        mcp.run(transport="sse")
    else:
        # Default: stdio transport (for Claude Desktop)
        mcp.run(transport="stdio")
