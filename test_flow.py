"""
KYC MCP Server — Test Flow
Compatible with mcp 1.26.0 SSE protocol.

Protocol:
1. GET /sse          → server sends session endpoint URL
2. POST initialize   → MCP handshake
3. POST tools/call   → tool invocations
4. Responses arrive on the SSE stream
"""

import httpx
import json
import os
import threading
import queue
import time
import uuid

BASE = os.environ.get("KYC_MCP_BASE_URL", "http://localhost:8000")


class MCPClient:

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_url = None
        self.response_queue = queue.Queue()
        self._stop = threading.Event()

    def connect(self):
        """Open SSE stream and wait for the session endpoint URL."""
        ready = threading.Event()

        def sse_listener():
            with httpx.Client(timeout=None) as client:
                with client.stream("GET", f"{self.base_url}/sse") as resp:
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            break

                        if line.startswith("data:"):
                            raw = line[len("data:"):].strip()

                            # Session URL comes as plain text like: /messages/?session_id=xxx
                            if not ready.is_set() and "/messages" in raw:
                                url = raw if raw.startswith("http") else self.base_url + raw
                                self.session_url = url
                                ready.set()
                                continue

                            # All subsequent data lines are JSON-RPC responses
                            if ready.is_set() and raw:
                                try:
                                    msg = json.loads(raw)
                                    self.response_queue.put(msg)
                                except json.JSONDecodeError:
                                    pass

        t = threading.Thread(target=sse_listener, daemon=True)
        t.start()

        if not ready.wait(timeout=10):
            raise RuntimeError("Could not get session URL. Is the server running?")

        print(f"✅ SSE session: {self.session_url}\n")

    def _post(self, payload: dict) -> httpx.Response:
        r = httpx.post(self.session_url, json=payload, timeout=10)
        if r.status_code not in (200, 202, 204):
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        return r

    def _wait_response(self, req_id: int, timeout: int = 15) -> dict:
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
        """MCP handshake — must be called once after connect()."""
        self._post({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
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

        self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })
        time.sleep(0.3)
        print("✅ MCP handshake complete\n")

    def call_tool(self, tool_name: str, arguments: dict, req_id: int = 1) -> dict:
        self._post({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            }
        })
        msg = self._wait_response(req_id)
        if "error" in msg:
            raise RuntimeError(f"MCP error calling '{tool_name}': {msg['error']}")
        content = msg["result"]["content"]
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        if text is None:
            raise RuntimeError(f"No text in response: {msg}")
        return json.loads(text)

    def close(self):
        self._stop.set()


