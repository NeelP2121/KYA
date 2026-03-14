"""
AML / Fraud Detection Database Layer.
Separate SQLite database (aml_alerts.db) for persisting fraud flags and alert history.
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

AML_DB_PATH = Path(__file__).parent.parent / "aml_alerts.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(AML_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_aml_db():
    """Create AML tables. Safe to call multiple times."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS aml_alerts (
                id              TEXT PRIMARY KEY,
                merchant_id     TEXT NOT NULL,
                rule_id         TEXT NOT NULL,
                rule_name       TEXT NOT NULL,
                severity        TEXT NOT NULL,   -- LOW | MEDIUM | HIGH | CRITICAL
                evidence        TEXT NOT NULL,   -- JSON blob
                triggered_at    TEXT NOT NULL,
                resolved        INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS aml_scan_log (
                id              TEXT PRIMARY KEY,
                merchant_id     TEXT NOT NULL,
                risk_score      INTEGER NOT NULL,
                risk_level      TEXT NOT NULL,
                flags_raised    INTEGER NOT NULL,
                scanned_at      TEXT NOT NULL,
                summary         TEXT            -- JSON blob of active flag ids
            );

            CREATE INDEX IF NOT EXISTS idx_aml_merchant ON aml_alerts(merchant_id);
            CREATE INDEX IF NOT EXISTS idx_aml_severity ON aml_alerts(severity);
            CREATE INDEX IF NOT EXISTS idx_aml_scan_merchant ON aml_scan_log(merchant_id);
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# Alert operations
# ──────────────────────────────────────────────

def save_alert(merchant_id: str, rule_id: str, rule_name: str,
               severity: str, evidence: dict) -> dict:
    alert_id = new_id()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO aml_alerts (id, merchant_id, rule_id, rule_name, severity, evidence, triggered_at, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (alert_id, merchant_id, rule_id, rule_name, severity, json.dumps(evidence), ts)
        )
    return {
        "alert_id": alert_id, "merchant_id": merchant_id,
        "rule_id": rule_id, "rule_name": rule_name,
        "severity": severity, "evidence": evidence,
        "triggered_at": ts, "resolved": False
    }


def get_alerts_for_merchant(merchant_id: str, unresolved_only: bool = False) -> list[dict]:
    query = "SELECT * FROM aml_alerts WHERE merchant_id = ?"
    params = [merchant_id]
    if unresolved_only:
        query += " AND resolved = 0"
    query += " ORDER BY triggered_at DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["alert_id"] = d.pop("id")   # rename primary key column → alert_id
        d["evidence"] = json.loads(d["evidence"])
        d["resolved"] = bool(d["resolved"])
        result.append(d)
    return result



def resolve_alert(alert_id: str):
    with get_connection() as conn:
        conn.execute("UPDATE aml_alerts SET resolved = 1 WHERE id = ?", (alert_id,))


def clear_alerts_for_merchant(merchant_id: str):
    """Remove all historical alerts (used before re-scan to avoid duplicates)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM aml_alerts WHERE merchant_id = ?", (merchant_id,))


def save_scan_log(merchant_id: str, risk_score: int, risk_level: str,
                  flags_raised: int, flag_ids: list[str]) -> dict:
    log_id = new_id()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO aml_scan_log (id, merchant_id, risk_score, risk_level, flags_raised, scanned_at, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (log_id, merchant_id, risk_score, risk_level, flags_raised, ts, json.dumps(flag_ids))
        )
    return {"log_id": log_id, "merchant_id": merchant_id, "risk_score": risk_score,
            "risk_level": risk_level, "flags_raised": flags_raised, "scanned_at": ts}


def get_latest_scan(merchant_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM aml_scan_log WHERE merchant_id = ? ORDER BY scanned_at DESC LIMIT 1",
            (merchant_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["summary"] = json.loads(d["summary"]) if d["summary"] else []
    return d
