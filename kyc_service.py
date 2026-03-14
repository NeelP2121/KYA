"""
KYC Service Layer.

Contains all business logic called by MCP tools.
MCP tools are thin wrappers; this module holds the substance.
"""

from db import database as kyc_db
from verifiers.registry import supported_doc_types
from otp_service import verify_otp, OTP_VALIDITY_MINUTES
import registry_service


DEFAULT_AGENT_CAPABILITIES = ["ECOMMERCE_ACCESS", "CHECKOUT", "PAYMENT"]


# ─────────────────────────────────────────────────────────
# Tool 1 — register_user
# ─────────────────────────────────────────────────────────

def register_user(full_name: str = "", email: str = "", phone: str | None = None) -> dict:
    """
    Register a new user for the simplified phone-based KYC flow.
    Returns user record.
    """
    phone = _normalise_phone(phone or "")
    email = email.strip().lower()

    if not phone:
        return _err("phone is required.")
    if not _is_valid_phone(phone):
        return _err("phone must be a valid 10-digit mobile number.")

    existing_by_phone = kyc_db.get_user_by_phone(phone)
    if existing_by_phone:
        return {
            "success": True,
            "message": f"User with phone '{phone}' already exists.",
            "user": _safe_user(existing_by_phone),
            "next_step": "Call initiate_kyc with this user_id to receive a dummy OTP challenge.",
        }

    full_name = full_name.strip() or f"User {phone[-4:]}"
    if not email:
        email = f"user_{phone}@mock-kyc.local"

    existing = kyc_db.get_user_by_email(email)
    if existing:
        email = f"user_{phone}_{existing['id'][:8]}@mock-kyc.local"

    user = kyc_db.create_user(full_name, email, phone)
    kyc_db.audit("USER_REGISTERED", user_id=user["id"], detail={"email": email, "phone": phone})

    return {
        "success": True,
        "message": f"User for phone '{phone}' registered successfully.",
        "user": _safe_user(user),
        "next_step": "Call initiate_kyc with this user_id to receive a dummy OTP challenge.",
    }


# ─────────────────────────────────────────────────────────
# Tool 2 — initiate_kyc
# ─────────────────────────────────────────────────────────

def initiate_kyc(user_id: str, documents: dict | None = None) -> dict:
    """
    Step 1 of the simplified KYC flow: create a session and ask for any OTP.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] == "VERIFIED":
        return _err(
            "User is already KYC verified. "
            "Use re_verify_kyc if you want to trigger the OTP flow again."
        )

    # Cancel any existing OTP_PENDING sessions
    active = kyc_db.get_active_session_for_user(user_id)
    if active:
        kyc_db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by new session.")

    session = kyc_db.create_kyc_session(
        user_id,
        "INITIAL",
        {"MOBILE": {"mobile_number": user.get("phone") or ""}},
    )
    kyc_db.update_user_kyc_status(user_id, "INITIATED")
    kyc_db.audit("KYC_INITIATED", user_id=user_id, session_id=session["id"],
             detail={"flow": "PHONE_OTP_ONLY", "phone": user.get("phone")})

    return {
        "success": True,
        "message": "KYC session initiated. Confirm with any OTP to proceed.",
        "session_id": session["id"],
        "otp_instruction": (
            f"Submit any OTP via confirm_kyc_otp. "
            f"Session is valid for {OTP_VALIDITY_MINUTES} minutes."
        ),
        "documents_received": [],
    }


# ─────────────────────────────────────────────────────────
# Tool 3 — confirm_kyc_otp
# ─────────────────────────────────────────────────────────

def confirm_kyc_otp(user_id: str, session_id: str, otp: str) -> dict:
    """
    Step 2 of the simplified KYC flow: accept any OTP and mark the user verified.
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
            return _err("OTP already confirmed for this session.")
        return _err(f"Session is in status '{session['status']}' and cannot accept OTP.")

    # Verify OTP
    otp_valid, otp_err = verify_otp(otp, session["initiated_at"])
    if not otp_valid:
        kyc_db.audit("OTP_FAILED", user_id=user_id, session_id=session_id, detail={"reason": otp_err})
        return _err(f"OTP verification failed: {otp_err}")

    kyc_db.confirm_session_otp(session_id)
    kyc_db.audit("OTP_CONFIRMED", user_id=user_id, session_id=session_id)

    result = {
        "doc_type": "MOBILE",
        "doc_number": _mask_phone(user.get("phone") or ""),
        "verified": True,
        "name_matched": True,
        "extracted_data": {
            "phone": user.get("phone") or "",
            "verification_mode": "dummy_otp",
        },
        "failure_reason": None,
    }
    if user.get("phone"):
        kyc_db.save_document_result(
            user_id=user_id,
            session_id=session_id,
            doc_type="MOBILE",
            doc_number=result["doc_number"],
            verified=True,
            verify_result=result,
        )

    final_status = "VERIFIED"
    session_status = "DOC_VERIFIED"

    kyc_db.complete_session(session_id, session_status, failure_reason=None)
    kyc_db.update_user_kyc_status(user_id, final_status)
    kyc_db.audit("KYC_COMPLETED", user_id=user_id, session_id=session_id,
             detail={"status": final_status, "flow": "PHONE_OTP_ONLY"})

    response = {
        "success": True,
        "kyc_status": final_status,
        "message": "KYC verification successful. User is now fully verified.",
        "document_results": [result],
        "session_id": session_id,
        "next_step": "KYC is VERIFIED. Call register_agent to create an AR-managed agent_id for ecommerce.",
    }

    agent = registry_service.generate_or_get_agent_id(user)
    response["agent_id"] = agent["agent_id"]
    response["registry_agent_id"] = agent["agent_id"]
    response["message"] += (
        f" Registry agent ID generated: {agent['agent_id']}. "
        "For ecommerce access, call register_agent to create the AR-managed agent ID."
    )

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
            "message": (
                "User verified successfully. "
                "Call register_agent next to create the AR-managed agent_id for ecommerce."
            ),
            "agent_id": res["agent_id"],
            "registry_agent_id": res["agent_id"],
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

