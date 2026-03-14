"""
Registry Database Layer.
Stores the mapping of KYC user_id → unique agent_id.
Agent IDs are issued only upon successful KYC verification.
"""

import os
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REGISTRY_DB_PATH = Path(__file__).parent.parent / "agent_registry.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(REGISTRY_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_registry_db():
    """Create the agent_registry table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_registry (
                id          TEXT PRIMARY KEY,
                user_id     TEXT UNIQUE NOT NULL,
                agent_id    TEXT UNIQUE NOT NULL,
                full_name   TEXT NOT NULL,
                email       TEXT NOT NULL,
                phone       TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_registry_user ON agent_registry(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_registry_agent ON agent_registry(agent_id)"
        )


def _generate_agent_id(user_id: str, full_name: str) -> str:
    """
    Generate a unique agent ID for a user.
    Format: <name_slug>_agent-<hash>@pinelabsUPAI
    """
    import re
    # Cleaned: alphanumeric only, lowercase, spaces removed
    cleaned_name = re.sub(r'[^a-z0-9]', '', full_name.lower())
    name_slug = cleaned_name if cleaned_name else "user"
    
    salt = "pinelabs_kya_secret_salt_2026"
    raw_str = f"{uuid.uuid4()}{salt}"
    unique_part = hashlib.sha256(raw_str.encode()).hexdigest()[:8]
    
    return f"{name_slug}_agent-{unique_part}@pinelabsUPAI"



def create_agent(user_id: str, full_name: str, email: str, phone: Optional[str] = None) -> dict:
    agent_id = _generate_agent_id(user_id, full_name)
    record_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agent_registry "
            "(id, user_id, agent_id, full_name, email, phone, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, user_id, agent_id, full_name, email, phone, ts)
        )

    return get_agent_by_user_id(user_id)


def get_agent_by_user_id(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM agent_registry WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_agent_by_agent_id(agent_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM agent_registry WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    return dict(row) if row else None


def list_all_agents() -> list[dict]:
    """Return all registered agents."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_registry ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]

