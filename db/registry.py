"""
Agent Registry database layer.
Uses SQLite to store the mapping of User ID to their Unique Agent ID.
"""

import os
import sqlite3
import hashlib
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(
    os.environ.get(
        "AGENT_REGISTRY_DB_PATH",
        str(Path(__file__).parent.parent / "agent_registry.db"),
    )
)
SALT = "pinelabs_kya_secret_salt_2026"

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_registry_db():
    """Create the registry table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registered_agents (
                agent_id    TEXT PRIMARY KEY,
                user_id     TEXT UNIQUE NOT NULL,
                full_name   TEXT NOT NULL,
                email       TEXT,
                phone       TEXT,
                created_at  TEXT NOT NULL
            )
        """)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def generate_unique_agent_id(full_name: str) -> str:
    """
    Generate an ID: name_agent-[uuid_with_salt]@pinelabsUPAI
    The name is stripped of spaces and special chars, lowercase.
    The unique part is a short hash of (uuid4 + salt).
    """
    # Clean the name: keep only alphanumeric, lowercase
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', full_name).lower()
    if not clean_name:
        clean_name = "user"
        
    # Generate unique hash portion (first 8 chars of SHA256 of uuid + salt)
    unique_base = str(uuid.uuid4()) + SALT
    hash_hex = hashlib.sha256(unique_base.encode('utf-8')).hexdigest()[:8]
    
    return f"{clean_name}_agent-{hash_hex}@pinelabsUPAI"

def create_agent(user_id: str, full_name: str, email: str, phone: Optional[str]) -> dict:
    """Generate and store a new agent ID for a user. Returns the agent record."""
    agent_id = generate_unique_agent_id(full_name)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO registered_agents (agent_id, user_id, full_name, email, phone, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, user_id, full_name, email, phone, now_iso())
        )
    return get_agent_by_user_id(user_id)

def get_agent_by_user_id(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM registered_agents WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None

def get_agent_by_agent_id(agent_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM registered_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return dict(row) if row else None
