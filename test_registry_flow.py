"""
KYC MCP Server — Registry Integration Test Flow
Tests the agent_id generation, lookup, and idempotency via MCP tools.

Run:
    # Terminal 1 — server
    python server.py

    # Terminal 2 — tests
    python test_registry_flow.py

Covers:
  - agent_id is NOT issued before KYC is complete
  - agent_id IS issued immediately upon verify_and_generate_id
  - agent_id is idempotent (same user always gets same ID)
  - get_registered_agent_id works for VERIFIED users
  - get_registered_agent_id blocked for non-VERIFIED users
  - agent_id persists across re-verification sessions
  - fetch_verified_profile includes agent_id
"""

import httpx
import json
import threading
import queue
import time

BASE = "http://localhost:8000"


# ─────────────────────────────────────────────────────────
# MCP Client (same reusable client)
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
            raise RuntimeError("Cannot connect. Is the server running? (python server.py)")
        print(f"✅ SSE session: {self.session_url}\n")

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
                               "clientInfo": {"name": "registry-test", "version": "1.0"}}})
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
        content = msg["result"]["content"]
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        return json.loads(text)

    def close(self):
        self._stop.set()


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def ok(label: str):
    print(f"  ✅ {label}")

def fail(label: str, detail: str = ""):
    print(f"  ❌ FAIL: {label}")
    if detail:
        print(f"     {detail}")
    raise AssertionError(label)


# ─────────────────────────────────────────────────────────
# Registry test suite
# ─────────────────────────────────────────────────────────

