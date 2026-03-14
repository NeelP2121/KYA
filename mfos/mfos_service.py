"""
MFOS Service Layer.
Business logic for all merchant financial tools.
Called by MCP tools in server.py — thin wrappers only there.
"""

import json
from mfos import mfos_db as db
from mfos.analytics import (
    compute_revenue_summary,
    compute_payment_breakdown,
    compute_cashflow_prediction,
    compute_credit_eligibility,
)
from mfos import aml_engine
from mfos import aml_db


# ─────────────────────────────────────────────────────────
# Tool M1 — onboard_merchant
# ─────────────────────────────────────────────────────────

def onboard_merchant(kyc_user_id: str, agent_id: str, business_name: str,
                     business_type: str = "Retail",
                     city: str = "", state: str = "") -> dict:
    """
    Onboard a KYC-verified user as a merchant.
    Requires a valid agent_id (only issued post-KYC verification).
    """
    if not kyc_user_id or not agent_id:
        return _err("kyc_user_id and agent_id are required.")
    if not business_name.strip():
        return _err("business_name is required.")

    # Idempotency: already onboarded?
    existing = db.get_merchant_by_agent_id(agent_id)
    if existing:
        return {
            "success": True,
            "already_onboarded": True,
            "message": f"Merchant already exists for this agent_id.",
            "merchant": _safe_merchant(existing),
        }

    merchant = db.create_merchant(
        agent_id=agent_id,
        kyc_user_id=kyc_user_id,
        business_name=business_name.strip(),
        business_type=business_type,
        city=city,
        state=state,
    )

    return {
        "success": True,
        "message": f"Merchant '{business_name}' onboarded successfully.",
        "merchant": _safe_merchant(merchant),
        "next_steps": [
            "Transactions will be recorded as payments come through /webhook/pine-labs.",
            "Use get_revenue_summary to query financial performance.",
            "Use check_credit_eligibility after processing payments to check working capital.",
        ],
    }


# ─────────────────────────────────────────────────────────
# Tool M2 — get_revenue_summary
# ─────────────────────────────────────────────────────────

def get_revenue_summary(merchant_id: str) -> dict:
    """Revenue summary: today, yesterday, 7-day, 30-day, WoW growth."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found. Use onboard_merchant first.")

    summary = compute_revenue_summary(merchant_id)
    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        **summary,
    }


# ─────────────────────────────────────────────────────────
# Tool M3 — get_payment_breakdown
# ─────────────────────────────────────────────────────────

def get_payment_breakdown(merchant_id: str, days_back: int = 30) -> dict:
    """Payment method breakdown by volume and transaction count."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found.")

    breakdown = compute_payment_breakdown(merchant_id, days_back)
    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        **breakdown,
    }


# ─────────────────────────────────────────────────────────
# Tool M4 — predict_cashflow
# ─────────────────────────────────────────────────────────

def predict_cashflow(merchant_id: str) -> dict:
    """Predict next 7 days revenue based on rolling trend."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found.")

    prediction = compute_cashflow_prediction(merchant_id)
    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        **prediction,
    }


# ─────────────────────────────────────────────────────────
# Tool M5 — check_credit_eligibility
# ─────────────────────────────────────────────────────────

def check_credit_eligibility(merchant_id: str) -> dict:
    """Working capital eligibility, health score, and credit limit."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found.")

    eligibility = compute_credit_eligibility(merchant_id)
    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        **eligibility,
    }


# ─────────────────────────────────────────────────────────
# Tool M6 — list_merchants  (admin/demo utility)
# ─────────────────────────────────────────────────────────

def list_merchants() -> dict:
    """List all onboarded merchants."""
    merchants = db.list_all_merchants()
    return {
        "success": True,
        "total": len(merchants),
        "merchants": [_safe_merchant(m) for m in merchants],
    }


# ─────────────────────────────────────────────────────────
# Webhook handler — Pine Labs payment event
# ─────────────────────────────────────────────────────────

