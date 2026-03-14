"""
AR Client for the ecommerce server.

Reads the KYA/AR SQLite database directly so the ecommerce MCP can:
- verify agent traffic before allowing cart or checkout access
- ensure the SoleSpace service is registered with AR
- fetch user profile details for checkout
"""

import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
KYC_DB_PATH = os.path.normpath(os.path.join(_BASE, "../../kyc_store.db"))
PROJECT_ROOT = os.path.normpath(os.path.join(_BASE, "../.."))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from db.database import init_db as init_ar_db
except Exception:
    init_ar_db = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(KYC_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _bootstrap_ar_schema() -> None:
    if init_ar_db is None:
        return
    try:
        init_ar_db()
    except Exception as exc:
        logger.warning("Failed to initialize AR schema: %s", exc)


def _ensure_registered_services_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registered_services (
            id              TEXT PRIMARY KEY,
            service_name    TEXT UNIQUE NOT NULL,
            service_url     TEXT NOT NULL,
            description     TEXT DEFAULT '',
            capabilities    TEXT NOT NULL DEFAULT '[]',
            api_key         TEXT,
            status          TEXT NOT NULL DEFAULT 'ACTIVE',
            registered_at   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_services_name ON registered_services(service_name)"
    )


def _decode_row(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if not row:
        return None
    data = dict(row)
    for field in json_fields:
        raw_value = data.get(field)
        if not raw_value:
            data[field] = []
            continue
        try:
            data[field] = json.loads(raw_value)
        except json.JSONDecodeError:
            logger.warning("Failed to decode JSON field '%s': %r", field, raw_value)
            data[field] = []
    return data


def ensure_service_registered(
    service_name: str = "solespace",
    service_url: str = "http://localhost:8001",
    description: str = "SoleSpace ecommerce MCP server",
    capabilities: list[str] | None = None,
) -> dict:
    """
    Ensure the ecommerce service is present in AR's registered_services table.
    Safe to call repeatedly.
    """
    normalized_name = service_name.strip()
    normalized_url = service_url.strip()
    caps = capabilities or ["ECOMMERCE"]

    if not normalized_name:
        return {"success": False, "registered": False, "reason": "service_name is required"}
    if not normalized_url:
        return {"success": False, "registered": False, "reason": "service_url is required"}

    try:
        _bootstrap_ar_schema()
        with _connect() as conn:
            _ensure_registered_services_table(conn)
            existing = conn.execute(
                "SELECT * FROM registered_services WHERE service_name = ?",
                (normalized_name,),
            ).fetchone()
            if existing:
                service = _decode_row(existing, ("capabilities",))
                return {"success": True, "registered": False, "service": service}

            service_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO registered_services
                (id, service_name, service_url, description, capabilities, api_key, status, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    service_id,
                    normalized_name,
                    normalized_url,
                    description,
                    json.dumps(caps),
                    None,
                    _now_iso(),
                ),
            )
            service = conn.execute(
                "SELECT * FROM registered_services WHERE id = ?",
                (service_id,),
            ).fetchone()
            return {
                "success": True,
                "registered": True,
                "service": _decode_row(service, ("capabilities",)),
            }
    except sqlite3.Error as exc:
        logger.warning("Failed to ensure AR service registration: %s", exc)
        return {
            "success": False,
            "registered": False,
            "reason": f"AR database not available: {exc}",
        }


def verify_agent(
    agent_id: str,
    required_capability: str = "ECOMMERCE_ACCESS",
    service_name: str = "solespace",
) -> dict:
    """
    Verify an agent is registered with AR and has the required capability.
    Also ensures the target service is registered and active.
    """
    normalized_agent_id = agent_id.strip()
    normalized_capability = (required_capability or "ECOMMERCE_ACCESS").strip().upper()
    normalized_service = service_name.strip() or "solespace"

    if not normalized_agent_id:
        return {"allowed": False, "reason": "agent_id is required"}

    if not os.path.exists(KYC_DB_PATH):
        return {"allowed": False, "reason": "AR database not available"}

    try:
        _bootstrap_ar_schema()
        with _connect() as conn:
            _ensure_registered_services_table(conn)

            service_row = conn.execute(
                "SELECT * FROM registered_services WHERE service_name = ?",
                (normalized_service,),
            ).fetchone()
            service = _decode_row(service_row, ("capabilities",))
            if not service:
                return {
                    "allowed": False,
                    "reason": f"Service '{normalized_service}' is not registered with AR",
                }
            if service.get("status") != "ACTIVE":
                return {
                    "allowed": False,
                    "reason": f"Service '{normalized_service}' is not active in AR",
                }

            agent_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?",
                (normalized_agent_id,),
            ).fetchone()
            agent = _decode_row(agent_row, ("capabilities",))
            if not agent:
                return {"allowed": False, "reason": "Agent not registered with AR"}
            if agent.get("status") != "ACTIVE":
                return {"allowed": False, "reason": "Agent is not active"}

            capabilities = agent.get("capabilities", [])
            if normalized_capability not in capabilities:
                return {
                    "allowed": False,
                    "reason": f"Agent lacks required capability '{normalized_capability}'",
                    "capabilities": capabilities,
                }

            user_row = conn.execute(
                "SELECT full_name, email, phone, kyc_status FROM users WHERE id = ?",
                (agent["user_id"],),
            ).fetchone()
            user = dict(user_row) if user_row else {}
            if not user:
                return {"allowed": False, "reason": "Agent owner not found in KYA"}
            if user.get("kyc_status") != "VERIFIED":
                return {"allowed": False, "reason": "Agent owner is not KYC verified"}

            return {
                "allowed": True,
                "agent_id": normalized_agent_id,
                "agent_name": agent.get("agent_name", ""),
                "user_id": agent.get("user_id", ""),
                "user_name": user.get("full_name", ""),
                "user_email": user.get("email", ""),
                "user_phone": user.get("phone", "") or "",
                "capabilities": capabilities,
                "verified_capability": normalized_capability,
                "service_name": normalized_service,
            }
    except sqlite3.Error as exc:
        logger.warning("Failed to verify agent against AR database: %s", exc)
        return {"allowed": False, "reason": f"AR database not available: {exc}"}


def get_agent_user_info(agent_id: str) -> dict | None:
    """
    Get the user profile associated with an agent_id.
    Used to populate customer details in Pine Labs checkout.
    """
    normalized_agent_id = agent_id.strip()
    if not normalized_agent_id or not os.path.exists(KYC_DB_PATH):
        return None

    try:
        _bootstrap_ar_schema()
        with _connect() as conn:
            agent_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?",
                (normalized_agent_id,),
            ).fetchone()
            agent = _decode_row(agent_row, ("capabilities",))
            if not agent:
                return None

            user_row = conn.execute(
                "SELECT full_name, email, phone FROM users WHERE id = ?",
                (agent["user_id"],),
            ).fetchone()
            if not user_row:
                return None

            user = dict(user_row)
            return {
                "full_name": user.get("full_name", ""),
                "email": user.get("email", ""),
                "phone": user.get("phone", "") or "",
            }
    except sqlite3.Error as exc:
        logger.warning("Failed to read AR database: %s", exc)
        return None
