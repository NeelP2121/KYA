"""
KYC MCP Server — End-to-End Test Flow
Compatible with mcp 1.26.0 SSE protocol.

Run:
    # Terminal 1 — server
    python server.py

    # Terminal 2 — tests
    python test_flow.py

Covers all 10 KYC tools:
  register_user, initiate_kyc, confirm_kyc_otp, verify_and_generate_id,
  get_registered_agent_id, check_kyc_status, fetch_verified_profile,
  re_verify_kyc, list_registered_users, list_supported_document_types
"""

import httpx
import json
import os
import threading
import queue
import time
import uuid

BASE = os.environ.get("KYC_MCP_BASE_URL", "http://localhost:8000")


# ─────────────────────────────────────────────────────────
# MCP SSE Client
# ─────────────────────────────────────────────────────────

class MCPClient:
    """
    Minimal MCP 1.26.0-compatible SSE client.
    Protocol:
      1. GET /sse  →  server sends session endpoint URL
      2. POST initialize handshake
      3. POST tools/call  →  response arrives on SSE stream
    """

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
            raise RuntimeError(
                "Could not establish SSE session. Is the server running?\n"
                "  Start it with: python server.py"
            )
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
        raise RuntimeError(f"No response for request id={req_id} within {timeout}s")

    def initialize(self):
        """MCP handshake — call once after connect()."""
        self._post({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        })
        try:
            self.response_queue.get(timeout=3)
        except queue.Empty:
            pass
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        time.sleep(0.3)
        print("✅ MCP handshake complete\n")

    def call_tool(self, tool_name: str, arguments: dict, req_id: int) -> dict:
        self._post({
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        })
        msg = self._wait(req_id)
        if "error" in msg:
            raise RuntimeError(f"MCP error [{tool_name}]: {msg['error']}")
        content = msg["result"]["content"]
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        if text is None:
            raise RuntimeError(f"No text content in response: {msg}")
        return json.loads(text)

    def close(self):
        self._stop.set()


# ─────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────

