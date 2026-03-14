"""
AR Client — Stub for Ecommerce Server
=======================================
Provides agent verification and user-info lookups.

Currently a STUB that always allows access — agent verification
is skipped per project requirements. When ready, this module will
read the AR server's SQLite databases directly.
"""

import os
import sqlite3
import json
import logging

logger = logging.getLogger(__name__)

# Paths to AR databases (when verification is enabled)
_BASE = os.path.dirname(os.path.abspath(__file__))
KYC_DB_PATH = os.path.join(_BASE, "../../kyc_store.db")
AGENT_REGISTRY_DB = os.path.join(_BASE, "../../agent_registry.db")


def verify_agent(agent_id: str, required_capability: str = "ECOMMERCE_ACCESS") -> dict:
    """
    Verify an agent is registered with AR and has the required capability.

    STUB: Always returns allowed=True.
    """
    # Stub — always allow
    return {
        "allowed": True,
        "agent_id": agent_id,
        "agent_name": "stub-agent",
        "user_id": "stub-user",
        "user_name": "Stub User",
        "user_email": "",
        "user_phone": "",
        "capabilities": [required_capability],
    }


def get_agent_user_info(agent_id: str) -> dict | None:
    """
    Get the user profile associated with an agent_id.
    Used to populate customer details in Pine Labs checkout.

    STUB: Returns minimal info. When AR verification is enabled,
    this will read from the AR databases directly.
    """
    # Try to read from AR database if available
    db_path = os.path.normpath(KYC_DB_PATH)
    if not os.path.exists(db_path):
        logger.debug("AR database not found at %s, returning stub info", db_path)
        return {"full_name": "", "email": "", "phone": ""}

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Look up agent to get user_id
        agent = conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()

        if not agent:
            conn.close()
            return {"full_name": "", "email": "", "phone": ""}

        user = conn.execute(
            "SELECT full_name, email, phone FROM users WHERE id = ?",
            (agent["user_id"],),
        ).fetchone()

        conn.close()

        if user:
            return {
                "full_name": user["full_name"],
                "email": user["email"],
                "phone": user["phone"] or "",
            }
        return {"full_name": "", "email": "", "phone": ""}

    except Exception as e:
        logger.warning("Failed to read AR database: %s", e)
        return {"full_name": "", "email": "", "phone": ""}
