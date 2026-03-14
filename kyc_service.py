"""
KYC Service Layer.

Contains all business logic called by MCP tools.
MCP tools are thin wrappers; this module holds the substance.
"""

from db import database as db
from verifiers.registry import get_verifier, supported_doc_types
from otp_service import verify_otp, FIXED_OTP, OTP_VALIDITY_MINUTES


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

    existing = db.get_user_by_email(email)
    if existing:
        return _err(
            f"A user with email '{email}' already exists. "
            f"User ID: {existing['id']}, KYC status: {existing['kyc_status']}."
        )

    user = db.create_user(full_name, email, phone)
    db.audit("USER_REGISTERED", user_id=user["id"], detail={"email": email})

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
    user = db.get_user_by_id(user_id)
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
    active = db.get_active_session_for_user(user_id)
    if active:
        db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by new session.")

    session = db.create_kyc_session(user_id, "INITIAL", documents)
    db.update_user_kyc_status(user_id, "INITIATED")
    db.audit("KYC_INITIATED", user_id=user_id, session_id=session["id"],
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
    user = db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    session = db.get_session_by_id(session_id)
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
        db.audit("OTP_FAILED", user_id=user_id, session_id=session_id, detail={"reason": otp_err})
        return _err(f"OTP verification failed: {otp_err}")

    db.confirm_session_otp(session_id)
    db.audit("OTP_CONFIRMED", user_id=user_id, session_id=session_id)

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
        db.save_document_result(
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

    db.complete_session(session_id, session_status, failure_reason="; ".join(failures) if failures else None)
    db.update_user_kyc_status(user_id, final_status)
    db.audit("KYC_COMPLETED", user_id=user_id, session_id=session_id,
             detail={"status": final_status, "failures": failures})

    return {
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


# ─────────────────────────────────────────────────────────
# Tool 4 — check_kyc_status
# ─────────────────────────────────────────────────────────

def check_kyc_status(user_id: str) -> dict:
    """Return current KYC status and latest session summary for a user."""
    user = db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    sessions = db.get_sessions_for_user(user_id)
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
    user = db.get_user_by_id(user_id)
    if not user:
        return _err(f"User '{user_id}' not found.")

    if user["kyc_status"] != "VERIFIED":
        return _err(
            f"Profile not available. User KYC status is '{user['kyc_status']}'. "
            "Only VERIFIED users have a fetchable profile."
        )

    docs = db.get_documents_for_user(user_id)
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

    return {
        "success": True,
        "user": _safe_user(user),
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
    user = db.get_user_by_id(user_id)
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
    active = db.get_active_session_for_user(user_id)
    if active:
        db.complete_session(active["id"], "DOC_FAILED", failure_reason="Superseded by re-verify session.")

    session = db.create_kyc_session(user_id, "RE_VERIFY", documents)
    db.update_user_kyc_status(user_id, "INITIATED")
    db.audit("KYC_REVERIFY_INITIATED", user_id=user_id, session_id=session["id"],
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
    users = db.list_all_users()

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
