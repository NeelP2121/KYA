"""
KYC Service Layer.

Contains all business logic called by MCP tools.
MCP tools are thin wrappers; this module holds the substance.
"""

from db import database as kyc_db
from verifiers.registry import get_verifier, supported_doc_types
from otp_service import verify_otp, FIXED_OTP, OTP_VALIDITY_MINUTES
import registry_service


DEFAULT_AGENT_CAPABILITIES = ["ECOMMERCE_ACCESS"]


# ─────────────────────────────────────────────────────────
# Tool 1 — register_user
# ─────────────────────────────────────────────────────────

def register_user(full_name: str, email: str, phone: str | None = None) -> dict:
    """
    Register a new user. Does NOT start KYC.
    Returns user record.
    """
    full_name = full_name.strip()
    email = email.strip().lower()

    if not full_name:
        return _err("full_name is required.")
    if not email or "@" not in email:
        return _err("A valid email address is required.")

    existing = kyc_db.get_user_by_email(email)
    if existing:
        return _err(
            f"A user with email '{email}' already exists. "
            f"User ID: {existing['id']}, KYC status: {existing['kyc_status']}."
        )

    user = kyc_db.create_user(full_name, email, phone)
    kyc_db.audit("USER_REGISTERED", user_id=user["id"], detail={"email": email})

    return {
        "success": True,
        "message": f"User '{full_name}' registered successfully.",
        "user": _safe_user(user),
        "next_step": "Call initiate_kyc with this user_id and your documents to begin KYC.",
    }


# ─────────────────────────────────────────────────────────
# Tool 2 — initiate_kyc
# ─────────────────────────────────────────────────────────

