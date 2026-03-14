"""
Registry Service Layer.
Handles the generation of unique IDs for verified users and fetching them.
"""

from db import registry as registry_db
from db import database as kyc_db

# Initialize the db table
registry_db.init_registry_db()

def generate_or_get_agent_id(user_dict: dict) -> dict:
    """
    Checks if an agent ID already exists for this user.
    If not, generates a new one and saves it in the registry DB.
    """
    existing_agent = registry_db.get_agent_by_user_id(user_dict["id"])
    if existing_agent:
        return existing_agent
    
    # Create new agent ID
    return registry_db.create_agent(
        user_id=user_dict["id"],
        full_name=user_dict["full_name"],
        email=user_dict["email"],
        phone=user_dict.get("phone")
    )

def get_registered_agent_id(user_id: str) -> dict:
    """
    Tool logic: fetch a user's unique agent ID.
    User must have a VERIFIED KYC status.
    """
    # 1. Ensure user exists
    user = kyc_db.get_user_by_id(user_id)
    if not user:
        return {"success": False, "error": f"User '{user_id}' not found in KYC store."}
        
    # 2. Ensure user is verified
    if user["kyc_status"] != "VERIFIED":
        return {
            "success": False, 
            "error": f"Cannot get agent ID. User KYC status is '{user['kyc_status']}'. Must be 'VERIFIED'."
        }

    # 3. Get the ID from registry
    agent = registry_db.get_agent_by_user_id(user_id)
    if not agent:
        return {
            "success": False, 
            "error": "Agent ID not found for this verified user. Note: The ID should have been generated upon verification."
        }

    return {
        "success": True,
        "agent_id": agent["agent_id"],
        "user_id": agent["user_id"],
        "full_name": agent["full_name"],
        "created_at": agent["created_at"]
    }
