"""
MFOS Analytics Engine.
Computes financial intelligence from transaction data.
Pure functions — no side effects, no DB writes.
"""

from datetime import datetime, timezone, timedelta
from mfos.mfos_db import (
    get_revenue_last_n_days,
    get_revenue_for_date,
    get_payment_method_breakdown,
    get_daily_revenue_series,
    get_transaction_stats,
)


def compute_revenue_summary(merchant_id: str) -> dict:
    """
    Today's revenue, yesterday's, WoW growth, 7-day average.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    today_rev = get_revenue_for_date(merchant_id, today)
    yesterday_rev = get_revenue_for_date(merchant_id, yesterday)
    last_7 = get_revenue_last_n_days(merchant_id, 7)
    last_30 = get_revenue_last_n_days(merchant_id, 30)

    avg_daily_7d = round(last_7 / 7, 2)
    avg_daily_30d = round(last_30 / 30, 2)

    wow_growth = 0.0
    if yesterday_rev > 0:
        wow_growth = round(((today_rev - yesterday_rev) / yesterday_rev) * 100, 1)

    daily_series = get_daily_revenue_series(merchant_id, 7)
    stats = get_transaction_stats(merchant_id, 30)

    success_rate = 0.0
    total = stats.get("total_count", 0)
    if total > 0:
        success_rate = round(stats["success_count"] / total * 100, 1)

    return {
        "today_revenue": round(today_rev, 2),
        "yesterday_revenue": round(yesterday_rev, 2),
        "wow_growth_pct": wow_growth,
        "last_7_days_revenue": round(last_7, 2),
        "last_30_days_revenue": round(last_30, 2),
        "avg_daily_revenue_7d": avg_daily_7d,
        "avg_daily_revenue_30d": avg_daily_30d,
        "daily_series_last_7": daily_series,
        "total_transactions_30d": stats.get("total_count", 0),
        "success_rate_pct": success_rate,
        "avg_transaction_value": round(stats.get("avg_txn_value", 0), 2),
    }


def compute_payment_breakdown(merchant_id: str, days_back: int = 30) -> dict:
    """
    Payment method share by volume and transaction count.
    """
    rows = get_payment_method_breakdown(merchant_id, days_back)
    total_amount = sum(r["total"] for r in rows)
    total_count = sum(r["count"] for r in rows)

    breakdown = {}
    for row in rows:
        pct_volume = round(row["total"] / total_amount * 100, 1) if total_amount > 0 else 0.0
        pct_count = round(row["count"] / total_count * 100, 1) if total_count > 0 else 0.0
        breakdown[row["payment_method"]] = {
            "volume": round(row["total"], 2),
            "volume_pct": pct_volume,
            "transaction_count": row["count"],
            "count_pct": pct_count,
        }

    dominant = max(breakdown.items(), key=lambda x: x[1]["volume_pct"])[0] if breakdown else None

    return {
        "period_days": days_back,
        "total_revenue": round(total_amount, 2),
        "total_transactions": total_count,
        "breakdown": breakdown,
        "dominant_method": dominant,
        "insight": _payment_insight(breakdown, dominant),
    }


def compute_cashflow_prediction(merchant_id: str) -> dict:
    """
    Simple rolling-average prediction for next 7 days.
    Uses last 14 days vs last 7 days trend to extrapolate.
    """
    last_7 = get_revenue_last_n_days(merchant_id, 7)
    last_14 = get_revenue_last_n_days(merchant_id, 14)
    prev_7 = last_14 - last_7   # revenue from days 8–14

    # Growth rate from prior week to current week
    growth_rate = 0.08  # default 8%
    if prev_7 > 0:
        growth_rate = (last_7 - prev_7) / prev_7
        growth_rate = max(-0.5, min(growth_rate, 0.5))  # cap at ±50%

    predicted_next_7 = round(last_7 * (1 + growth_rate), 2)
    predicted_daily = round(predicted_next_7 / 7, 2)

    # Risk assessment
    daily_series = get_daily_revenue_series(merchant_id, 14)
    revenue_values = [d["revenue"] for d in daily_series if d["revenue"] > 0]
    volatility = "LOW"
    if len(revenue_values) >= 3:
        avg = sum(revenue_values) / len(revenue_values)
        std = (sum((x - avg) ** 2 for x in revenue_values) / len(revenue_values)) ** 0.5
        cv = std / avg if avg > 0 else 0
        volatility = "HIGH" if cv > 0.4 else ("MEDIUM" if cv > 0.2 else "LOW")

    risk_flag = "HIGH" if predicted_next_7 < 10000 else ("MEDIUM" if predicted_next_7 < 30000 else "LOW")

    return {
        "last_7_days_actual": round(last_7, 2),
        "previous_7_days": round(prev_7, 2),
        "week_on_week_growth_pct": round(growth_rate * 100, 1),
        "predicted_next_7_days": predicted_next_7,
        "predicted_daily_avg": predicted_daily,
        "revenue_volatility": volatility,
        "risk_flag": risk_flag,
        "confidence": "MEDIUM" if len(revenue_values) >= 5 else "LOW",
        "note": "Prediction based on rolling 14-day trend. Accuracy improves with more history.",
    }


def compute_credit_eligibility(merchant_id: str) -> dict:
    """
    Working capital eligibility based on revenue history and health score.
    Score: 0–100. Limit: 60% of 30-day revenue.
    """
    last_30 = get_revenue_last_n_days(merchant_id, 30)
    last_7 = get_revenue_last_n_days(merchant_id, 7)
    stats = get_transaction_stats(merchant_id, 30)

    total = stats.get("total_count", 0)
    success_count = stats.get("success_count", 0)
    success_rate = (success_count / total * 100) if total > 0 else 0

    # Health score components (out of 100)
    revenue_score = min(40, int(last_30 / 2500))        # up to 40 pts — ₹1L/month = 40
    consistency_score = min(30, int(success_rate * 0.3)) # up to 30 pts
    frequency_score = min(20, int(total / 3))            # up to 20 pts — 60 txns = 20
    trend_score = 10 if last_7 > (last_30 / 4) else 5   # 10 pts if recent week > avg week

    health_score = revenue_score + consistency_score + frequency_score + trend_score

    # Credit limit: 60% of 30-day revenue, tiered by score
    base_limit = last_30 * 0.6
    if health_score >= 80:
        credit_limit = base_limit * 1.2
        tier = "PREMIUM"
        repayment_days = 45
    elif health_score >= 60:
        credit_limit = base_limit
        tier = "STANDARD"
        repayment_days = 30
    elif health_score >= 40:
        credit_limit = base_limit * 0.7
        tier = "BASIC"
        repayment_days = 21
    else:
        credit_limit = 0
        tier = "INELIGIBLE"
        repayment_days = 0

    eligible = credit_limit > 0

    return {
        "eligible": eligible,
        "health_score": health_score,
        "score_breakdown": {
            "revenue_score": revenue_score,
            "consistency_score": consistency_score,
            "frequency_score": frequency_score,
            "trend_score": trend_score,
        },
        "tier": tier,
        "recommended_credit_limit": round(credit_limit, 2),
        "repayment_term_days": repayment_days,
        "monthly_revenue": round(last_30, 2),
        "success_rate_pct": round(success_rate, 1),
        "total_transactions": total,
        "reason": _credit_reason(eligible, health_score, last_30),
    }


# ──────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────

def _payment_insight(breakdown: dict, dominant: str) -> str:
    if not dominant:
        return "No transaction data available."
    pct = breakdown[dominant]["volume_pct"]
    insight = f"{dominant} dominates at {pct}% of revenue."
    if dominant == "UPI" and pct > 60:
        insight += " High UPI reliance — consider incentivising Card payments for higher-value orders."
    elif dominant == "Card" and pct > 60:
        insight += " Strong card usage — good indicator of higher average order values."
    elif dominant == "Wallet" and pct > 40:
        insight += " Significant wallet usage — check for loyalty program opportunities."
    return insight


def _credit_reason(eligible: bool, score: int, monthly_rev: float) -> str:
    if not eligible:
        if monthly_rev < 5000:
            return "Insufficient transaction history. Process more payments to qualify."
        return f"Health score ({score}/100) below minimum threshold (40). Improve payment consistency."
    if score >= 80:
        return f"Excellent payment history (score {score}/100). You qualify for premium working capital."
    if score >= 60:
        return f"Good payment history (score {score}/100). Standard working capital available."
    return f"Adequate history (score {score}/100). Basic working capital available with shorter terms."