def run_registry_tests():
    client = MCPClient(BASE)
    print(f"Connecting to {BASE}...\n")
    client.connect()
    client.initialize()

    req = 1

    # Use a unique email per run to avoid DB conflicts
    ts = int(time.time())
    email_a = f"priya.registry.{ts}@test.com"
    email_b = f"amit.registry.{ts}@test.com"

    # ════════════════════════════════════════════════════
    print("━" * 55)
    print("BLOCK 1 — agent_id requires VERIFIED KYC status")
    print("━" * 55)

    # Register user
    res = client.call_tool("register_user", {
        "full_name": "Priya Mehta",
        "email": email_a,
        "phone": "9123456789"
    }, req_id=req); req += 1
    assert res["success"], res
    uid_a = res["user"]["user_id"]
    ok(f"User registered: {uid_a}")

    # Attempt get_registered_agent_id before KYC — must fail
    res = client.call_tool("get_registered_agent_id", {"user_id": uid_a}, req_id=req); req += 1
    assert not res["success"], "Expected failure: agent_id should not exist before KYC"
    assert "VERIFIED" in res["error"] or "not found" in res["error"].lower()
    ok("get_registered_agent_id correctly blocked before KYC")

    # Initiate KYC
    res = client.call_tool("initiate_kyc", {
        "user_id": uid_a,
        "documents_json": json.dumps({
            "AADHAAR": {"aadhaar_number": "888888888888"},
            "PAN":     {"pan_number": "PQRST5678G"},
        })
    }, req_id=req); req += 1
    assert res["success"], res
    sid_a = res["session_id"]
    ok(f"KYC initiated: {sid_a}")

    # Attempt get_registered_agent_id while INITIATED — must still fail
    res = client.call_tool("get_registered_agent_id", {"user_id": uid_a}, req_id=req); req += 1
    assert not res["success"]
    ok("get_registered_agent_id correctly blocked during INITIATED state")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 2 — agent_id issued on verify_and_generate_id")
    print("━" * 55)

    res = client.call_tool("verify_and_generate_id", {
        "user_id": uid_a,
        "session_id": sid_a,
        "otp": "421596"
    }, req_id=req); req += 1
    assert res["success"], res
    assert res["kyc_status"] == "VERIFIED"
    assert "agent_id" in res and res["agent_id"]
    agent_id_a = res["agent_id"]
    ok(f"KYC verified. agent_id issued: {agent_id_a}")

    # Validate agent_id format: name_agent-<hash>@pinelabsUPAI
    assert "@pinelabsUPAI" in agent_id_a, f"Unexpected format: {agent_id_a}"
    assert "_agent-" in agent_id_a
    ok(f"agent_id format correct: contains '_agent-' and '@pinelabsUPAI'")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 3 — get_registered_agent_id after VERIFIED")
    print("━" * 55)

    res = client.call_tool("get_registered_agent_id", {"user_id": uid_a}, req_id=req); req += 1
    assert res["success"], res
    assert res["agent_id"] == agent_id_a
    ok(f"get_registered_agent_id returns correct ID: {res['agent_id']}")
    print(f"  · full_name : {res['full_name']}")
    print(f"  · created_at: {res['created_at']}")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 4 — agent_id is IDEMPOTENT across re-verification")
    print("━" * 55)

    # Re-verify with Mobile
    res = client.call_tool("re_verify_kyc", {
        "user_id": uid_a,
        "documents_json": json.dumps({
            "MOBILE": {"mobile_number": "9123456789"}
        })
    }, req_id=req); req += 1
    assert res["success"], res
    new_sid = res["session_id"]
    ok(f"Re-verify initiated: {new_sid}")

    res = client.call_tool("verify_and_generate_id", {
        "user_id": uid_a,
        "session_id": new_sid,
        "otp": "421596"
    }, req_id=req); req += 1
    assert res["success"], res
    assert res["agent_id"] == agent_id_a, (
        f"agent_id changed after re-verify! Before: {agent_id_a}, After: {res['agent_id']}"
    )
    ok(f"agent_id unchanged after re-verification ✓ (idempotency confirmed)")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 5 — fetch_verified_profile includes agent_id")
    print("━" * 55)

    res = client.call_tool("fetch_verified_profile", {"user_id": uid_a}, req_id=req); req += 1
    assert res["success"], res
    assert res["agent_id"] == agent_id_a
    ok(f"fetch_verified_profile includes agent_id: {res['agent_id']}")
    ok(f"verified_documents count: {len(res['verified_documents'])}")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 6 — two different users get two different agent_ids")
    print("━" * 55)

    # Register second user
    res = client.call_tool("register_user", {
        "full_name": "Amit Kumar Singh",
        "email": email_b,
        "phone": "9000000001"
    }, req_id=req); req += 1
    assert res["success"], res
    uid_b = res["user"]["user_id"]
    ok(f"Second user registered: {uid_b}")

    res = client.call_tool("initiate_kyc", {
        "user_id": uid_b,
        "documents_json": json.dumps({
            "AADHAAR": {"aadhaar_number": "777777777777"},
            "PAN":     {"pan_number": "LMNOP9012H"},
        })
    }, req_id=req); req += 1
    assert res["success"], res
    sid_b = res["session_id"]

    res = client.call_tool("verify_and_generate_id", {
        "user_id": uid_b,
        "session_id": sid_b,
        "otp": "421596"
    }, req_id=req); req += 1
    assert res["success"], res
    agent_id_b = res["agent_id"]
    ok(f"Second user agent_id: {agent_id_b}")

    assert agent_id_a != agent_id_b, "Two different users got the same agent_id!"
    ok("agent_ids are unique across different users ✓")

    # ════════════════════════════════════════════════════
    print("\n━" * 56)
    print("BLOCK 7 — list_registered_users shows both VERIFIED")
    print("━" * 55)

    res = client.call_tool("list_registered_users", {
        "kyc_status_filter": "VERIFIED"
    }, req_id=req); req += 1
    assert res["success"]
    verified_ids = [u["user_id"] for u in res["users"]]
    assert uid_a in verified_ids
    assert uid_b in verified_ids
    ok(f"Both users appear in VERIFIED list (total: {res['total']})")

    # ════════════════════════════════════════════════════
    print("\n" + "=" * 55)
    print("🎉 ALL REGISTRY TESTS PASSED")
    print("=" * 55)
    print(f"\n  User A  : {uid_a}")
    print(f"  Agent A : {agent_id_a}")
    print(f"  User B  : {uid_b}")
    print(f"  Agent B : {agent_id_b}")
    client.close()


if __name__ == "__main__":
    run_registry_tests()
