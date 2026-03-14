# KYC MCP Server

A production-ready **KYC (Know Your Customer) MCP Server** with mock DigiLocker integration, multi-step OTP verification, and a plugin-based verifier architecture. Built on the **Model Context Protocol (MCP)** — exposing KYC capabilities as tools that any MCP-compatible client (Claude Desktop, custom apps, automated pipelines) can invoke over HTTP/SSE.

---

## Table of Contents

1. [What is this?](#what-is-this)
2. [Architecture](#architecture)
3. [Setup & Installation](#setup--installation)
4. [The KYC Flow — Step by Step](#the-kyc-flow--step-by-step)
5. [Integration Guide](#integration-guide)
   - [Integrating via Python (direct SSE client)](#option-a-python-direct-sse-client)
   - [Integrating via Claude Desktop](#option-b-claude-desktop)
   - [Integrating via any MCP-compatible client](#option-c-any-mcp-compatible-client)
6. [MCP Tools Reference](#mcp-tools-reference)
7. [Test Data (Mock DigiLocker)](#test-data-mock-digilocker)
8. [Error Handling & Status Codes](#error-handling--status-codes)
9. [Adding a New Document Type](#adding-a-new-document-type)
10. [Database Schema](#database-schema)
11. [Environment Variables](#environment-variables)

---

## What is this?

This server exposes KYC verification as **MCP tools** — structured, callable functions that any AI agent or application can invoke without knowing the internals. Think of it as a KYC microservice, but surfaced through the Model Context Protocol instead of REST.

**What it does:**
- Registers users and manages their KYC lifecycle
- Registers customer-controlled agents and verifies their routing eligibility
- Supports a simplified phone-first KYC flow with dummy OTP confirmation
- Stores all results and a full audit trail in SQLite (`kyc_store.db`)
- Automatically triggers a unique **Agent ID** generation bounded to the user upon verification completion and saves this in `agent_registry.db`.
- Exposes everything as 12 MCP tools over HTTP/SSE

**What it is NOT (yet):**
- Connected to real UIDAI / NSDL / telecom APIs (mock only)
- Production-hardened (no rate limiting, no auth — add these before deploying)

---

## Architecture

```
kyc_mcp_server/
├── server.py                  ← MCP server entry point, 12 tools defined here
├── kyc_service.py             ← All business logic (tools call this)
├── registry_service.py        ← Unique Agent ID generation and lookup logic
├── otp_service.py             ← OTP verification logic
├── test_flow.py               ← End-to-end test script
├── test_registry_flow.py      ← Agent registry integration test
├── requirements.txt
│
├── db/
│   ├── database.py            ← SQLite: core kyc_store.db logic
│   └── registry.py            ← SQLite: agent_registry.db logic
│
└── verifiers/
    ├── base.py                ← BaseVerifier abstract class (plugin contract)
    ├── registry.py            ← ⭐ Register new document types here
    ├── mock_digilocker.py     ← Mock test data + name-match logic
    ├── aadhaar_verifier.py    ← Aadhaar Card verifier
    ├── pan_verifier.py        ← PAN Card verifier
    └── mobile_verifier.py     ← Mobile Number verifier
```

**Key design principles:**
- **Thin MCP tools** — tools are JSON wrappers; all logic lives in `kyc_service.py`
- **Plugin verifiers** — adding a new document type = 1 new file + 1 line in `registry.py`
- **Full audit trail** — every event logged to SQLite with timestamps
- **Separation of concerns** — DB / service / transport / verifiers are fully decoupled

---

## Setup & Installation

### Prerequisites
- Python 3.10 or higher
- pip

### Steps

```bash
# 1. Clone / download the project
cd ~/your-projects/kyc_mcp_server

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Mac / Linux
# venv\Scripts\activate         # Windows

# 3. Install dependencies using the venv's pip
python -m pip install -r requirements.txt

# 4. Start the server
python server.py
```

On success you will see:
```
🚀 KYC MCP Server starting on http://0.0.0.0:8000
   SSE endpoint : http://0.0.0.0:8000/sse
   Tools        : 12 tools registered
   Storage      : SQLite → /path/to/kyc_store.db (+ /path/to/agent_registry.db)
   OTP          : Fixed (421596), valid 10 min
```

The server creates `kyc_store.db` automatically on first run. To run on a different port:
```bash
KYC_MCP_PORT=9090 python server.py
```

### Verify the server is up
```bash
curl -N http://localhost:8000/sse
# You will see a streaming SSE connection. Ctrl+C to exit.
```

---

## The KYC Flow — Step by Step

There are two flows: **Initial KYC** for new users, and **Re-verification** for existing users.

### Initial KYC Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INITIAL KYC FLOW                             │
└─────────────────────────────────────────────────────────────────────┘

  STEP 1 — Register
  ─────────────────
  Call: register_user(phone, full_name?, email?)
  
  → Creates a user record in the DB
  → Returns user_id
  → KYC status set to: PENDING
  → Does NOT start KYC yet

         │
         ▼

  STEP 2 — Initiate KYC
  ──────────────────────
  Call: initiate_kyc(user_id)
  
  → Creates a KYC session (status: OTP_PENDING)
  → Returns session_id
  → KYC status set to: INITIATED

         │
         ▼

  STEP 3 — Confirm OTP
  ─────────────────────
  Call: confirm_kyc_otp(user_id, session_id, otp)
  
  Any non-empty OTP works in the simplified mock flow
  OTP is valid for 10 minutes from session creation.
  
  On empty OTP  → error returned, session stays OTP_PENDING, retry allowed
  On any non-empty OTP → user is marked VERIFIED and an agent ID is generated
  
         │
         ├─── All docs pass? ──► KYC status = VERIFIED ✅
         │                        session status = DOC_VERIFIED
         │
         └─── Any doc fails? ──► KYC status = FAILED ❌
                                  session status = DOC_FAILED
                                  failure_reason explains which doc failed and why

         │
         ▼

  STEP 4 — Fetch Profile (optional)
  ───────────────────────────────────
  Call: fetch_verified_profile(user_id)
  
  Only available when kyc_status == VERIFIED
  Returns masked document data (e.g. XXXX-XXXX-1234 for Aadhaar)
  and all extracted fields from each verified document.
```

### Re-verification Flow

Use this when a user's KYC has FAILED, or when they want to add / replace documents.

```
  Call: re_verify_kyc(user_id, documents_json)
  
  → Cancels any pending session
  → Creates a new RE_VERIFY session
  → Allows submitting different or additional documents
  → Full OTP + document verification runs again
  
  Then call confirm_kyc_otp with the new session_id — same as initial flow.
```

### Document Verification Logic

When OTP is confirmed, each submitted document is run through its verifier:

```
For each document:
  1. Look up the document number in mock DigiLocker
     → Not found?  → verified = False, failure_reason = "not found"
  
  2. Check document status (e.g. PAN must be ACTIVE)
     → Inactive?   → verified = False, failure_reason = "not active"
  
  3. Name match: compare user's registered full_name against name on document
     → Mismatch?   → verified = True BUT name_matched = False
                      failure_reason = "Name does not match"
     → Match?      → verified = True, name_matched = True ✅

Overall KYC passes only if ALL documents pass both checks.
```

---

## Integration Guide

The server speaks the **MCP protocol over HTTP/SSE**. There are three ways to integrate.

### The MCP Protocol (all integrations need to know this)

MCP over SSE uses a two-channel model:
- **SSE channel** (`GET /sse`) — server pushes responses to client
- **Messages channel** (`POST /messages/?session_id=...`) — client sends requests

The handshake sequence before any tool call:
```
1. Client opens GET /sse
   Server sends: data: /messages/?session_id=<uuid>

2. Client POSTs to that session URL:
   {"jsonrpc":"2.0","id":0,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{...}}}

3. Client POSTs:
   {"jsonrpc":"2.0","method":"notifications/initialized","params":{}}

4. Now tool calls work:
   {"jsonrpc":"2.0","id":1,"method":"tools/call",
    "params":{"name":"register_user","arguments":{...}}}

5. Response arrives on the SSE stream (not in the POST response body)
```

---

### Option A: Python (direct SSE client)

The included `test_flow.py` contains a fully working `MCPClient` class you can import and reuse.

```python
from test_flow import MCPClient
import json

client = MCPClient("http://localhost:8000")
client.connect()
client.initialize()

# Register a user
res = client.call_tool("register_user", {
    "full_name": "Priya Mehta",
    "email": "priya@example.com",
    "phone": "9123456789"
}, req_id=1)
user_id = res["user"]["user_id"]

# Initiate KYC
res = client.call_tool("initiate_kyc", {
    "user_id": user_id,
    "documents_json": json.dumps({
        "AADHAAR": {"aadhaar_number": "888888888888"},
        "PAN":     {"pan_number": "PQRST5678G"}
    })
}, req_id=2)
session_id = res["session_id"]

# Confirm OTP
res = client.call_tool("confirm_kyc_otp", {
    "user_id": user_id,
    "session_id": session_id,
    "otp": "421596"
}, req_id=3)
print(res["kyc_status"])  # → VERIFIED

client.close()
```

Run the full end-to-end test suite:
```bash
python test_flow.py
```

---

### Option B: Claude Desktop

Connect the KYC server to Claude Desktop and drive it entirely with natural language.

**1. Find your Claude Desktop config file:**
- Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**2. Add the server:**
```json
{
  "mcpServers": {
    "kyc-server": {
      "url": "http://localhost:8000/sse",
      "transport": "sse"
    }
  }
}
```

**3. Restart Claude Desktop.** You will see `kyc-server` appear in the tools panel.

**4. Use natural language to invoke the tools:**

| You say | What happens |
|---------|-------------|
| *"Register a new user named Rahul Sharma with email rahul@test.com"* | Calls `register_user` |
| *"Start KYC for user \<id\> using Aadhaar 999999999999 and PAN ABCDE1234F"* | Calls `initiate_kyc` |
| *"Confirm the OTP 421596 for session \<id\>"* | Calls `confirm_kyc_otp` |
| *"Verify user \<id\> and session \<session_id\> with 421596 and get me their new Agent ID"* | Calls `verify_and_generate_id` |
| *"What is the unique agent ID for user \<id\>?"* | Calls `get_registered_agent_id` |
| *"What is the KYC status of user \<id\>?"* | Calls `check_kyc_status` |
| *"Show me the full verified profile for user \<id\>"* | Calls `fetch_verified_profile` |
| *"List all users who have been verified"* | Calls `list_registered_users` with filter VERIFIED |
| *"What document types can I verify?"* | Calls `list_supported_document_types` |

Claude handles the session management, parameter extraction, and multi-step flow automatically.

---

### Option C: Any MCP-compatible client

Any client that speaks MCP over SSE can connect. Point it at:
```
http://localhost:8000/sse
```

**Using the `mcp` CLI inspector:**
```bash
# Fix npm permissions if needed (one-time)
sudo chown -R $(id -u):$(id -g) ~/.npm

# Launch the visual inspector (opens at http://localhost:5173)
mcp dev server.py
```

**Using curl (raw JSON-RPC):**

Step 1 — open SSE in one terminal and note the session URL printed:
```bash
curl -N http://localhost:8000/sse
# data: /messages/?session_id=abc123...
```

Step 2 — in another terminal, initialize and call tools:
```bash
SESSION="http://localhost:8000/messages/?session_id=abc123..."

# Initialize
curl -s -X POST "$SESSION" -H "Content-Type: application/json" -d \
  '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

# Initialized notification
curl -s -X POST "$SESSION" -H "Content-Type: application/json" -d \
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'

# Register a user
curl -s -X POST "$SESSION" -H "Content-Type: application/json" -d \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"register_user","arguments":{"full_name":"Rahul Sharma","email":"rahul@test.com","phone":"9876543210"}}}'
```

Responses come back on the SSE stream in terminal 1.

---

## MCP Tools Reference

All tools return a JSON string. Every response has a `"success": true/false` field.

### `register_user`
Register a new user. Does not start KYC.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `phone` | string | ✅ | 10-digit Indian mobile number |
| `full_name` | string | — | Optional display name |
| `email` | string | — | Optional email |

```json
// Request
{"phone": "9876543210", "full_name": "Rahul Sharma"}

// Response
{
  "success": true,
  "user": {"user_id": "<uuid>", "kyc_status": "PENDING", ...},
  "next_step": "Call initiate_kyc with this user_id..."
}
```

---

### `initiate_kyc`
Step 1 of KYC. Starts the simplified phone-based OTP flow.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `user_id` | string | ✅ | UUID from `register_user` |
| `documents_json` | string | — | Ignored in the simplified flow; kept for backward compatibility |

```json
// Response
{
  "success": true,
  "session_id": "<uuid>",
  "documents_received": [],
  "otp_instruction": "Submit any OTP via confirm_kyc_otp. Session valid for 10 minutes."
}
```

---

### `confirm_kyc_otp`
Step 2 of KYC. Accepts any non-empty OTP and completes verification.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `user_id` | string | ✅ | User UUID |
| `session_id` | string | ✅ | Session UUID from `initiate_kyc` |
| `otp` | string | ✅ | Any non-empty value |

```json
// Response (success)
{
  "success": true,
  "kyc_status": "VERIFIED",
  "document_results": [
    {
      "doc_type": "AADHAAR",
      "verified": true,
      "name_matched": true,
      "extracted_data": {"name_on_record": "Rahul Sharma", "dob": "1990-05-15", ...}
    }
  ]
}

// Response (failure)
{
  "success": false,
  "kyc_status": "FAILED",
  "message": "KYC verification failed. Issues: PAN: Name mismatch"
}
```

---

### `verify_and_generate_id`
A convenience wrapper for confirmation. Verifies OTP and documents, and automatically returns the generated `agent_id` string directly inside the response for verified users. Follows the exact same arguments as `confirm_kyc_otp`.

```json
// Response (success snippet)
{
  "success": true,
  "kyc_status": "VERIFIED",
  "agent_id": "rahulsharma_agent-565c8f94@pinelabsUPAI"
}
```

---

### `get_registered_agent_id`
Fetch the unique Agent ID for an already `VERIFIED` user out of the registry. Returns the mapped ID string and generation datestamps.

```json
// Request
{"user_id": "<uuid>"}

// Response
{
  "success": true,
  "agent_id": "testagentsharma_agent-565c8f94@pinelabsUPAI",
  "user_id": "<uuid>",
  "full_name": "Test Agent Sharma",
  "created_at": "2026-03-14T10:00:00+00:00"
}
```

---

### `check_kyc_status`
Get current KYC status and latest session summary.

```json
// Request
{"user_id": "<uuid>"}

// Response
{
  "success": true,
  "kyc_status": "VERIFIED",
  "latest_session": {"session_id": "...", "status": "DOC_VERIFIED", "completed_at": "..."}
}
```

---

### `fetch_verified_profile`
Full verified profile with masked document data. Only available when `kyc_status == VERIFIED`.

```json
// Response
{
  "success": true,
  "user": {"full_name": "Rahul Sharma", "email": "...", "kyc_status": "VERIFIED"},
  "verified_documents": [
    {
      "doc_type": "AADHAAR",
      "doc_number_masked": "XXXX-XXXX-9999",
      "verified_at": "2026-03-14T10:00:00+00:00",
      "extracted_data": {"name_on_record": "Rahul Sharma", "state": "Karnataka", ...}
    }
  ]
}
```

---

### `re_verify_kyc`
Restart KYC with new or replacement documents. Allows adding document types or correcting failed ones.

```json
// Request — add a Mobile that wasn't in the original submission
{
  "user_id": "<uuid>",
  "documents_json": "{\"MOBILE\": {\"mobile_number\": \"9876543210\"}}"
}
```

---

### `list_registered_users`
List all users, optionally filtered by KYC status.

```json
// Request — show only verified users
{"kyc_status_filter": "VERIFIED"}

// Response
{
  "total": 2,
  "filter": "VERIFIED",
  "users": [{"full_name": "...", "kyc_status": "VERIFIED", ...}]
}
```

---

### `register_agent`
Register a customer-controlled agent with AR before routing traffic to an ecommerce MCP.

```json
// Request
{
  "agent_name": "Rahul Shopper Agent",
  "phone": "9876543210",
  "description": "Customer-controlled ecommerce agent",
  "capabilities_json": "[\"ECOMMERCE_ACCESS\", \"CHECKOUT\"]"
}

// Response
{
  "success": true,
  "agent": {
    "agent_id": "<uuid>",
    "user_id": "<uuid>",
    "agent_name": "Rahul Shopper Agent",
    "capabilities": ["ECOMMERCE_ACCESS", "CHECKOUT"],
    "status": "ACTIVE"
  }
}
```

---

### `verify_agent_capability`
Check whether an agent is registered with AR and can be routed to an ecommerce MCP.

```json
// Request
{"agent_id": "<uuid>", "capability": "ECOMMERCE_ACCESS"}

// Response (registered and allowed)
{
  "success": true,
  "allowed_to_route": true,
  "route_decision": "ALLOW",
  "verified_capability": "ECOMMERCE_ACCESS"
}

// Response (unknown agent)
{
  "success": false,
  "allowed_to_route": false,
  "route_decision": "BLOCK_REGISTER_AGENT",
  "registration_required": true,
  "message": "Agent is not registered with AR. Please register the agent first before routing traffic to the ecommerce MCP."
}
```

---

### `list_supported_document_types`
Discover verifiable document types and their required fields. Call this first when building integrations.

```json
// Response
{
  "supported_documents": [
    {"doc_type": "AADHAAR", "display_name": "Aadhaar Card", "required_fields": ["aadhaar_number"]},
    {"doc_type": "PAN",     "display_name": "PAN Card",     "required_fields": ["pan_number"]},
    {"doc_type": "MOBILE",  "display_name": "Mobile Number","required_fields": ["mobile_number"]}
  ]
}
```

---

## Test Data (Mock DigiLocker)

Only these specific numbers pass verification. All others return "not found".

### Aadhaar Numbers
| Number | Name on Record | State |
|--------|---------------|-------|
| `999999999999` | Rahul Sharma | Karnataka |
| `888888888888` | Priya Mehta | Maharashtra |
| `777777777777` | Amit Kumar Singh | Delhi |
| `666666666666` | Sneha Iyer | Tamil Nadu |

Format rules: 12 digits, must not start with 0 or 1.

### PAN Numbers
| Number | Name on Record | Type |
|--------|---------------|------|
| `ABCDE1234F` | Rahul Sharma | Individual |
| `PQRST5678G` | Priya Mehta | Individual |
| `LMNOP9012H` | Amit Kumar Singh | Individual |
| `UVWXY3456I` | Sneha Iyer | Individual |
| `ZZZZZ9999Z` | Test Business Entity | Company |

Format rules: `AAAAA9999A` — 5 uppercase letters, 4 digits, 1 uppercase letter.

### Mobile Numbers
| Number | Name on Record | Operator |
|--------|---------------|----------|
| `9876543210` | Rahul Sharma | Airtel |
| `9123456789` | Priya Mehta | Jio |
| `9000000001` | Amit Kumar Singh | Vi |
| `9000000002` | Sneha Iyer | BSNL |

Format rules: 10 digits, must start with 6–9. `+91` or `0` prefix is stripped automatically.

### OTP
Any non-empty OTP is accepted — valid for **10 minutes** from session creation.

### Name Matching
The registered `full_name` must share at least one token (word) with the name on the document record. Matching is case-insensitive. Examples:

| Registered name | Document name | Result |
|----------------|--------------|--------|
| `Rahul Sharma` | `Rahul Sharma` | ✅ Match |
| `Rahul` | `Rahul Sharma` | ✅ Match (token overlap) |
| `R. Sharma` | `Rahul Sharma` | ✅ Match (`Sharma` overlaps) |
| `John Doe` | `Rahul Sharma` | ❌ No match |

---

## Error Handling & Status Codes

Every tool response has `"success": true` or `"success": false`. On failure, `"error"` contains the reason.

### KYC Status Values
| Status | Meaning | Next action |
|--------|---------|-------------|
| `PENDING` | Registered, KYC not started | Call `initiate_kyc` |
| `INITIATED` | Session active, OTP pending | Call `confirm_kyc_otp` |
| `VERIFIED` | All documents verified ✅ | Call `fetch_verified_profile` |
| `FAILED` | One or more documents failed ❌ | Call `re_verify_kyc` |
| `BLOCKED` | Admin-blocked (reserved) | Contact admin |

### Session Status Values
| Status | Meaning |
|--------|---------|
| `OTP_PENDING` | Session created, awaiting OTP |
| `OTP_CONFIRMED` | OTP accepted, verification in progress |
| `DOC_VERIFIED` | All documents verified |
| `DOC_FAILED` | One or more documents failed |

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `"already exists"` | Email already registered | Use existing user_id or different email |
| `"Invalid Aadhaar format"` | Aadhaar starts with 0/1 or wrong length | Use 12-digit number starting with 2–9 |
| `"Invalid PAN format"` | Doesn't match AAAAA9999A | Check format: 5 letters + 4 digits + 1 letter |
| `"not found in DigiLocker"` | Not a test number | Use a number from the test data table |
| `"Name does not match"` | Registered name has no token overlap with document | Ensure at least one word matches |
| `"OTP has expired"` | More than 10 minutes since `initiate_kyc` | Call `initiate_kyc` again to get a fresh session |
| `"Incorrect OTP"` | Wrong OTP submitted | Use `421596` |
| `"Profile not available"` | User is not VERIFIED | Complete KYC first |

---

## Adding a New Document Type

The verifier system uses a plugin pattern. Adding a new document type requires exactly two changes and zero modifications to existing files.

**Step 1 — Create the verifier file** `verifiers/driving_licence_verifier.py`:

```python
import re
from .base import BaseVerifier, VerificationResult
from .mock_digilocker import name_match

# Add test records to mock_digilocker.py:
# DL_RECORDS = {"MH01 20110012345": {"name": "Rahul Sharma", ...}}
from .mock_digilocker import DL_RECORDS

class DrivingLicenceVerifier(BaseVerifier):

    @property
    def doc_type(self) -> str:
        return "DRIVING_LICENCE"

    @property
    def display_name(self) -> str:
        return "Driving Licence"

    @property
    def required_fields(self) -> list[str]:
        return ["dl_number", "date_of_birth"]

    def validate_format(self, payload: dict) -> tuple[bool, str | None]:
        dl = payload.get("dl_number", "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}[0-9]{2} [0-9]{11}", dl):
            return False, "Invalid DL format. Expected: XX99 99999999999"
        return True, None

    def verify(self, payload: dict, user_name: str) -> VerificationResult:
        dl = payload["dl_number"].strip().upper()
        record = DL_RECORDS.get(dl)
        if not record:
            return VerificationResult(
                doc_type=self.doc_type, doc_number=dl,
                verified=False, name_matched=False, extracted_data={},
                failure_reason="DL not found in records."
            )
        matched = name_match(user_name, record["name"])
        return VerificationResult(
            doc_type=self.doc_type, doc_number=dl,
            verified=True, name_matched=matched,
            extracted_data={"name_on_record": record["name"]},
            failure_reason=None if matched else "Name mismatch."
        )
```

**Step 2 — Register it** in `verifiers/registry.py` (the ONLY other file to touch):

```python
from .driving_licence_verifier import DrivingLicenceVerifier

VERIFIERS = [
    AadhaarVerifier(),
    PANVerifier(),
    MobileVerifier(),
    DrivingLicenceVerifier(),   # ← add here
]
```

Done. All existing KYC tools now support `DRIVING_LICENCE` automatically. No other files change.

---

## Database Schema

The core SQLite database (`kyc_store.db`) is created automatically on first run to manage KYC history and sessions. 

Additionally, a parallel database (`agent_registry.db`) is automatically populated representing the verified Agent mappings.

### Core Database (`kyc_store.db`)

```sql
-- User registry
users (
    id          TEXT PRIMARY KEY,   -- UUID
    full_name   TEXT,
... (truncated) ...
```

### Registry Database (`agent_registry.db`)

```sql
-- Agent Registry Mapping
registered_agents (
    agent_id    TEXT PRIMARY KEY,   -- e.g. rahulsharma_agent-a12b3c4d@pinelabsUPAI
    user_id     TEXT UNIQUE,        -- UUID from users table
    full_name   TEXT,
    email       TEXT,
    phone       TEXT,
    created_at  TEXT
)
```

**Useful queries:**

```bash
# Agent mapping retrieval
sqlite3 agent_registry.db
SELECT agent_id, user_id, full_name FROM registered_agents;

# General session retrieval
sqlite3 kyc_store.db
SELECT full_name, email, kyc_status, created_at FROM users;
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KYC_MCP_PORT` | `8000` | Port the server listens on |
| `KYC_MCP_HOST` | `0.0.0.0` | Host/interface to bind to |

```bash
# Example: run on port 9090, localhost only
KYC_MCP_PORT=9090 KYC_MCP_HOST=127.0.0.1 python server.py
```