def initiate_kyc(user_id: str, documents: dict) -> dict:
    """
    Step 1 of KYC flow: validate documents format, create a session,
    and instruct the user to confirm via OTP.

    documents: { "AADHAAR": {"aadhaar_number": "..."}, "PAN": {"pan_number": "..."}, ... }
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] == "VERIFIED":
        return _err(
            "User is already KYC verified. "
            "Use re_verify_kyc if you want to update or re-verify documents."
        )

    if not documents:
        return _err("No documents provided. Please supply at least one document.")

    # Validate each document format before creating session
    format_errors = []
    for doc_type, payload in documents.items():
        verifier = get_verifier(doc_type)
        if not verifier:
            format_errors.append(
                f"'{doc_type}' is not a supported document type. "
                f"Supported: {[d['doc_type'] for d in supported_doc_types()]}"
            )
            continue
        valid, err = verifier.validate_format(payload)
        if not valid:
            format_errors.append(f"{verifier.display_name}: {err}")

    if format_errors:
        return _err("Document format validation failed.", errors=format_errors)

    # Cancel any existing OTP_PENDING sessions
    active = kyc_db.get_active_session_for_user(user_id)
    if active:
        kyc_db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by new session.")

    session = kyc_db.create_kyc_session(user_id, "INITIAL", documents)
    kyc_db.update_user_kyc_status(user_id, "INITIATED")
    kyc_db.audit("KYC_INITIATED", user_id=user_id, session_id=session["id"],
             detail={"doc_types": list(documents.keys())})

    return {
        "success": True,
        "message": "KYC session initiated. Please confirm with OTP to proceed.",
        "session_id": session["id"],
        "otp_instruction": (
            f"Submit the OTP via confirm_kyc_otp. "
            f"Session is valid for {OTP_VALIDITY_MINUTES} minutes."
        ),
        "documents_received": list(documents.keys()),
    }


# ─────────────────────────────────────────────────────────
# Tool 3 — confirm_kyc_otp
# ─────────────────────────────────────────────────────────

def confirm_kyc_otp(user_id: str, session_id: str, otp: str) -> dict:
    """
    Step 2 of KYC flow: verify OTP, then run document verification.
    This completes the KYC process.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    session = kyc_db.get_session_by_id(session_id)
    if not session:
        return _err(f"Session '{session_id}' not found.")

    if session["user_id"] != user_id:
        return _err("Session does not belong to this user.")

    if session["status"] not in ("OTP_PENDING",):
        if session["status"] == "OTP_CONFIRMED":
            return _err("OTP already confirmed for this session. Documents may already be verified.")
        return _err(f"Session is in status '{session['status']}' and cannot accept OTP.")

    # Verify OTP
    otp_valid, otp_err = verify_otp(otp, session["initiated_at"])
    if not otp_valid:
        kyc_db.audit("OTP_FAILED", user_id=user_id, session_id=session_id, detail={"reason": otp_err})
        return _err(f"OTP verification failed: {otp_err}")

    kyc_db.confirm_session_otp(session_id)
    kyc_db.audit("OTP_CONFIRMED", user_id=user_id, session_id=session_id)

    # Run document verifications
    documents = session["documents"]
    results = []
    all_verified = True
    failures = []

    for doc_type, payload in documents.items():
        verifier = get_verifier(doc_type)
        if not verifier:
            failures.append(f"No verifier found for {doc_type}")
            all_verified = False
            continue

        result = verifier.verify(payload, user["full_name"])
        kyc_db.save_document_result(
            user_id=user_id,
            session_id=session_id,
            doc_type=result.doc_type,
            doc_number=result.doc_number,
            verified=result.verified,
            verify_result=result.to_dict(),
        )
        results.append(result.to_dict())

        if not result.verified:
            all_verified = False
            failures.append(f"{result.doc_type}: {result.failure_reason}")
        elif not result.name_matched:
            all_verified = False
            failures.append(f"{result.doc_type}: Name mismatch — {result.failure_reason}")

    final_status = "VERIFIED" if all_verified else "FAILED"
    session_status = "DOC_VERIFIED" if all_verified else "DOC_FAILED"

    kyc_db.complete_session(session_id, session_status, failure_reason="; ".join(failures) if failures else None)
    kyc_db.update_user_kyc_status(user_id, final_status)
    kyc_db.audit("KYC_COMPLETED", user_id=user_id, session_id=session_id,
             detail={"status": final_status, "failures": failures})

    response = {
        "success": all_verified,
        "kyc_status": final_status,
        "message": (
            "KYC verification successful. User is now fully verified."
            if all_verified
            else f"KYC verification failed. Issues: {'; '.join(failures)}"
        ),
        "document_results": results,
        "session_id": session_id,
        "next_step": (
            "User is verified. Use fetch_verified_profile to retrieve full profile."
            if all_verified
            else "Fix the issues above and use re_verify_kyc to try again."
        ),
    }

    if all_verified:
        agent = registry_service.generate_or_get_agent_id(user)
        response["agent_id"] = agent["agent_id"]
        response["message"] += f" Agent ID generated: {agent['agent_id']}"

    return response

# ─────────────────────────────────────────────────────────
# Tool 3A — verify_and_generate_id
# ─────────────────────────────────────────────────────────

def verify_and_generate_id(user_id: str, session_id: str, otp: str) -> dict:
    """
    Convenience wrapper around confirm_kyc_otp.
    If KYC passes, returns the newly generated unique agent_id prominently.
    """
    res = confirm_kyc_otp(user_id, session_id, otp)
    if res.get("success") and "agent_id" in res:
        return {
            "success": True,
            "message": "User verified successfully.",
            "agent_id": res["agent_id"],
            "kyc_status": res["kyc_status"]
        }
    return res

# ─────────────────────────────────────────────────────────
# Tool 4 — check_kyc_status
# ─────────────────────────────────────────────────────────

def check_kyc_status(user_id: str) -> dict:
    """Return current KYC status and latest session summary for a user."""
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    sessions = kyc_db.get_sessions_for_user(user_id)
    latest = sessions[0] if sessions else None

    return {
        "success": True,
        "user_id": user_id,
        "kyc_status": user["kyc_status"],
        "email": user["email"],
        "full_name": user["full_name"],
        "total_sessions": len(sessions),
        "latest_session": {
            "session_id": latest["id"],
            "type": latest["session_type"],
            "status": latest["status"],
            "initiated_at": latest["initiated_at"],
            "completed_at": latest["completed_at"],
            "failure_reason": latest["failure_reason"],
        } if latest else None,
    }


# ─────────────────────────────────────────────────────────
# Tool 5 — fetch_verified_profile
# ─────────────────────────────────────────────────────────