def run_tests():
    client = MCPClient(BASE)
    print("Connecting to MCP server at", BASE)
    client.connect()
    client.initialize()

    req = 1
    USER_ID = None
    SESSION_ID = None
    AGENT_ID = None
    user_email = f"rahul+{uuid.uuid4().hex[:8]}@example.com"

    print("=== 1. REGISTER USER ===")
    res = client.call_tool("register_user", {
        "full_name": "Rahul Sharma",
        "email": user_email,
        "phone": "9876543210"
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"], f"Expected success: {res}"
    USER_ID = res["user"]["user_id"]
    print(f"→ user_id: {USER_ID}\n")

    print("=== 2. DUPLICATE EMAIL (expect failure) ===")
    res = client.call_tool("register_user", {
        "full_name": "Rahul Sharma",
        "email": user_email,
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    print("=== 3. VERIFY UNKNOWN AGENT (expect register-first block) ===")
    res = client.call_tool("verify_agent_capability", {
        "agent_id": str(uuid.uuid4()),
        "capability": "ECOMMERCE_ACCESS",
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert not res["success"]
    assert not res["allowed_to_route"]
    assert res["registration_required"]
    assert res["route_decision"] == "BLOCK_REGISTER_AGENT"
    print()

    print("=== 4. REGISTER AGENT FOR UNKNOWN USER (expect failure) ===")
    res = client.call_tool("register_agent", {
        "user_id": str(uuid.uuid4()),
        "agent_name": "Ghost Agent",
        "description": "Should fail because owner is unknown",
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert not res["success"]
    print()

    print("=== 5. REGISTER AGENT ===")
    res = client.call_tool("register_agent", {
        "user_id": USER_ID,
        "agent_name": "Rahul Shopper Agent",
        "description": "Customer-controlled ecommerce agent",
        "capabilities_json": json.dumps(["ECOMMERCE_ACCESS", "CHECKOUT"]),
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    AGENT_ID = res["agent"]["agent_id"]
    print(f"→ agent_id: {AGENT_ID}\n")

    print("=== 6. VERIFY REGISTERED AGENT (expect allow) ===")
    res = client.call_tool("verify_agent_capability", {
        "agent_id": AGENT_ID,
        "capability": "ECOMMERCE_ACCESS",
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    assert res["allowed_to_route"]
    assert res["route_decision"] == "ALLOW"
    print()

    print("=== 7. VERIFY MISSING CAPABILITY (expect block) ===")
    res = client.call_tool("verify_agent_capability", {
        "agent_id": AGENT_ID,
        "capability": "PAY_ORDER",
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert not res["success"]
    assert not res["allowed_to_route"]
    assert res["route_decision"] == "BLOCK_CAPABILITY_MISSING"
    print()

    print("=== 8. CHECK KYC STATUS (expect PENDING) ===")
    res = client.call_tool("check_kyc_status", {
        "user_id": USER_ID
    }, req_id=req); req += 1
    assert res["kyc_status"] == "PENDING"
    print(f"✅ Status: {res['kyc_status']}\n")

    print("=== 9. LIST SUPPORTED DOCUMENT TYPES ===")
    res = client.call_tool("list_supported_document_types", {}, req_id=req); req += 1
    for doc in res["supported_documents"]:
        print(f"  {doc['doc_type']} — required fields: {doc['required_fields']}")
    print()

    print("=== 10. BAD AADHAAR FORMAT (expect failure) ===")
    res = client.call_tool("initiate_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"AADHAAR": {"aadhaar_number": "012345"}})
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    print("=== 11. UNKNOWN DOC TYPE (expect failure) ===")
    res = client.call_tool("initiate_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"DRIVING_LICENCE": {"number": "X"}})
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    print("=== 12. INITIATE KYC ===")
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
    print(f"→ session_id: {SESSION_ID}\n")

    print("=== 13. WRONG OTP (expect failure) ===")
    res = client.call_tool("confirm_kyc_otp", {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "otp": "000000"
    }, req_id=req); req += 1
    assert not res["success"]
    print(f"✅ Correctly rejected: {res['error']}\n")

    print("=== 14. CORRECT OTP (421596) → expect VERIFIED ===")
    res = client.call_tool("confirm_kyc_otp", {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "otp": "421596"
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["kyc_status"] == "VERIFIED"
    print()

    print("=== 15. FETCH VERIFIED PROFILE ===")
    res = client.call_tool("fetch_verified_profile", {
        "user_id": USER_ID
    }, req_id=req); req += 1
    print(json.dumps(res, indent=2))
    assert res["success"]
    assert len(res["verified_documents"]) == 3
    print()

    print("=== 16. RE-VERIFY KYC ===")
    res = client.call_tool("re_verify_kyc", {
        "user_id": USER_ID,
        "documents_json": json.dumps({"PAN": {"pan_number": "ABCDE1234F"}})
    }, req_id=req); req += 1
    assert res["success"]
    new_session = res["session_id"]
    print(f"→ new session_id: {new_session}")
    res = client.call_tool("confirm_kyc_otp", {
        "user_id": USER_ID,
        "session_id": new_session,
        "otp": "421596"
    }, req_id=req); req += 1
    print(f"Re-verify result: {res['kyc_status']}\n")
    assert res["kyc_status"] == "VERIFIED"

    print("=== 17. LIST VERIFIED USERS ===")
    res = client.call_tool("list_registered_users", {
        "kyc_status_filter": "VERIFIED"
    }, req_id=req); req += 1
    print(f"Total VERIFIED: {res['total']}")
    for u in res["users"]:
        print(f"  {u['full_name']} | {u['email']} | {u['kyc_status']}")

    print("\n" + "=" * 50)
    print("🎉 ALL TESTS PASSED")
    print("=" * 50)
    client.close()


if __name__ == "__main__":
    run_tests()
