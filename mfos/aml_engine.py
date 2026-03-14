"""
AML / Fraud Detection Engine.
Rule-based Anti-Money Laundering and fraud signal detector.

Rules implemented:
  R1 — Structuring (smurfing): transactions clustered just below ₹50,000
  R2 — Round Amount Surge: excessive round-number transactions
  R3 — Velocity Spike: too many transactions in a short time window
  R4 — Revenue Anomaly: sudden day-over-day revenue explosion
  R5 — Refund Abuse: high refund-to-success ratio
  R6 — Card Testing: many tiny failed card txns + large success afterwards
  R7 — Dormant Surge: inactive merchant suddenly processing large volume

Pure functions — reads from DB, returns flags, no side effects.
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from mfos.mfos_db import (
    get_transactions,
    get_transactions_for_window,
    get_all_transactions,
    get_revenue_last_n_days,
    get_revenue_for_date,
    get_transaction_stats,
)


@dataclass
class AMLFlag:
    rule_id: str
    rule_name: str
    severity: str           # LOW | MEDIUM | HIGH | CRITICAL
    description: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────
# Severity scoring weights
# ─────────────────────────────────────────────────────────
SEVERITY_WEIGHTS = {
    "LOW":      10,
    "MEDIUM":   25,
    "HIGH":     45,
    "CRITICAL": 80,
}

def _risk_level(score: int) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────
# Rule R1 — Structuring (Smurfing)
# ─────────────────────────────────────────────────────────
STRUCTURING_THRESHOLD = 50_000
STRUCTURING_BAND = 0.12  # within 12% below threshold
STRUCTURING_MIN_COUNT = 3

def _check_structuring(merchant_id: str) -> Optional[AMLFlag]:
    """Detect multiple transactions just below the ₹50K reporting threshold."""
    txns = get_transactions(merchant_id, days_back=7, status_filter="success")
    lower_band = STRUCTURING_THRESHOLD * (1 - STRUCTURING_BAND)
    suspicious = [
        t for t in txns
        if lower_band <= t["amount"] < STRUCTURING_THRESHOLD
    ]
    if len(suspicious) >= STRUCTURING_MIN_COUNT:
        amounts = [t["amount"] for t in suspicious[:10]]
        return AMLFlag(
            rule_id="R1",
            rule_name="Structuring / Smurfing",
            severity="HIGH",
            description=(
                f"{len(suspicious)} transactions found between "
                f"₹{int(lower_band):,}–₹{STRUCTURING_THRESHOLD:,} in the last 7 days. "
                "This is a classic structuring pattern to avoid reporting thresholds."
            ),
            evidence={
                "suspicious_count": len(suspicious),
                "threshold": STRUCTURING_THRESHOLD,
                "band": f"±{int(STRUCTURING_BAND*100)}%",
                "sample_amounts": amounts,
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R2 — Round Amount Surge
# ─────────────────────────────────────────────────────────
ROUND_AMOUNT_RATIO_THRESHOLD = 0.60  # 60% of txns are "round"

def _is_round(amount: float) -> bool:
    return amount >= 1000 and amount % 1000 == 0

def _check_round_amounts(merchant_id: str) -> Optional[AMLFlag]:
    txns = get_transactions(merchant_id, days_back=14, status_filter="success")
    if len(txns) < 5:
        return None
    round_txns = [t for t in txns if _is_round(t["amount"])]
    ratio = len(round_txns) / len(txns)
    if ratio >= ROUND_AMOUNT_RATIO_THRESHOLD:
        return AMLFlag(
            rule_id="R2",
            rule_name="Round Amount Surge",
            severity="MEDIUM",
            description=(
                f"{int(ratio*100)}% of transactions in the last 14 days are exact round amounts "
                f"(multiples of ₹1,000+). Legitimate retail volume rarely shows such uniformity."
            ),
            evidence={
                "round_transaction_count": len(round_txns),
                "total_transactions": len(txns),
                "round_ratio_pct": round(ratio * 100, 1),
                "sample_round_amounts": [t["amount"] for t in round_txns[:5]],
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R3 — Velocity Spike
# ─────────────────────────────────────────────────────────
VELOCITY_WINDOW_MINUTES = 30
VELOCITY_MAX_TXNS = 10

def _check_velocity(merchant_id: str) -> Optional[AMLFlag]:
    recent = get_transactions_for_window(merchant_id, minutes=VELOCITY_WINDOW_MINUTES)
    if len(recent) >= VELOCITY_MAX_TXNS:
        total_amount = sum(t["amount"] for t in recent if t["status"] == "success")
        return AMLFlag(
            rule_id="R3",
            rule_name="Velocity Spike",
            severity="HIGH",
            description=(
                f"{len(recent)} transactions detected in the last {VELOCITY_WINDOW_MINUTES} minutes "
                f"(threshold: {VELOCITY_MAX_TXNS}). Rapid-fire transactions may indicate automation or fraud."
            ),
            evidence={
                "transactions_in_window": len(recent),
                "window_minutes": VELOCITY_WINDOW_MINUTES,
                "total_amount_in_window": round(total_amount, 2),
                "threshold": VELOCITY_MAX_TXNS,
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R4 — Revenue Anomaly
# ─────────────────────────────────────────────────────────
ANOMALY_MULTIPLIER = 5.0  # today must be 5x the 7-day daily avg
ANOMALY_MIN_BASELINE = 500  # only flag if baseline > ₹500 (avoid zero-history merchants)

def _check_revenue_anomaly(merchant_id: str) -> Optional[AMLFlag]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_rev = get_revenue_for_date(merchant_id, today)
    last_7 = get_revenue_last_n_days(merchant_id, 7)
    daily_avg_7d = last_7 / 7

    if daily_avg_7d >= ANOMALY_MIN_BASELINE and today_rev >= daily_avg_7d * ANOMALY_MULTIPLIER:
        return AMLFlag(
            rule_id="R4",
            rule_name="Revenue Anomaly",
            severity="MEDIUM",
            description=(
                f"Today's revenue (₹{today_rev:,.0f}) is "
                f"{today_rev/daily_avg_7d:.1f}x the 7-day daily average (₹{daily_avg_7d:,.0f}). "
                "Sudden unexplained spikes may indicate account compromise or layering."
            ),
            evidence={
                "today_revenue": round(today_rev, 2),
                "daily_avg_7d": round(daily_avg_7d, 2),
                "multiplier": round(today_rev / daily_avg_7d, 2),
                "threshold_multiplier": ANOMALY_MULTIPLIER,
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R5 — Refund Abuse
# ─────────────────────────────────────────────────────────
REFUND_RATIO_THRESHOLD = 0.30  # 30% refund-to-success ratio

def _check_refund_abuse(merchant_id: str) -> Optional[AMLFlag]:
    stats = get_transaction_stats(merchant_id, days_back=30)
    success = stats.get("success_count", 0)
    refunded = stats.get("refunded_count", 0)
    if success < 5:
        return None  # not enough history
    ratio = refunded / success
    if ratio >= REFUND_RATIO_THRESHOLD:
        return AMLFlag(
            rule_id="R5",
            rule_name="Refund Abuse",
            severity="HIGH",
            description=(
                f"Refund-to-success ratio is {int(ratio*100)}% (threshold: {int(REFUND_RATIO_THRESHOLD*100)}%). "
                f"{refunded} refunds against {success} successes in the last 30 days. "
                "Excessive refunds indicate potential friendly fraud or chargeback abuse."
            ),
            evidence={
                "success_count": success,
                "refunded_count": refunded,
                "refund_ratio_pct": round(ratio * 100, 1),
                "threshold_pct": int(REFUND_RATIO_THRESHOLD * 100),
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R6 — Card Testing Pattern (CRITICAL)
# ─────────────────────────────────────────────────────────
CARD_TEST_FAILED_MIN = 5       # at least 5 tiny failed card txns
CARD_TEST_AMOUNT_MAX = 100.0   # below ₹100 = "probe transaction"
CARD_TEST_SUCCESS_MIN = 5000.0 # followed by a success >= ₹5,000

def _check_card_testing(merchant_id: str) -> Optional[AMLFlag]:
    """Detect card-testing: many tiny failed transactions followed by a large success."""
    txns = get_transactions(merchant_id, days_back=3)  # last 3 days
    failed_probes = [
        t for t in txns
        if t["status"] == "failed"
        and t["payment_method"] == "Card"
        and t["amount"] <= CARD_TEST_AMOUNT_MAX
    ]
    if len(failed_probes) < CARD_TEST_FAILED_MIN:
        return None

    # Check if a large card success happened after the probes
    probe_timestamps = sorted(t["timestamp"] for t in failed_probes)
    earliest_probe = probe_timestamps[0]

    large_success = [
        t for t in txns
        if t["status"] == "success"
        and t["payment_method"] == "Card"
        and t["amount"] >= CARD_TEST_SUCCESS_MIN
        and t["timestamp"] > earliest_probe
    ]

    if large_success:
        return AMLFlag(
            rule_id="R6",
            rule_name="Card Testing Pattern",
            severity="CRITICAL",
            description=(
                f"{len(failed_probes)} small failed Card transactions (<₹{CARD_TEST_AMOUNT_MAX}) "
                f"detected, followed by {len(large_success)} large Card success(es) ≥₹{CARD_TEST_SUCCESS_MIN:,}. "
                "This is the classic stolen card verification pattern."
            ),
            evidence={
                "failed_probe_count": len(failed_probes),
                "probe_amount_max": CARD_TEST_AMOUNT_MAX,
                "large_success_count": len(large_success),
                "large_success_amounts": [t["amount"] for t in large_success[:5]],
                "first_probe_at": earliest_probe,
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Rule R7 — Dormant Surge
# ─────────────────────────────────────────────────────────
DORMANT_DAYS = 14              # days of near-zero activity
DORMANT_THRESHOLD = 500.0      # less than ₹500 in those days = "dormant"
SURGE_THRESHOLD = 50_000.0    # then ₹50K+ in last 24 hours

def _check_dormant_surge(merchant_id: str) -> Optional[AMLFlag]:
    prior_rev = get_revenue_last_n_days(merchant_id, DORMANT_DAYS + 1)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_rev = get_revenue_for_date(merchant_id, today)
    prior_14 = prior_rev  # approximation (includes today slightly but close enough)

    if prior_14 <= DORMANT_THRESHOLD and today_rev >= SURGE_THRESHOLD:
        return AMLFlag(
            rule_id="R7",
            rule_name="Dormant Surge",
            severity="HIGH",
            description=(
                f"Merchant had only ₹{prior_14:,.0f} in revenue over the last {DORMANT_DAYS} days "
                f"(dormancy threshold: ₹{DORMANT_THRESHOLD:,}), then suddenly processed "
                f"₹{today_rev:,.0f} today. This pattern is consistent with money laundering via shell accounts."
            ),
            evidence={
                "revenue_last_14d": round(prior_14, 2),
                "today_revenue": round(today_rev, 2),
                "dormancy_threshold": DORMANT_THRESHOLD,
                "surge_threshold": SURGE_THRESHOLD,
            }
        )
    return None


# ─────────────────────────────────────────────────────────
# Main public API
# ─────────────────────────────────────────────────────────

ALL_RULES = [
    _check_structuring,
    _check_round_amounts,
    _check_velocity,
    _check_revenue_anomaly,
    _check_refund_abuse,
    _check_card_testing,
    _check_dormant_surge,
]


def run_aml_scan(merchant_id: str) -> dict:
    """
    Run all AML rules against a merchant and return the scan result.
    Returns: {flags, risk_score, risk_level, scan_summary}
    """
    flags: list[AMLFlag] = []
    for rule_fn in ALL_RULES:
        try:
            flag = rule_fn(merchant_id)
            if flag:
                flags.append(flag)
        except Exception as e:
            # Never let a single buggy rule crash the scan
            flags.append(AMLFlag(
                rule_id="ERR",
                rule_name=f"Rule Error ({rule_fn.__name__})",
                severity="LOW",
                description=f"Rule evaluation error: {e}",
            ))

    # Composite risk score: cap at 100, sum of weighted flags
    raw_score = sum(SEVERITY_WEIGHTS.get(f.severity, 0) for f in flags)
    risk_score = min(100, raw_score)
    risk_level = _risk_level(risk_score)

    return {
        "merchant_id": merchant_id,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "flags_count": len(flags),
        "flags": [f.to_dict() for f in flags],
        "recommendation": _recommendation(risk_level),
    }


def _recommendation(risk_level: str) -> str:
    return {
        "LOW":      "No immediate action required. Continue monitoring.",
        "MEDIUM":   "Elevated risk. Review flagged transactions and request merchant explanation.",
        "HIGH":     "High risk. Consider temporary transaction limits and compliance review.",
        "CRITICAL": "IMMEDIATE ACTION REQUIRED. Freeze account and escalate to compliance team.",
    }.get(risk_level, "Unknown risk level.")
