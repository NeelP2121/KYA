"""
Dashboard REST API
==================
FastAPI server on port 8001 that exposes REST endpoints for both
the Merchant Dashboard and the Pine Labs operator dashboard.

Backed by: mfos_service, aml_engine, aml_db, mfos_db, kyc_db
Run: uvicorn dashboard_api:app --port 8001 --reload
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from mfos.mfos_db import init_mfos_db, get_transactions, now_iso, insert_transaction, get_merchant_by_id as _mfos_get_merchant
from mfos.aml_db import init_aml_db, get_alerts_for_merchant, get_latest_scan
from mfos import mfos_service, aml_engine, aml_db
from db.database import init_db, list_all_users

app = FastAPI(title="KYA Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Init DBs on startup
init_db()
init_mfos_db()
init_aml_db()


# ─────────────────────────────────────────────────────────
# Serve static dashboard files
# ─────────────────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).parent / "dashboard"

@app.get("/")
def root():
    return {"message": "KYA Dashboard API", "merchant_ui": "/merchant", "pinelabs_ui": "/pinelabs"}

@app.get("/merchant", include_in_schema=False)
def merchant_ui():
    return FileResponse(DASHBOARD_DIR / "merchant.html")

@app.get("/pinelabs", include_in_schema=False)
def pinelabs_ui():
    return FileResponse(DASHBOARD_DIR / "pinelabs.html")


# ─────────────────────────────────────────────────────────
# Merchant endpoints
# ─────────────────────────────────────────────────────────

@app.get("/api/merchants")
def list_merchants():
    result = mfos_service.list_merchants()
    # Enrich with latest AML scan
    merchants = result.get("merchants", [])
    enriched = []
    for m in merchants:
        scan = get_latest_scan(m["merchant_id"])
        m["risk_score"] = scan["risk_score"] if scan else None
        m["risk_level"] = scan["risk_level"] if scan else "UNKNOWN"
        enriched.append(m)
    return {"success": True, "total": len(enriched), "merchants": enriched}


@app.get("/api/merchants/{merchant_id}/summary")
def revenue_summary(merchant_id: str):
    result = mfos_service.get_revenue_summary(merchant_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/merchants/{merchant_id}/payment_breakdown")
def payment_breakdown(merchant_id: str, days: int = 30):
    result = mfos_service.get_payment_breakdown(merchant_id, days_back=days)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/merchants/{merchant_id}/cashflow")
def cashflow(merchant_id: str):
    result = mfos_service.predict_cashflow(merchant_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/merchants/{merchant_id}/credit")
def credit(merchant_id: str):
    result = mfos_service.check_credit_eligibility(merchant_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/merchants/{merchant_id}/aml")
def aml_score(merchant_id: str):
    result = mfos_service.get_aml_risk_score(merchant_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/merchants/{merchant_id}/transactions")
def transactions(merchant_id: str, days: int = 7):
    from mfos.mfos_db import get_daily_revenue_series, get_transaction_stats
    series = get_daily_revenue_series(merchant_id, days_back=days)
    stats = get_transaction_stats(merchant_id, days_back=days)
    return {"success": True, "daily_series": series, "stats": stats}


# ─────────────────────────────────────────────────────────
# AML scan (triggers live scan)
# ─────────────────────────────────────────────────────────

@app.post("/api/merchants/{merchant_id}/scan")
def run_scan(merchant_id: str):
    result = mfos_service.scan_merchant_for_fraud(merchant_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("error"))
    return result


# ─────────────────────────────────────────────────────────
# Pine Labs global views
# ─────────────────────────────────────────────────────────

@app.get("/api/aml/alerts")
def all_alerts():
    """All unresolved alerts across all merchants."""
    from mfos.mfos_db import list_all_merchants
    merchants = list_all_merchants()
    all_flags = []
    for m in merchants:
        alerts = get_alerts_for_merchant(m["merchant_id"], unresolved_only=True)
        for a in alerts:
            a["business_name"] = m["business_name"]
            all_flags.append(a)
    all_flags.sort(key=lambda x: x["triggered_at"], reverse=True)
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    all_flags.sort(key=lambda x: severity_order.get(x["severity"], 99))
    return {"success": True, "total": len(all_flags), "alerts": all_flags}


@app.get("/api/kyc/stats")
def kyc_stats():
    users = list_all_users()
    counts = {"PENDING": 0, "INITIATED": 0, "VERIFIED": 0, "FAILED": 0, "BLOCKED": 0}
    for u in users:
        status = u.get("kyc_status", "PENDING")
        counts[status] = counts.get(status, 0) + 1
    return {
        "success": True,
        "total_users": len(users),
        "by_status": counts,
    }


# ─────────────────────────────────────────────────────────
# Demo data seeder
# ─────────────────────────────────────────────────────────

@app.post("/api/seed_demo")
def seed_demo():
    """
    Seeds realistic-looking demo transaction data for all existing merchants.
    Adds normal transactions + suspicious patterns for demo display.
    """
    from mfos.mfos_db import list_all_merchants
    from datetime import datetime, timezone, timedelta
    import random

    merchants = list_all_merchants()
    if not merchants:
        return {"success": False, "message": "No merchants found. Please onboard a merchant first."}

    seeded = 0
    for merchant in merchants:
        mid = merchant["merchant_id"]
        methods = ["UPI", "Card", "Wallet", "NetBanking"]

        # Seed 30 days of normal activity (3-8 txns/day)
        for day in range(30, 0, -1):
            for _ in range(random.randint(2, 6)):
                amount = round(random.uniform(500, 12000), 2)
                ts = (datetime.now(timezone.utc) - timedelta(days=day, hours=random.randint(0, 23))).isoformat()
                insert_transaction(mid, amount, random.choice(methods), "success", ts, source="demo_seed")

        # Seed a few refunds
        for _ in range(random.randint(2, 4)):
            ts = (datetime.now(timezone.utc) - timedelta(days=random.randint(1, 15))).isoformat()
            insert_transaction(mid, round(random.uniform(1000, 5000), 2), "UPI", "refunded", ts, source="demo_seed")

        # Seed today's transactions
        for _ in range(random.randint(4, 8)):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 480))).isoformat()
            insert_transaction(mid, round(random.uniform(800, 15000), 2), random.choice(methods), "success", ts, source="demo_seed")

        # For first merchant, seed suspicious structuring pattern
        if merchant == merchants[0]:
            for i in range(5):
                ts = (datetime.now(timezone.utc) - timedelta(days=2, hours=i)).isoformat()
                insert_transaction(mid, 47000 + i * 400, "UPI", "success", ts, source="demo_seed_suspicious")

        # Run fresh AML scan
        mfos_service.scan_merchant_for_fraud(mid)
        seeded += 1

    return {
        "success": True,
        "message": f"Seeded demo data for {seeded} merchant(s) and ran AML scans.",
        "merchants_seeded": [m["merchant_id"] for m in merchants],
    }