def handle_pine_labs_event(event: dict) -> dict:
    """
    Called by the FastAPI webhook endpoint.
    Parses a Pine Labs payment event and stores the transaction.
    """
    event_type = event.get("event")
    if event_type not in ("payment_success", "payment_failed", "payment_refunded"):
        return {"status": "ignored", "reason": f"Unknown event type: {event_type}"}

    merchant_id = event.get("merchant_id")
    if not merchant_id:
        return {"status": "error", "reason": "merchant_id missing from event"}

    # Validate merchant exists
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return {"status": "error", "reason": f"Merchant '{merchant_id}' not found. Onboard first."}

    # Map event type to status
    status_map = {
        "payment_success": "success",
        "payment_failed": "failed",
        "payment_refunded": "refunded",
    }

    ts = event.get("timestamp", "")
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"

    txn = db.insert_transaction(
        merchant_id=merchant_id,
        amount=float(event.get("amount", 0)),
        payment_method=event.get("payment_method", "UPI"),
        status=status_map[event_type],
        timestamp=ts,
        source="webhook",
        transaction_id=event.get("transaction_id"),
    )

    return {
        "status": "ok",
        "message": "Transaction recorded.",
        "transaction_id": txn["transaction_id"],
        "merchant_id": merchant_id,
        "amount": txn["amount"],
    }



# ──────────────────────────────────────────────
# Tool M7 — scan_merchant_for_fraud
# ──────────────────────────────────────────────

def scan_merchant_for_fraud(merchant_id: str) -> dict:
    """Run the full AML/fraud scan, persist alerts, return results with risk score."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found.")

    scan_result = aml_engine.run_aml_scan(merchant_id)

    aml_db.clear_alerts_for_merchant(merchant_id)
    alert_ids = []
    for flag in scan_result["flags"]:
        alert = aml_db.save_alert(
            merchant_id=merchant_id,
            rule_id=flag["rule_id"],
            rule_name=flag["rule_name"],
            severity=flag["severity"],
            evidence=flag["evidence"],
        )
        alert_ids.append(alert["alert_id"])

    aml_db.save_scan_log(
        merchant_id=merchant_id,
        risk_score=scan_result["risk_score"],
        risk_level=scan_result["risk_level"],
        flags_raised=len(scan_result["flags"]),
        flag_ids=alert_ids,
    )

    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        **scan_result,
    }


# ──────────────────────────────────────────────
# Tool M8 — get_aml_risk_score
# ──────────────────────────────────────────────

def get_aml_risk_score(merchant_id: str) -> dict:
    """Read most recent AML scan result and active alert summary for a merchant."""
    merchant = db.get_merchant_by_id(merchant_id)
    if not merchant:
        return _err(f"Merchant '{merchant_id}' not found.")

    latest_scan = aml_db.get_latest_scan(merchant_id)
    active_alerts = aml_db.get_alerts_for_merchant(merchant_id, unresolved_only=True)

    if not latest_scan:
        return {
            "success": True,
            "merchant_id": merchant_id,
            "business_name": merchant["business_name"],
            "risk_score": None,
            "risk_level": "UNKNOWN",
            "message": "No scan has been run yet. Call scan_merchant_for_fraud first.",
            "active_alerts": [],
        }

    return {
        "success": True,
        "merchant_id": merchant_id,
        "business_name": merchant["business_name"],
        "risk_score": latest_scan["risk_score"],
        "risk_level": latest_scan["risk_level"],
        "last_scanned_at": latest_scan["scanned_at"],
        "active_alerts_count": len(active_alerts),
        "active_alerts": [
            {
                "alert_id": a["alert_id"],
                "rule_id": a["rule_id"],
                "rule_name": a["rule_name"],
                "severity": a["severity"],
                "triggered_at": a["triggered_at"],
            }
            for a in active_alerts
        ],
    }


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _err(message: str, **kwargs) -> dict:
    return {"success": False, "error": message, **kwargs}


def _safe_merchant(m: dict) -> dict:
    return {
        "merchant_id": m["merchant_id"],
        "agent_id": m["agent_id"],
        "business_name": m["business_name"],
        "business_type": m["business_type"],
        "city": m.get("city", ""),
        "state": m.get("state", ""),
        "onboarded_at": m["onboarded_at"],
        "is_active": bool(m["is_active"]),
    }
