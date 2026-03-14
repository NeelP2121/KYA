"""
KYC MCP Server — MFOS Integration Test Flow
Tests the MFOS tools including onboarding, transactions, and analytics via MCP.

Run:
    # Terminal 1 — server
    python server.py

    # Terminal 2 — tests
    python test_mfos_flow.py
"""

import httpx
import json
import threading
import queue
import time
from datetime import datetime

BASE = "http://localhost:8000"

# ─────────────────────────────────────────────────────────
# MCP Server Client
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
            raise RuntimeError("Cannot connect to SSE endpoint.")
        print(f"✅ connected to {self.session_url}\n")

    def _post(self, payload: dict):
        r = httpx.post(self.session_url, json=payload, timeout=10)
        if r.status_code not in (200, 202, 204):
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")

    def _wait(self, req_id: int, timeout: int = 15) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.response_queue.get(timeout=1)
                if msg.get("id") == req_id:
                    return msg
                if "id" in msg and msg["id"] != req_id:
                    self.response_queue.put(msg)
            except queue.Empty:
                continue
        raise RuntimeError(f"No response for id={req_id}")

    def initialize(self):
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "mfos-test", "version": "1.0"}}})
        try:
            self.response_queue.get(timeout=3)
        except queue.Empty:
            pass
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        time.sleep(0.3)
        print("✅ MCP handshake complete\n")

    def call_tool(self, name: str, args: dict, req_id: int) -> dict:
        self._post({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
        msg = self._wait(req_id)
        if "error" in msg:
            raise RuntimeError(f"MCP error [{name}]: {msg['error']}")
        content = msg["result"].get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        return json.loads(text) if text else {}

    def close(self):
        self._stop.set()


def ok(label: str):
    print(f"  ✅ {label}")

# ─────────────────────────────────────────────────────────
# Test Flow
# ─────────────────────────────────────────────────────────

def run_mfos_tests():
    client = MCPClient(BASE)
    client.connect()
    client.initialize()

    req = 1

    # Unique string to prevent conflicts in DB
    ts = int(time.time())

    # Step A: Register & KYC
    print("━" * 55)
    print("STEP 1 — Setup user through KYC to get 'agent_id'")
    
    # 1. Register User
    res = client.call_tool("register_user", {
        "full_name": "Rahul Sharma",
        "email": f"merchant.{ts}@test.com",
    }, req_id=req); req += 1
    kyc_uid = res["user"]["user_id"]
    
    # 2. Initiate KYC
    res = client.call_tool("initiate_kyc", {
        "user_id": kyc_uid,
        "documents_json": json.dumps({"AADHAAR": {"aadhaar_number": "999999999999"}})
    }, req_id=req); req += 1
    sid = res["session_id"]
    
    # 3. Verify & Generate agent ID
    res = client.call_tool("verify_and_generate_id", {
        "user_id": kyc_uid,
        "session_id": sid,
        "otp": "421596"
    }, req_id=req); req += 1
    
    agent_id = res["agent_id"]
    ok(f"KYC completed. Agent ID derived: {agent_id}")
    
    # Step B: Onboard Merchant
    print("━" * 55)
    print("STEP 2 — Onboard the User as a Merchant (Tool: onboard_merchant)")
    res = client.call_tool("onboard_merchant", {
        "kyc_user_id": kyc_uid,
        "agent_id": agent_id,
        "business_name": "Sharma SuperMart",
        "business_type": "Grocery",
        "city": "Bengaluru",
        "state": "Karnataka"
    }, req_id=req); req += 1
    
    assert res["success"]
    merchant_id = res["merchant"]["merchant_id"]
    ok(f"Business onboarded. Merchant ID: {merchant_id}")
    
    # Step C: List Merchants
    print("━" * 55)
    print("STEP 3 — List All Merchants (Tool: list_merchants)")
    res = client.call_tool("list_merchants", {}, req_id=req); req += 1
    assert res["success"]
    matched = [m for m in res["merchants"] if m["merchant_id"] == merchant_id]
    assert matched
    ok(f"Merchant is visible in the registry ({res['total']} onboarded overall)")

    # Step D: Seed Transactions using internal DB to test Analytics correctly
    from mfos.mfos_db import insert_transaction, now_iso
    insert_transaction(
        merchant_id=merchant_id, amount=1200.50, payment_method="UPI",
        status="success", timestamp=now_iso(), source="seed"
    )
    insert_transaction(
        merchant_id=merchant_id, amount=850.00, payment_method="Card",
        status="success", timestamp=now_iso(), source="seed"
    )
    insert_transaction(
        merchant_id=merchant_id, amount=3000.00, payment_method="NetBanking",
        status="failed", timestamp=now_iso(), source="seed"
    )
    ok(f"Seeded mock transactions successfully.")

    # Step E: Analytics Tools
    print("━" * 55)
    print("STEP 4 — Financial Analytics Tools")
    
    # 1. Revenue
    res = client.call_tool("get_revenue_summary", {"merchant_id": merchant_id}, req_id=req); req += 1
    assert res["success"]
    today_rev = res["today_revenue"]
    assert today_rev >= 2050.5, f"Expected at least 2050.5, got {today_rev}"
    ok(f"get_revenue_summary working: Today={today_rev} | 30Day={res['last_30_days_revenue']}")
    
    # 2. Payment Breakdown
    res = client.call_tool("get_payment_breakdown", {"merchant_id": merchant_id}, req_id=req); req += 1
    assert res["success"]
    assert len(res["breakdown"]) == 2  # UPI, Card
    ok(f"get_payment_breakdown working: Method Count={len(res['breakdown'])}")

    # 3. Predict Cashflow
    res = client.call_tool("predict_cashflow", {"merchant_id": merchant_id}, req_id=req); req += 1
    assert res["success"]
    ok(f"predict_cashflow working: Next 7 Days Predicted={res['predicted_next_7_days']}")

    # 4. Check Credit
    res = client.call_tool("check_credit_eligibility", {"merchant_id": merchant_id}, req_id=req); req += 1
    assert res["success"]
    ok(f"check_credit_eligibility working: Is Eligible={res['eligible']} | Score={res['health_score']}/100")

    # Step F: Simulating the Webhook integration for Pine Labs
    print("━" * 55)
    print("STEP 5 — Webhook testing (Simulating Pine Labs POST via httpx)")
    payload = {
        "event": "payment_success",
        "merchant_id": merchant_id,
        "amount": 550.0,
        "payment_method": "Wallet",
        "transaction_id": "TXN_WEBHOOK_001",
        "timestamp": now_iso()
    }
    r = httpx.post(f"http://localhost:8001/webhook/pine-labs", json=payload)
    assert r.status_code == 200
    res_py = r.json()
    assert res_py["status"] == "ok"
    ok(f"Webhook recorded transaction TXN_WEBHOOK_001 for amount: 550.0.")

    # Check Revenue again to see if webhook updated the total
    res = client.call_tool("get_revenue_summary", {"merchant_id": merchant_id}, req_id=req); req += 1
    assert res["today_revenue"] >= 2600.5 - 0.01  # 2050.5 + 550
    ok(f"Revenue auto-updated via Webhook -> {res['today_revenue']}")

    print("\n" + "=" * 55)
    print("🎉 ALL MFOS FINANCE TESTS PASSED END-TO-END")
    print("=" * 55)
    client.close()

if __name__ == "__main__":
    run_mfos_tests()
