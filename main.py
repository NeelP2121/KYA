# main.py
# KYC Backend Service – Mocked DigiLocker Integration
# Run with: uvicorn main:app --reload --port 8000

from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Dict, Optional, List
import uuid
from datetime import datetime, timedelta
import secrets
import hashlib
import base64
from mfos import mfos_service
from mfos.mfos_db import init_mfos_db

# Init db
init_mfos_db()

app = FastAPI(
    title="KYC Backend Service (Mock DigiLocker Trust Source)",
    description="Backend-only KYC flow service. Mocked DigiLocker OAuth + document pull for demo/PoC.",
    version="1.0.0"
)

# ────────────────────────────────────────────────
# Mock "DigiLocker database" – pretend these users exist
# In reality: fetched via /UserProfile + /files/issued + /file/{uri}
# ────────────────────────────────────────────────
MOCK_DIGILOCKER_USERS: Dict[str, dict] = {
    "123456789012": {  # Aadhaar as key for simplicity
        "sub": "dl_user_001",
        "name": "Neelabh Sharma",
        "date_of_birth": "1995-03-14",
        "gender": "M",
        "mobile": "9876543210",
        "email": "neelabh@example.com",
        "address": "Flat 101, Marine Drive, Mumbai, Maharashtra 400020",
        "photo": "https://example.com/mock-photo.jpg",  # base64 or URL in real
        "documents": {
            "AADHAAR": {
                "uri": "in.gov.uidai.aadhaar.eKYC.XXXX-XXXX-XXXX-1234",
                "type": "XML+PDF",
                "issued_by": "UIDAI",
                "issue_date": "2023-05-10",
                "data": {
                    "name": "Neelabh Sharma",
                    "dob": "1995-03-14",
                    "gender": "M",
                    "address": "Mumbai, Maharashtra",
                    "photo": "(base64 mocked)"
                }
            },
            "PAN": {
                "uri": "in.gov.incometax.pan.ABCDE1234F",
                "type": "PDF",
                "issued_by": "Income Tax Department",
                "issue_date": "2018-11-05",
                "data": {
                    "pan": "ABCDE1234F",
                    "name": "Neelabh Sharma",
                    "father_name": "Mr. Sharma Sr.",
                    "dob": "1995-03-14"
                }
            }
        }
    }
}

# In-memory session store (use Redis/DB in production)
sessions: Dict[str, dict] = {}          # session_id → session data
auth_codes: Dict[str, dict] = {}        # code → session_id + verifier
access_tokens: Dict[str, dict] = {}     # token → session + expiry

# ────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────

class KYCInitiateRequest(BaseModel):
    user_id: str = Field(..., description="Your internal user identifier")
    aadhaar: str = Field(..., min_length=12, max_length=12, description="Aadhaar number")
    mobile: Optional[str] = None
    redirect_uri: str = Field(..., description="Your frontend callback URL (for real flow)")

class KYCInitiateResponse(BaseModel):
    session_id: str
    auth_url: str
    message: str

class ConsentCallback模拟(BaseModel):  # For demo – in real life this is GET from DigiLocker
    session_id: str
    code: str
    state: str

class TokenExchangeRequest(BaseModel):
    session_id: str
    code: str
    code_verifier: str   # for PKCE

class KYCResult(BaseModel):
    session_id: str
    status: str                # initiated / consented / fetched / approved / rejected
    kyc_approved: bool = False
    verified_data: Optional[dict] = None
    message: str

# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def generate_pkce_pair():
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge

def create_mock_auth_url(session_id: str, redirect_uri: str, state: str):
    verifier, challenge = generate_pkce_pair()
    auth_codes[session_id] = {"verifier": verifier}  # pretend DigiLocker stores this

    # Real URL would be:
    # https://api.digitallocker.gov.in/public/oauth2/1/authorize?response_type=code&client_id=xxx&redirect_uri=...&scope=openid+profile+...&state=...&code_challenge=...&code_challenge_method=S256
    mock_url = (
        f"http://localhost:8000/mock-digilocker-authorize?"
        f"session_id={session_id}&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"code_challenge={challenge}"
    )
    return mock_url

# ────────────────────────────────────────────────
# Endpoints – Backend Service API
# ────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "KYC Backend Service (Mock DigiLocker)",
        "docs": "/docs",
        "typical_flow": [
            "1. POST /kyc/initiate",
            "2. Redirect user to auth_url (frontend responsibility)",
            "3. User consents → DigiLocker redirects to your redirect_uri with ?code=xxx&state=yyy",
            "4. POST /kyc/token-exchange (exchange code → token)",
            "5. GET /kyc/result/{session_id} or webhook"
        ]
    }