def fetch_verified_profile(user_id: str) -> dict:
    """
    Return the full verified profile including all successfully
    verified document data. Only available for VERIFIED users.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] != "VERIFIED":
        return _err(
            f"Profile not available. User KYC status is '{user['kyc_status']}'. "
            "Only VERIFIED users have a fetchable profile."
        )

    docs = kyc_db.get_documents_for_user(user_id)
    # Only include the most recent verified doc per type
    seen_types: set[str] = set()
    verified_docs = []
    for doc in docs:
        if doc["verified"] and doc["doc_type"] not in seen_types:
            seen_types.add(doc["doc_type"])
            verified_docs.append({
                "doc_type": doc["doc_type"],
                "doc_number_masked": doc["doc_number"],
                "verified_at": doc["verified_at"],
                "extracted_data": doc["verify_result"].get("extracted_data", {}),
            })

    agent = registry_service.get_registered_agent_id(user_id)
    agent_id = agent["agent_id"] if agent.get("success") else None

    return {
        "success": True,
        "user": _safe_user(user),
        "agent_id": agent_id,
        "verified_documents": verified_docs,
        "profile_complete": len(verified_docs) > 0,
    }


# ─────────────────────────────────────────────────────────
# Tool 6 — re_verify_kyc
# ─────────────────────────────────────────────────────────

def re_verify_kyc(user_id: str, documents: dict) -> dict:
    """
    Initiate a re-verification session.
    Allows adding new document types or replacing existing ones.
    Resets user to INITIATED status; full OTP + doc verify flow runs again.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] == "PENDING":
        return _err(
            "User has never started KYC. Use initiate_kyc instead of re_verify_kyc."
        )

    if not documents:
        return _err("No documents provided for re-verification.")

    # Format validation
    format_errors = []
    for doc_type, payload in documents.items():
        verifier = get_verifier(doc_type)
        if not verifier:
            format_errors.append(f"'{doc_type}' is not a supported document type.")
            continue
        valid, err = verifier.validate_format(payload)
        if not valid:
            format_errors.append(f"{verifier.display_name}: {err}")

    if format_errors:
        return _err("Document format validation failed.", errors=format_errors)

    # Cancel existing OTP_PENDING sessions
    active = kyc_db.get_active_session_for_user(user_id)
    if active:
        kyc_db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by re-verify session.")

    session = kyc_db.create_kyc_session(user_id, "RE_VERIFY", documents)
    kyc_db.update_user_kyc_status(user_id, "INITIATED")
    kyc_db.audit("KYC_REVERIFY_INITIATED", user_id=user_id, session_id=session["id"],
             detail={"doc_types": list(documents.keys())})

    return {
        "success": True,
        "message": "Re-verification session initiated.",
        "session_id": session["id"],
        "otp_instruction": (
            f"Submit OTP via confirm_kyc_otp to proceed. "
            f"Session is valid for {OTP_VALIDITY_MINUTES} minutes."
        ),
        "documents_received": list(documents.keys()),
    }


# ─────────────────────────────────────────────────────────
# Tool 7 — list_registered_users
# ─────────────────────────────────────────────────────────

def list_registered_users(kyc_status_filter: str | None = None) -> dict:
    """
    List all registered users, optionally filtered by KYC status.
    kyc_status_filter: PENDING | INITIATED | VERIFIED | FAILED | BLOCKED
    """
    users = kyc_db.list_all_users()

    if kyc_status_filter:
        kyc_status_filter = kyc_status_filter.upper()
        users = [u for u in users if u["kyc_status"] == kyc_status_filter]

    return {
        "success": True,
        "total": len(users),
        "filter": kyc_status_filter or "ALL",
        "users": [_safe_user(u) for u in users],
    }


# ─────────────────────────────────────────────────────────
# Tool 8 — list_supported_document_types
# ─────────────────────────────────────────────────────────

def list_supported_document_types() -> dict:
    """Return all document types the server can verify, with their required fields."""
    return {
        "success": True,
        "supported_documents": supported_doc_types(),
        "note": (
            "To add a new document type, subclass BaseVerifier and register it in verifiers/registry.py."
        ),
    }


