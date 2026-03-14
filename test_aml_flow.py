"""
KYC MCP Server — AML & Fraud Detection Test Flow
Demonstrates 4 fraud patterns being detected by the rule engine.

Run:
    # Terminal 1: python server.py
    # Terminal 2: python test_aml_flow.py
"""

import json
import threading
import queue
import time
import httpx
from datetime import datetime, timezone, timedelta

BASE = "http://localhost:8000"

# ─────────────────────────────────────────────────────────
# MCP Client (reused from other test files)
# ─────────────────────────────────────────────────────────

class MCPClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_url = None
        self.response_queue = queue.Queue()
        self._stop = threading.Event()

    def connect(self):
        ready = threading.Event()
        def sse_listener():
            with httpx.Client(timeout=None) as client:
                with client.stream("GET", f"{self.base_url}/sse") as resp:
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            break
                        if line.startswith("data:"):
                            raw = line[len("data:"):].strip()
                            if not ready.is_set() and "/messages" in raw:
                                url = raw if raw.startswith("http") else self.base_url + raw
                                self.session_url = url
                                ready.set()
                                continue
                            if ready.is_set() and raw:
                                try:
                                    msg = json.loads(raw)
                                    self.response_queue.put(msg)
                                except json.JSONDecodeError:
                                    pass
        t = threading.Thread(target=sse_listener, daemon=True)
        t.start()
        if not ready.wait(timeout=10):
            raise RuntimeError("Cannot connect. Is the server running?")
        print(f"✅ Connected to MCP server\n")

    def _post(self, payload):
        r = httpx.post(self.session_url, json=payload, timeout=10)
        if r.status_code not in (200, 202, 204):
            raise RuntimeError(f"HTTP {r.status_code}")

    def _wait(self, req_id, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.response_queue.get(timeout=1)
                if msg.get("id") == req_id:
                    return msg
                self.response_queue.put(msg)
            except queue.Empty:
                continue
        raise RuntimeError(f"No response for id={req_id}")

    def initialize(self):
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "aml-test", "version": "1.0"}}})
        try:
            self.response_queue.get(timeout=3)
        except queue.Empty:
            pass
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        time.sleep(0.3)
        print("✅ MCP handshake complete\n")

    def call(self, name, args, req_id) -> dict:
        self._post({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
        msg = self._wait(req_id)
        if "error" in msg:
            raise RuntimeError(f"MCP error [{name}]: {msg['error']}")
        result = msg.get("result", {})
        content = result.get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        if text:
            return json.loads(text)
        # Fallback: return the raw result dict (some tools may not have text content)
        return result


    def close(self):
        self._stop.set()


def section(title):
    print(f"\n{'━'*57}")
    print(f"  {title}")
    print(f"{'━'*57}")

def ok(msg):
    print(f"  ✅ {msg}")

def flag(msg):
    print(f"  🚨 {msg}")


# ─────────────────────────────────────────────────────────
# Helpers to seed transactions directly into DB
# ─────────────────────────────────────────────────────────

def seed_txn(merchant_id, amount, status="success", method="UPI", minutes_ago=0):
    from mfos.mfos_db import insert_transaction
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    insert_transaction(merchant_id=merchant_id, amount=amount,
                       payment_method=method, status=status,
                       timestamp=ts, source="aml_test_seed")


# ─────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────

def run():
    client = MCPClient(BASE)
    client.connect()
    client.initialize()
    req = 1
    ts = int(time.time())

    # ── Setup: KYC → Agent ID → Merchant ──────────────────
    section("SETUP — Register, KYC, and Onboard Merchant")
    res = client.call("register_user", {"full_name": "Rahul Sharma",
                                         "email": f"aml.test.{ts}@test.com"}, req); req += 1
    uid = res["user"]["user_id"]

    res = client.call("initiate_kyc", {"user_id": uid,
                       "documents_json": json.dumps({"AADHAAR": {"aadhaar_number": "999999999999"}})}, req); req += 1
    sid = res["session_id"]

    res = client.call("verify_and_generate_id", {"user_id": uid, "session_id": sid, "otp": "421596"}, req); req += 1
    agent_id = res["agent_id"]

    res = client.call("onboard_merchant", {
        "kyc_user_id": uid, "agent_id": agent_id,
        "business_name": "Rahu Sharma Enterprises",
        "business_type": "Cash-Intensive", "city": "Mumbai", "state": "Maharashtra"
    }, req); req += 1
    merchant_id = res["merchant"]["merchant_id"]
    ok(f"Merchant ready: {merchant_id}")

    # ── Phase 1: Clean scan (no fraud) ────────────────────
    section("PHASE 1 — Clean merchant scan (expect LOW risk)")
    seed_txn(merchant_id, 1500, "success", "UPI", 5)
    seed_txn(merchant_id, 2200, "success", "Card", 10)
    seed_txn(merchant_id, 800, "success", "UPI", 20)

    res = client.call("scan_merchant_for_fraud", {"merchant_id": merchant_id}, req); req += 1
    assert res["success"]
    assert res["risk_level"] in ("LOW", "MEDIUM"), f"Expected low risk, got {res['risk_level']}"
    ok(f"Risk Level: {res['risk_level']} | Score: {res['risk_score']}/100 | Flags: {res['flags_count']}")

    # ── Phase 2: Seed Structuring pattern ─────────────────
    section("PHASE 2 — Structuring (Smurfing) → expect R1 flag [HIGH]")
    # 8 transactions just below ₹50,000
    for i in range(8):
        seed_txn(merchant_id, 47_500 + (i * 200), "success", "UPI", 60 + i * 5)
    ok("Seeded 8 transactions in ₹47,500–₹49,100 band")

    res = client.call("scan_merchant_for_fraud", {"merchant_id": merchant_id}, req); req += 1
    flag_ids = [f["rule_id"] for f in res["flags"]]
    assert "R1" in flag_ids, f"Expected R1 (Structuring) flag! Got: {flag_ids}"
    r1 = next(f for f in res["flags"] if f["rule_id"] == "R1")
    flag(f"R1 Structuring detected! Evidence: {r1['evidence']['suspicious_count']} transactions in band")
    ok(f"Risk Level: {res['risk_level']} | Score: {res['risk_score']}/100")

    # ── Phase 3: Card Testing ──────────────────────────────
    section("PHASE 3 — Card Testing Pattern → expect R6 flag [CRITICAL]")
    # 15 tiny failed card probes
    for i in range(15):
        seed_txn(merchant_id, 10 + i, "failed", "Card", 20 + i)
    # Then a large card success
    seed_txn(merchant_id, 75_000, "success", "Card", 5)
    ok("Seeded 15 failed card probes (₹10–₹24) + 1 success of ₹75,000")

    res = client.call("scan_merchant_for_fraud", {"merchant_id": merchant_id}, req); req += 1
    flag_ids = [f["rule_id"] for f in res["flags"]]
    assert "R6" in flag_ids, f"Expected R6 (Card Testing) flag! Got: {flag_ids}"
    r6 = next(f for f in res["flags"] if f["rule_id"] == "R6")
    flag(f"R6 CRITICAL Card Testing detected! {r6['evidence']['failed_probe_count']} probes → ₹{r6['evidence']['large_success_amounts'][0]:,} success")
    ok(f"Risk Level: {res['risk_level']} | Score: {res['risk_score']}/100")

    # ── Phase 4: Refund Abuse ──────────────────────────────
    section("PHASE 4 — Refund Abuse → expect R5 flag [HIGH]")
    # 10 successes, 5 refunds = 50% refund ratio
    for i in range(10):
        seed_txn(merchant_id, 3000 + i * 100, "success", "UPI", 200 + i * 10)
    for i in range(5):
        seed_txn(merchant_id, 2500, "refunded", "UPI", 210 + i * 10)
    ok("Seeded 10 successes + 5 refunds (50% refund ratio)")

    res = client.call("scan_merchant_for_fraud", {"merchant_id": merchant_id}, req); req += 1
    flag_ids = [f["rule_id"] for f in res["flags"]]
    if "R5" in flag_ids:
        r5 = next(f for f in res["flags"] if f["rule_id"] == "R5")
        flag(f"R5 Refund Abuse detected! Refund ratio: {r5['evidence']['refund_ratio_pct']}%")
    ok(f"Risk Level: {res['risk_level']} | Score: {res['risk_score']}/100 | Flags: {res['flags_count']}")

    # ── Phase 5: get_aml_risk_score tool ──────────────────
    section("PHASE 5 — get_aml_risk_score (read-only summary)")
    res = client.call("get_aml_risk_score", {"merchant_id": merchant_id}, req); req += 1
    assert res["success"]
    assert res["risk_level"] in ("HIGH", "CRITICAL", "MEDIUM"), f"Unexpected level: {res['risk_level']}"
    ok(f"Persistent risk score: {res['risk_score']}/100 | Level: {res['risk_level']}")
    ok(f"Active alerts in DB: {res['active_alerts_count']}")
    for a in res["active_alerts"]:
        flag(f"  [{a['severity']}] {a['rule_name']} (rule {a['rule_id']})")

    print(f"\n{'='*57}")
    print(f"  🎉 ALL AML DETECTION TESTS PASSED")
    print(f"{'='*57}")
    print(f"\n  Merchant: {merchant_id}")
    print(f"  Final Risk Score : {res['risk_score']}/100")
    print(f"  Final Risk Level : {res['risk_level']}")
    print(f"  Active Alerts    : {res['active_alerts_count']}")

    client.close()

if __name__ == "__main__":
    run()
