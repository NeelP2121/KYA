"""
MFOS Database Layer.
Separate SQLite file (mfos.db) from KYC store.
Handles merchants, transactions, and settlements.
"""

import sqlite3
import uuid
import json
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

MFOS_DB_PATH = Path(__file__).parent.parent / "mfos.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(MFOS_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_mfos_db():
    """Create all MFOS tables. Safe to call multiple times."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS merchants (
                merchant_id     TEXT PRIMARY KEY,
                agent_id        TEXT UNIQUE NOT NULL,   -- links to KYC registry agent_id
                kyc_user_id     TEXT NOT NULL,          -- links to KYC users.id
                business_name   TEXT NOT NULL,
                business_type   TEXT NOT NULL DEFAULT 'Retail',
                city            TEXT,
                state           TEXT,
                onboarded_at    TEXT NOT NULL,
                is_active       INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id   TEXT PRIMARY KEY,
                merchant_id      TEXT NOT NULL REFERENCES merchants(merchant_id),
                amount           REAL NOT NULL,
                payment_method   TEXT NOT NULL,   -- UPI | Card | Wallet | NetBanking
                status           TEXT NOT NULL,   -- success | failed | refunded
                timestamp        TEXT NOT NULL,
                source           TEXT DEFAULT 'manual'  -- manual | webhook | seed
            );

            CREATE TABLE IF NOT EXISTS settlements (
                settlement_id   TEXT PRIMARY KEY,
                merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id),
                amount          REAL NOT NULL,
                status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | PROCESSED | FAILED
                settlement_date TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
            CREATE INDEX IF NOT EXISTS idx_txn_timestamp ON transactions(timestamp);
            CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(status);
            CREATE INDEX IF NOT EXISTS idx_settle_merchant ON settlements(merchant_id);
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# Merchant operations
# ──────────────────────────────────────────────

def create_merchant(agent_id: str, kyc_user_id: str, business_name: str,
                    business_type: str = "Retail", city: str = "", state: str = "") -> dict:
    merchant_id = "M_" + new_id().replace("-", "")[:12].upper()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO merchants (merchant_id, agent_id, kyc_user_id, business_name, "
            "business_type, city, state, onboarded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (merchant_id, agent_id, kyc_user_id, business_name, business_type,
             city, state, now_iso())
        )
    return get_merchant_by_id(merchant_id)


def get_merchant_by_id(merchant_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,)
        ).fetchone()
    return dict(row) if row else None


def get_merchant_by_agent_id(agent_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM merchants WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    return dict(row) if row else None


def get_merchant_by_kyc_user_id(kyc_user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM merchants WHERE kyc_user_id = ?", (kyc_user_id,)
        ).fetchone()
    return dict(row) if row else None


def list_all_merchants() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM merchants ORDER BY onboarded_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# Transaction operations
# ──────────────────────────────────────────────

def insert_transaction(merchant_id: str, amount: float, payment_method: str,
                       status: str, timestamp: str, source: str = "manual",
                       transaction_id: str = None) -> dict:
    tid = transaction_id or ("TXN_" + new_id().replace("-", "")[:12].upper())
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO transactions "
            "(transaction_id, merchant_id, amount, payment_method, status, timestamp, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, merchant_id, amount, payment_method, status, timestamp, source)
        )
    return {"transaction_id": tid, "merchant_id": merchant_id, "amount": amount,
            "payment_method": payment_method, "status": status, "timestamp": timestamp}


def get_transactions(merchant_id: str, days_back: int = 30,
                     status_filter: str = None) -> list[dict]:
    query = """
        SELECT * FROM transactions
        WHERE merchant_id = ?
          AND timestamp >= datetime('now', ?)
    """
    params = [merchant_id, f"-{days_back} days"]
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY timestamp DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_transactions_for_window(merchant_id: str, minutes: int = 30) -> list[dict]:
    """Return all transactions within the last `minutes` minutes."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id = ? "
            "AND timestamp >= datetime('now', ?) ORDER BY timestamp DESC",
            (merchant_id, f"-{minutes} minutes")
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_transactions(merchant_id: str) -> list[dict]:
    """Return all transactions ever for a merchant, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id = ? ORDER BY timestamp ASC",
            (merchant_id,)
        ).fetchall()
    return [dict(r) for r in rows]




def get_revenue_for_date(merchant_id: str, target_date: str) -> float:
    """target_date as YYYY-MM-DD string."""
    with get_connection() as conn:
        val = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE merchant_id = ? AND date(timestamp) = ? AND status = 'success'",
            (merchant_id, target_date)
        ).scalar() if hasattr(conn, 'scalar') else None

        if val is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
                "WHERE merchant_id = ? AND date(timestamp) = ? AND status = 'success'",
                (merchant_id, target_date)
            ).fetchone()
            val = row["total"] if row else 0.0
    return float(val or 0.0)


def get_revenue_last_n_days(merchant_id: str, n: int = 7) -> float:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
            "WHERE merchant_id = ? AND timestamp >= datetime('now', ?) AND status = 'success'",
            (merchant_id, f"-{n} days")
        ).fetchone()
    return float(row["total"] if row else 0.0)


def get_payment_method_breakdown(merchant_id: str, days_back: int = 30) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT payment_method, SUM(amount) as total, COUNT(*) as count "
            "FROM transactions "
            "WHERE merchant_id = ? AND status = 'success' "
            "AND timestamp >= datetime('now', ?) "
            "GROUP BY payment_method",
            (merchant_id, f"-{days_back} days")
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_revenue_series(merchant_id: str, days_back: int = 7) -> list[dict]:
    """Returns day-by-day revenue for trend analysis."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date(timestamp) as day, COALESCE(SUM(amount), 0) as revenue, COUNT(*) as txn_count "
            "FROM transactions "
            "WHERE merchant_id = ? AND status = 'success' "
            "AND timestamp >= datetime('now', ?) "
            "GROUP BY date(timestamp) "
            "ORDER BY day ASC",
            (merchant_id, f"-{days_back} days")
        ).fetchall()
    return [dict(r) for r in rows]


def get_transaction_stats(merchant_id: str, days_back: int = 30) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT "
            "  COUNT(*) as total_count, "
            "  COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) as success_count, "
            "  COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) as failed_count, "
            "  COALESCE(SUM(CASE WHEN status='refunded' THEN 1 ELSE 0 END), 0) as refunded_count, "
            "  COALESCE(AVG(CASE WHEN status='success' THEN amount END), 0) as avg_txn_value "
            "FROM transactions "
            "WHERE merchant_id = ? AND timestamp >= datetime('now', ?)",
            (merchant_id, f"-{days_back} days")
        ).fetchone()
    return dict(row) if row else {}


# ──────────────────────────────────────────────
# Settlement operations
# ──────────────────────────────────────────────

def create_settlement(merchant_id: str, amount: float,
                      settlement_date: str = None) -> dict:
    sid = "SET_" + new_id().replace("-", "")[:12].upper()
    sdate = settlement_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settlements (settlement_id, merchant_id, amount, status, "
            "settlement_date, created_at) VALUES (?, ?, ?, 'PENDING', ?, ?)",
            (sid, merchant_id, amount, sdate, now_iso())
        )
    return {"settlement_id": sid, "merchant_id": merchant_id,
            "amount": amount, "status": "PENDING", "settlement_date": sdate}


def get_settlements(merchant_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM settlements WHERE merchant_id = ? ORDER BY created_at DESC",
            (merchant_id,)
        ).fetchall()
    return [dict(r) for r in rows]
