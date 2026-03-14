"""
Database layer for KYC MCP Server.
Uses SQLite with full audit trail support.
"""

import os
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(
    os.environ.get(
        "KYC_DB_PATH",
        str(Path(__file__).parent.parent / "kyc_store.db"),
    )
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                full_name   TEXT NOT NULL,
                email       TEXT UNIQUE NOT NULL,
                phone       TEXT,
                kyc_status  TEXT NOT NULL DEFAULT 'PENDING',
                -- PENDING | INITIATED | VERIFIED | FAILED | BLOCKED
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kyc_sessions (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(id),
                session_type    TEXT NOT NULL,   -- INITIAL | RE_VERIFY
                status          TEXT NOT NULL DEFAULT 'OTP_PENDING',
                -- OTP_PENDING | OTP_CONFIRMED | DOC_VERIFIED | DOC_FAILED
                documents       TEXT NOT NULL DEFAULT '{}',   -- JSON blob
                otp_verified    INTEGER NOT NULL DEFAULT 0,
                initiated_at    TEXT NOT NULL,
                completed_at    TEXT,
                failure_reason  TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS kyc_documents (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(id),
                session_id      TEXT NOT NULL REFERENCES kyc_sessions(id),
                doc_type        TEXT NOT NULL,   -- AADHAAR | PAN | MOBILE
                doc_number      TEXT NOT NULL,
                verified        INTEGER NOT NULL DEFAULT 0,
                verify_result   TEXT,            -- JSON: verifier response
                verified_at     TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(session_id) REFERENCES kyc_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          TEXT PRIMARY KEY,
                user_id     TEXT,
                session_id  TEXT,
                event       TEXT NOT NULL,
                detail      TEXT,               -- JSON blob
                timestamp   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agents (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(id),
                agent_name      TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                capabilities    TEXT NOT NULL DEFAULT '[]',
                status          TEXT NOT NULL DEFAULT 'ACTIVE',
                -- ACTIVE | REVOKED
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON kyc_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_docs_session ON kyc_documents(session_id);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_agents_user ON agents(user_id);
            CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# User operations
# ──────────────────────────────────────────────

def create_user(full_name: str, email: str, phone: Optional[str] = None) -> dict:
    uid = new_id()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, full_name, email, phone, kyc_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
            (uid, full_name, email, phone, ts, ts)
        )
    return get_user_by_id(uid)


def get_user_by_id(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def update_user_kyc_status(user_id: str, status: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET kyc_status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), user_id)
        )


def list_all_users() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# KYC Session operations
# ──────────────────────────────────────────────

def create_kyc_session(user_id: str, session_type: str, documents: dict) -> dict:
    sid = new_id()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO kyc_sessions "
            "(id, user_id, session_type, status, documents, otp_verified, initiated_at) "
            "VALUES (?, ?, ?, 'OTP_PENDING', ?, 0, ?)",
            (sid, user_id, session_type, json.dumps(documents), ts)
        )
    return get_session_by_id(sid)


def get_session_by_id(session_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM kyc_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["documents"] = json.loads(d["documents"])
    return d


def get_active_session_for_user(user_id: str) -> Optional[dict]:
    """Return the most recent OTP_PENDING session for a user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM kyc_sessions WHERE user_id = ? AND status = 'OTP_PENDING' "
            "ORDER BY initiated_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["documents"] = json.loads(d["documents"])
    return d


def confirm_session_otp(session_id: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE kyc_sessions SET otp_verified = 1, status = 'OTP_CONFIRMED' WHERE id = ?",
            (session_id,)
        )


def complete_session(session_id: str, status: str, failure_reason: Optional[str] = None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE kyc_sessions SET status = ?, completed_at = ?, failure_reason = ? WHERE id = ?",
            (status, now_iso(), failure_reason, session_id)
        )


def get_sessions_for_user(user_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM kyc_sessions WHERE user_id = ? ORDER BY initiated_at DESC",
            (user_id,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["documents"] = json.loads(d["documents"])
        result.append(d)
    return result


# ──────────────────────────────────────────────
# Document record operations
# ──────────────────────────────────────────────

def save_document_result(
    user_id: str,
    session_id: str,
    doc_type: str,
    doc_number: str,
    verified: bool,
    verify_result: dict
):
    did = new_id()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO kyc_documents "
            "(id, user_id, session_id, doc_type, doc_number, verified, verify_result, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did, user_id, session_id, doc_type, doc_number,
                int(verified), json.dumps(verify_result),
                now_iso() if verified else None
            )
        )


def get_documents_for_user(user_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM kyc_documents WHERE user_id = ? ORDER BY verified_at DESC",
            (user_id,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["verify_result"] = json.loads(d["verify_result"]) if d["verify_result"] else {}
        result.append(d)
    return result


# ──────────────────────────────────────────────
# Agent operations
# ──────────────────────────────────────────────

def create_agent(
    user_id: str,
    agent_name: str,
    description: str,
    capabilities: list[str],
) -> dict:
    aid = new_id()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO agents "
            "(id, user_id, agent_name, description, capabilities, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (aid, user_id, agent_name, description, json.dumps(capabilities), ts, ts)
        )
    return get_agent_by_id(aid)


def get_agent_by_id(agent_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if not row:
        return None
    agent = dict(row)
    agent["capabilities"] = json.loads(agent["capabilities"]) if agent["capabilities"] else []
    return agent


# ──────────────────────────────────────────────
# Audit log
# ──────────────────────────────────────────────

def audit(event: str, user_id: Optional[str] = None, session_id: Optional[str] = None, detail: Optional[dict] = None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (id, user_id, session_id, event, detail, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), user_id, session_id, event, json.dumps(detail or {}), now_iso())
        )
