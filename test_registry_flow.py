import json
import os
import uuid
import sys
import time

# We'll import the existing MCPClient from test_flow
try:
    from test_flow import MCPClient
except ImportError:
    print("Error: Could not import MCPClient from test_flow.py")
    sys.exit(1)

def main():
    print("--- Testing Agent Registry Flow ---")
    client = MCPClient(os.environ.get("KYC_MCP_BASE_URL", "http://localhost:8000"))
    
    print("Connecting...")
    client.connect()
    print("Initializing...")
    client.initialize()
    
    # 1. Register User
    unique_email = f"agent_test_{uuid.uuid4().hex[:8]}@example.com"
    print(f"\n[1] Registering User (Email: {unique_email})")
    res = client.call_tool("register_user", {
        "full_name": "Test Agent Sharma",
        "email": unique_email,
        "phone": "9876543210"
    }, req_id=1)
    
    if not res.get("success"):
        print(f"Failed to register user: {res}")
        return
        
    user_id = res["user"]["user_id"]
    print(f"  → Success! User ID: {user_id}")
    
    # 2. Initiate KYC
    print(f"\n[2] Initiating KYC for User {user_id}")
    res = client.call_tool("initiate_kyc", {
        "user_id": user_id,
        "documents_json": json.dumps({
            "AADHAAR": {"aadhaar_number": "999999999999"}, # Rahul Sharma in mock
            "PAN":     {"pan_number": "ABCDE1234F"}        # Rahul Sharma in mock
        })
    }, req_id=2)
    
    if not res.get("success"):
        print(f"Failed to initiate KYC: {res}")
        return
        
    session_id = res["session_id"]
    print(f"  → Success! Session ID: {session_id}")
    
    # 3. Verify and Generate ID
    print(f"\n[3] Calling verify_and_generate_id for Session {session_id}")
    # OTP is fixed to 421596
    res = client.call_tool("verify_and_generate_id", {
        "user_id": user_id,
        "session_id": session_id,
        "otp": "421596"
    }, req_id=3)
    
    print(f"  → Response body: {json.dumps(res, indent=2)}")
    
    if not res.get("success"):
        print(f"Failed to verify KYC: {res}")
        return
        
    agent_id = res.get("agent_id")
    print(f"  → Success! Generated Agent ID: {agent_id}")
    
    if not agent_id or not agent_id.endswith("@pinelabsUPAI"):
         print(f"  → Error: agent_id seems malformed: {agent_id}")
         return
         
    # 4. Fetch the agent ID via get_registered_agent_id
    print(f"\n[4] Calling get_registered_agent_id for User {user_id}")
    res = client.call_tool("get_registered_agent_id", {
        "user_id": user_id
    }, req_id=4)
    
    print(f"  → Response body: {json.dumps(res, indent=2)}")
    
    fetched_agent_id = res.get("agent_id")
    if fetched_agent_id != agent_id:
        print(f"  → Error! Fetched Agent ID ({fetched_agent_id}) does not match generated ({agent_id})")
        return
        
    print(f"  → Success! Fetched Agent matches exactly.")
        
    # 5. Check if fetch_verified_profile returns the agent_id
    print(f"\n[5] Calling fetch_verified_profile for User {user_id}")
    res = client.call_tool("fetch_verified_profile", {
        "user_id": user_id
    }, req_id=5)
    
    if res.get("agent_id") != agent_id:
         print(f"  → Error! fetch_verified_profile did not include correct agent_id: {res.get('agent_id')}")
         return
         
    print(f"  → Success! Profile includes correct Agent ID.")
    
    client.close()
    print("\n--- All tests passed! ---")


if __name__ == "__main__":
    main()