def re_verify_kyc(user_id: str, documents: dict | None = None) -> dict:
    """
    Re-trigger the simplified OTP flow for an existing user.
    """
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] == "PENDING":
        return _err(
            "User has never started KYC. Use initiate_kyc instead of re_verify_kyc."
        )

    # Cancel existing OTP_PENDING sessions
    active = kyc_db.get_active_session_for_user(user_id)
    if active:
        kyc_db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by re-verify session.")

    session = kyc_db.create_kyc_session(
        user_id,
        "RE_VERIFY",
        {"MOBILE": {"mobile_number": user.get("phone") or ""}},
    )
    kyc_db.update_user_kyc_status(user_id, "INITIATED")
    kyc_db.audit("KYC_REVERIFY_INITIATED", user_id=user_id, session_id=session["id"],
             detail={"flow": "PHONE_OTP_ONLY", "phone": user.get("phone")})

    return {
        "success": True,
        "message": "Re-verification session initiated. Confirm with any OTP to proceed.",
        "session_id": session["id"],
        "otp_instruction": (
            f"Submit any OTP via confirm_kyc_otp to proceed. "
            f"Session is valid for {OTP_VALIDITY_MINUTES} minutes."
        ),
        "documents_received": [],
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
    user_id: str = "",
    agent_name: str = "",
    description: str = "",
    capabilities: list[str] | None = None,
    phone: str = "",
) -> dict:
    """
    Register a customer-controlled agent with AR so it can be verified before
    traffic is forwarded to another MCP server such as an ecommerce server.
    """
    user = None
    user_id = user_id.strip()
    phone = _normalise_phone(phone)

    if user_id:
        user = kyc_db.get_user_by_id(user_id)
    elif phone:
        user = kyc_db.get_user_by_phone(phone)
        user_id = user["id"] if user else ""

    if not user:
        return _err(
            "User not found. Register the customer first with register_user using the phone number.",
            user_id=user_id or None,
            phone=phone or None,
        )
    if user["kyc_status"] != "VERIFIED":
        return _err(
            "User must complete KYC and reach VERIFIED status before registering an agent.",
            user_id=user_id,
            current_kyc_status=user["kyc_status"],
            next_step="Complete initiate_kyc and confirm_kyc_otp first.",
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

    user = kyc_db.get_user_by_id(agent["user_id"])
    if not user or user["kyc_status"] != "VERIFIED":
        kyc_db.audit(
            "AGENT_ROUTING_BLOCKED",
            user_id=agent["user_id"],
            detail={"agent_id": agent_id, "capability": requested_capability, "reason": "USER_NOT_VERIFIED"},
        )
        return {
            "success": False,
            "allowed_to_route": False,
            "route_decision": "BLOCK_USER_NOT_VERIFIED",
            "registration_required": False,
            "message": "Agent owner is not KYC verified. Complete KYC before ecommerce routing.",
            "agent": _safe_agent(agent),
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

    user = kyc_db.get_user_by_id(agent["user_id"])
    if not user or user["kyc_status"] != "VERIFIED":
        return {"success": False, "allowed": False, "reason": "Agent owner is not KYC verified"}

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


def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 4:
        return phone
    return "XXXXXX" + phone[-4:]


def _normalise_phone(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("+91"):
        phone = phone[3:]
    elif phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    elif phone.startswith("0") and len(phone) == 11:
        phone = phone[1:]
    return phone


def _is_valid_phone(phone: str) -> bool:
    return len(phone) == 10 and phone.isdigit() and phone[0] in {"6", "7", "8", "9"}


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