# ─────────────────────────────────────────────────────────
# Tool 9 — register_agent
# ─────────────────────────────────────────────────────────

def register_agent(
    user_id: str,
    agent_name: str,
    description: str = "",
    capabilities: list[str] | None = None,
) -> dict:
    """
    Register a customer-controlled agent with AR so it can be verified before
    traffic is forwarded to another MCP server such as an ecommerce server.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(
            f"User '{user_id}' not found. Register the customer with register_user first."
        )

    agent_name = agent_name.strip()
    if not agent_name:
        return _err("agent_name is required.")

    normalized_capabilities = _normalize_capabilities(capabilities)
    agent = kyc_db.create_agent(
        user_id=user_id,
        agent_name=agent_name,
        description=description.strip(),
        capabilities=normalized_capabilities,
    )
    kyc_db.audit(
        "AGENT_REGISTERED",
        user_id=user_id,
        detail={
            "agent_id": agent["id"],
            "agent_name": agent_name,
            "capabilities": normalized_capabilities,
        },
    )

    return {
        "success": True,
        "message": (
            "Agent registered with AR. Use verify_agent_capability before "
            "routing ecommerce traffic for this agent."
        ),
        "agent": _safe_agent(agent),
        "next_step": "Call verify_agent_capability with this agent_id before ecommerce routing.",
    }


# ─────────────────────────────────────────────────────────
# Tool 10 — verify_agent_capability
# ─────────────────────────────────────────────────────────

def verify_agent_capability(
    agent_id: str,
    capability: str = "ECOMMERCE_ACCESS",
) -> dict:
    """
    Check whether an agent is registered with AR and allowed to be routed to an
    ecommerce MCP. If the agent is unknown, instruct the client to register first.
    """
    agent_id = agent_id.strip()
    requested_capability = capability.strip().upper() or "ECOMMERCE_ACCESS"

    if not agent_id:
        return {
            "success": False,
            "allowed_to_route": False,
            "route_decision": "BLOCK_REGISTER_AGENT",
            "registration_required": True,
            "message": (
                "Agent is not registered with AR. Please register the agent first "
                "before routing traffic to the ecommerce MCP."
            ),
            "next_step": "Call register_agent to obtain an AR-managed agent_id.",
        }

    agent = kyc_db.get_agent_by_id(agent_id)
    if not agent:
        kyc_db.audit(
            "AGENT_ROUTING_BLOCKED",
            detail={"agent_id": agent_id, "capability": requested_capability, "reason": "NOT_REGISTERED"},
        )
        return {
            "success": False,
            "allowed_to_route": False,
            "route_decision": "BLOCK_REGISTER_AGENT",
            "registration_required": True,
            "message": (
                "Agent is not registered with AR. Please register the agent first "
                "before routing traffic to the ecommerce MCP."
            ),
            "next_step": "Call register_agent to obtain an AR-managed agent_id.",
        }

    if agent["status"] != "ACTIVE":
        kyc_db.audit(
            "AGENT_ROUTING_BLOCKED",
            user_id=agent["user_id"],
            detail={"agent_id": agent_id, "capability": requested_capability, "reason": "INACTIVE"},
        )
        return {
            "success": False,
            "allowed_to_route": False,
            "route_decision": "BLOCK_AGENT_INACTIVE",
            "registration_required": False,
            "message": "Agent is registered with AR but is not active.",
            "agent": _safe_agent(agent),
        }

    if requested_capability not in agent["capabilities"]:
        kyc_db.audit(
            "AGENT_ROUTING_BLOCKED",
            user_id=agent["user_id"],
            detail={
                "agent_id": agent_id,
                "capability": requested_capability,
                "reason": "CAPABILITY_MISSING",
            },
        )
        return {
            "success": False,
            "allowed_to_route": False,
            "route_decision": "BLOCK_CAPABILITY_MISSING",
            "registration_required": False,
            "message": (
                f"Agent is registered with AR but does not have capability "
                f"'{requested_capability}'."
            ),
            "agent": _safe_agent(agent),
            "requested_capability": requested_capability,
        }

    kyc_db.audit(
        "AGENT_ROUTING_ALLOWED",
        user_id=agent["user_id"],
        detail={"agent_id": agent_id, "capability": requested_capability},
    )
    return {
        "success": True,
        "allowed_to_route": True,
        "route_decision": "ALLOW",
        "registration_required": False,
        "message": (
            "Agent is registered with AR and may be routed to the ecommerce MCP."
        ),
        "agent": _safe_agent(agent),
        "verified_capability": requested_capability,
    }


# ─────────────────────────────────────────────────────────
# Tool 11 — register_service
# ─────────────────────────────────────────────────────────

def register_service(service_name: str, service_url: str, description: str = "",
                     capabilities: list[str] | None = None) -> dict:
    """Register an ecommerce or other service with AR."""
    service_name = service_name.strip()
    service_url = service_url.strip()
    if not service_name:
        return _err("service_name is required.")
    if not service_url:
        return _err("service_url is required.")

    caps = capabilities or ["ECOMMERCE"]
    service = kyc_db.register_service(service_name, service_url, description, caps)
    kyc_db.audit("SERVICE_REGISTERED", detail={"service_name": service_name, "service_url": service_url})

    return {
        "success": True,
        "message": f"Service '{service_name}' registered with AR.",
        "service": {
            "service_id": service["id"],
            "service_name": service["service_name"],
            "service_url": service["service_url"],
            "capabilities": service["capabilities"],
            "status": service["status"],
        }
    }

def verify_traffic(agent_id: str, service_name: str) -> dict:
    """
    Verify that an agent is allowed to access a specific registered service.
    Checks: agent exists, is active, has matching capability.
    """
    agent_id = agent_id.strip()
    service_name = service_name.strip()

    if not agent_id:
        return {"success": False, "allowed": False, "reason": "agent_id is required"}
    if not service_name:
        return {"success": False, "allowed": False, "reason": "service_name is required"}

    # Check service exists
    service = kyc_db.get_service_by_name(service_name)
    if not service:
        return {"success": False, "allowed": False, "reason": f"Service '{service_name}' not registered with AR"}

    # Check agent exists and is active
    agent = kyc_db.get_agent_by_id(agent_id)
    if not agent:
        return {"success": False, "allowed": False, "reason": "Agent not registered with AR"}
    if agent["status"] != "ACTIVE":
        return {"success": False, "allowed": False, "reason": "Agent is not active"}

    # Check capability match — agent needs ECOMMERCE_ACCESS for ecommerce services
    required_cap = "ECOMMERCE_ACCESS"
    if required_cap not in agent["capabilities"]:
        return {"success": False, "allowed": False,
                "reason": f"Agent lacks {required_cap} capability"}

    kyc_db.audit("TRAFFIC_VERIFIED", user_id=agent["user_id"],
                 detail={"agent_id": agent_id, "service": service_name, "decision": "ALLOW"})

    return {
        "success": True,
        "allowed": True,
        "agent_id": agent_id,
        "agent_name": agent["agent_name"],
        "service_name": service_name,
        "message": f"Agent verified for {service_name}."
    }

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _err(message: str, **kwargs) -> dict:
    return {"success": False, "error": message, **kwargs}


def _safe_user(user: dict) -> dict:
    """Return user dict safe for external consumption."""
    return {
        "user_id": user["id"],
        "full_name": user["full_name"],
        "email": user["email"],
        "phone": user.get("phone"),
        "kyc_status": user["kyc_status"],
        "created_at": user["created_at"],
        "updated_at": user["updated_at"],
    }


def _safe_agent(agent: dict) -> dict:
    return {
        "agent_id": agent["id"],
        "user_id": agent["user_id"],
        "agent_name": agent["agent_name"],
        "description": agent["description"],
        "capabilities": agent["capabilities"],
        "status": agent["status"],
        "created_at": agent["created_at"],
        "updated_at": agent["updated_at"],
    }


def _normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    raw = capabilities or DEFAULT_AGENT_CAPABILITIES
    normalized: list[str] = []

    for capability in raw:
        if capability is None:
            continue
        value = str(capability).strip().upper()
        if value and value not in normalized:
            normalized.append(value)

    return normalized or DEFAULT_AGENT_CAPABILITIES.copy()