@app.post("/kyc/initiate", response_model=KYCInitiateResponse)
async def initiate_kyc(req: KYCInitiateRequest):
    """Step 1: Merchant/app starts KYC for a user"""
    if req.aadhaar not in MOCK_DIGILOCKER_USERS:
        raise HTTPException(404, detail="Mock DigiLocker user not found. Use 123456789012 for testing.")

    session_id = str(uuid.uuid4())
    state = secrets.token_urlsafe(16)

    sessions[session_id] = {
        "user_id": req.user_id,
        "aadhaar": req.aadhaar,
        "status": "initiated",
        "created_at": datetime.utcnow(),
        "state": state,
        "redirect_uri": req.redirect_uri,
    }

    auth_url = create_mock_auth_url(session_id, req.redirect_uri, state)

    return {
        "session_id": session_id,
        "auth_url": auth_url,
        "message": "Send user to auth_url (frontend job). After consent → callback to your redirect_uri with ?code & state"
    }

@app.get("/mock-digilocker-authorize")
async def mock_digilocker_authorize(
    session_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(...),
    code_challenge: str = Query(...)
):
    """Mock DigiLocker consent screen → pretend user clicked 'Allow'"""
    if session_id not in sessions:
        raise HTTPException(400, "Invalid session")

    # Pretend user approved
    code = secrets.token_urlsafe(24)
    auth_codes[code] = {"session_id": session_id, "verifier": "mock-verifier-for-demo"}

    # In real life → redirect to merchant redirect_uri
    # Here we just return what would be in query string
    return {
        "message": "Mock consent approved. In real flow DigiLocker would redirect to your redirect_uri with these params:",
        "redirect_to": f"{redirect_uri}?code={code}&state={state}",
        "code_for_testing": code,
        "session_id": session_id
    }

@app.post("/kyc/token-exchange")
async def exchange_code_for_token(body: TokenExchangeRequest):
    """Step 4: Exchange authorization code for access token (mocked)"""
    if body.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[body.session_id]

    if session["status"] != "initiated":
        raise HTTPException(400, "Session already processed")

    # In real: POST https://api.digitallocker.gov.in/public/oauth2/1/token
    # with code, grant_type=authorization_code, redirect_uri, client_id, client_secret, code_verifier

    access_token = f"mock_at_{secrets.token_hex(16)}"
    refresh_token = f"mock_rt_{secrets.token_hex(16)}"

    expiry = datetime.utcnow() + timedelta(hours=1)

    access_tokens[access_token] = {
        "session_id": body.session_id,
        "expires_at": expiry,
        "scope": "openid profile digilocker"
    }

    session["status"] = "consented"
    session["access_token"] = access_token  # pretend we store it briefly

    # Simulate fetch documents right after token (common pattern)
    aadhaar = session["aadhaar"]
    user_data = MOCK_DIGILOCKER_USERS[aadhaar]

    session["status"] = "fetched"
    session["verified_data"] = user_data
    session["kyc_approved"] = True  # simplistic – in prod add rules, liveness, etc.

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": refresh_token,
        "kyc_status": "APPROVED",
        "verified_name": user_data["name"],
        "message": "Token exchanged. Documents pulled (mocked)."
    }

@app.get("/kyc/result/{session_id}", response_model=KYCResult)
async def get_kyc_result(session_id: str):
    """Query current KYC status & data"""
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")

    s = sessions[session_id]
    status = s.get("status", "unknown")

    return {
        "session_id": session_id,
        "status": status,
        "kyc_approved": s.get("kyc_approved", False),
        "verified_data": s.get("verified_data"),
        "message": f"KYC flow status: {status}"
    }

@app.post("/webhook/pine-labs")
async def pine_labs_webhook(event: dict = Body(...)):
    """Webhook for Pine Labs to report payment successes, failures, etc."""
    result = mfos_service.handle_pine_labs_event(event)
    # The webhook endpoint usually just returns 200 OK + JSON
    return result

@app.post("/kyc/callback-simulate", response_model=KYCResult)
async def simulate_callback(body: ConsentCallback模拟):
    """For quick testing: pretend you received redirect from DigiLocker"""
    # In real app → this logic lives in your /callback GET endpoint
    if body.state != sessions.get(body.session_id, {}).get("state"):
        raise HTTPException(400, "State mismatch – possible CSRF")

    # Jump to token exchange simulation
    token_req = TokenExchangeRequest(
        session_id=body.session_id,
        code=body.code,
        code_verifier="mock-verifier-for-demo"
    )
    return await exchange_code_for_token(token_req)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)