def run_tests():
    client = MCPClient(BASE)
    print(f"Connecting to MCP server at {BASE}...\n")
    client.connect()
    client.initialize()

    req = 1
    USER_ID = None
    SESSION_ID = None
    AGENT_ID = None
    user_email = f"rahul+{uuid.uuid4().hex[:8]}@example.com"

    # ── 1. register_user ─────────────────────────────────
    print("=" * 55)
    print("TEST 1 — register_user")
    print("=" * 55)
    res = client.call_tool("register_user", {
        "full_name": "Rahul Sharma",
        "email": user_email,
        "phone": "9876543210"
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"], f"Expected success: {res}"
    USER_ID = res["user"]["user_id"]
    print(f"\n→ user_id: {USER_ID}\n")

    # ── 2. Duplicate email (error case) ──────────────────
    print("=" * 55)
    print("TEST 2 — register_user (duplicate email, expect failure)")
    print("=" * 55)
    res = client.call_tool("register_user", {
        "full_name": "Rahul Sharma",
        "email": user_email,
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    # ── 3. check_kyc_status (PENDING) ────────────────────
    print("=" * 55)
    print("TEST 3 — check_kyc_status (expect PENDING)")
    print("=" * 55)
    res = client.call_tool("check_kyc_status", {"user_id": USER_ID}, req_id=req); req += 1
    assert res["kyc_status"] == "PENDING"
    print(f"✅ Status: {res['kyc_status']}\n")

    # ── 4. list_supported_document_types ─────────────────
    print("=" * 55)
    print("TEST 4 — list_supported_document_types")
    print("=" * 55)
    res = client.call_tool("list_supported_document_types", {}, req_id=req); req += 1
    for doc in res["supported_documents"]:
        print(f"  {doc['doc_type']:<10} ({doc['display_name']}) — fields: {doc['required_fields']}")
    print()

    # ── 5. initiate_kyc — bad format ─────────────────────
    print("=" * 55)
    print("TEST 5 — initiate_kyc (bad Aadhaar format, expect failure)")
    print("=" * 55)
    res = client.call_tool("initiate_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"AADHAAR": {"aadhaar_number": "012345"}})
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    # ── 6. initiate_kyc — unknown doc type ───────────────
    print("=" * 55)
    print("TEST 6 — initiate_kyc (unknown doc type, expect failure)")
    print("=" * 55)
    res = client.call_tool("initiate_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"DRIVING_LICENCE": {"number": "X"}})
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    # ── 7. initiate_kyc — valid ───────────────────────────
    print("=" * 55)
    print("TEST 7 — initiate_kyc (Aadhaar + PAN + Mobile)")
    print("=" * 55)
    res = client.call_tool("initiate_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({
            "AADHAAR": {"aadhaar_number": "999999999999"},
            "PAN":     {"pan_number": "ABCDE1234F"},
            "MOBILE":  {"mobile_number": "9876543210"},
        })
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    SESSION_ID = res["session_id"]
    print(f"\n→ session_id: {SESSION_ID}\n")

    # ── 8. confirm_kyc_otp — wrong OTP ───────────────────
    print("=" * 55)
    print("TEST 8 — confirm_kyc_otp (wrong OTP, expect failure)")
    print("=" * 55)
    res = client.call_tool("confirm_kyc_otp", {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "otp": "000000"
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    # ── 9. verify_and_generate_id — correct OTP ──────────
    print("=" * 55)
    print("TEST 9 — verify_and_generate_id (OTP 421596 → VERIFIED + agent_id)")
    print("=" * 55)
    res = client.call_tool("verify_and_generate_id", {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "otp": "421596"
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    assert res["kyc_status"] == "VERIFIED"
    assert "agent_id" in res
    AGENT_ID = res["agent_id"]
    print(f"\n→ agent_id: {AGENT_ID}\n")

    # ── 10. get_registered_agent_id ──────────────────────
    print("=" * 55)
    print("TEST 10 — get_registered_agent_id")
    print("=" * 55)
    res = client.call_tool("get_registered_agent_id", {"user_id": USER_ID}, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    assert res["agent_id"] == AGENT_ID
    print()

    # ── 11. check_kyc_status (VERIFIED) ──────────────────
    print("=" * 55)
    print("TEST 11 — check_kyc_status (expect VERIFIED)")
    print("=" * 55)
    res = client.call_tool("check_kyc_status", {"user_id": USER_ID}, req_id=req); req += 1
    assert res["kyc_status"] == "VERIFIED"
    print(f"✅ Status: {res['kyc_status']}\n")

    # ── 12. fetch_verified_profile ───────────────────────
    print("=" * 55)
    print("TEST 12 — fetch_verified_profile")
    print("=" * 55)
    res = client.call_tool("fetch_verified_profile", {"user_id": USER_ID}, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    assert len(res["verified_documents"]) == 3
    assert res["agent_id"] == AGENT_ID
    print()

    # ── 13. re_verify_kyc ────────────────────────────────
    print("=" * 55)
    print("TEST 13 — re_verify_kyc (replace PAN)")
    print("=" * 55)
    res = client.call_tool("re_verify_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"PAN": {"pan_number": "ABCDE1234F"}})
    }, req_id=req); req += 1
    assert res["success"]
    new_session = res["session_id"]
    print(f"→ new session_id: {new_session}")

    res = client.call_tool("verify_and_generate_id", {
        "user_id": USER_ID,
        "session_id": new_session,
        "otp": "421596"
    }, req_id=req); req += 1
    assert res["kyc_status"] == "VERIFIED"
    print(f"✅ Re-verify result: {res['kyc_status']}\n")

    # ── 14. list_registered_users ────────────────────────
    print("=" * 55)
    print("TEST 14 — list_registered_users (filter: VERIFIED)")
    print("=" * 55)
    res = client.call_tool("list_registered_users", {
        "kyc_status_filter": "VERIFIED"
    }, req_id=req); req += 1
    print(f"Total VERIFIED users: {res['total']}")
    for u in res["users"]:
        print(f"  {u['full_name']:<25} {u['email']:<35} {u['kyc_status']}")

    print("\n" + "=" * 55)
    print("🎉 ALL 14 TEST CASES PASSED")
    print("=" * 55)
    print(f"\n  user_id  : {USER_ID}")
    print(f"  agent_id : {AGENT_ID}")
    client.close()


if __name__ == "__main__":
    run_tests()
