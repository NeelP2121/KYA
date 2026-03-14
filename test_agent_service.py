import os
import tempfile
import unittest
import uuid
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["KYC_DB_PATH"] = str(Path(_TEMP_DIR.name) / "agent_service_test.db")

from db import database as db
import kyc_service as svc


class AgentCapabilityServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def _register_user(self) -> str:
        result = svc.register_user(
            full_name="Rahul Sharma",
            email=f"rahul+{uuid.uuid4().hex[:8]}@example.com",
            phone="9876543210",
        )
        self.assertTrue(result["success"])
        return result["user"]["user_id"]

    def test_unknown_agent_requires_registration(self):
        result = svc.verify_agent_capability(str(uuid.uuid4()))

        self.assertFalse(result["success"])
        self.assertTrue(result["registration_required"])
        self.assertEqual(result["route_decision"], "BLOCK_REGISTER_AGENT")
        self.assertIn("register", result["message"].lower())

    def test_blank_agent_id_requires_registration(self):
        result = svc.verify_agent_capability("   ")

        self.assertFalse(result["success"])
        self.assertTrue(result["registration_required"])
        self.assertEqual(result["route_decision"], "BLOCK_REGISTER_AGENT")

    def test_register_agent_requires_existing_user(self):
        result = svc.register_agent(
            user_id=str(uuid.uuid4()),
            agent_name="Ghost Agent",
        )

        self.assertFalse(result["success"])
        self.assertIn("register_user", result["error"])

    def test_registered_agent_can_route_to_ecommerce(self):
        user_id = self._register_user()
        registration = svc.register_agent(
            user_id=user_id,
            agent_name="Rahul Shopper Agent",
            description="Customer-controlled ecommerce agent",
        )

        self.assertTrue(registration["success"])
        agent_id = registration["agent"]["agent_id"]
        self.assertEqual(
            registration["agent"]["capabilities"],
            ["ECOMMERCE_ACCESS"],
        )

        result = svc.verify_agent_capability(agent_id, "ECOMMERCE_ACCESS")

        self.assertTrue(result["success"])
        self.assertTrue(result["allowed_to_route"])
        self.assertEqual(result["route_decision"], "ALLOW")
        self.assertEqual(result["verified_capability"], "ECOMMERCE_ACCESS")

    def test_missing_capability_blocks_routing(self):
        user_id = self._register_user()
        registration = svc.register_agent(
            user_id=user_id,
            agent_name="Limited Agent",
            capabilities=["ECOMMERCE_ACCESS", "CHECKOUT"],
        )
        self.assertTrue(registration["success"])

        result = svc.verify_agent_capability(
            registration["agent"]["agent_id"],
            "PAY_ORDER",
        )

        self.assertFalse(result["success"])
        self.assertFalse(result["allowed_to_route"])
        self.assertEqual(result["route_decision"], "BLOCK_CAPABILITY_MISSING")
        self.assertEqual(result["requested_capability"], "PAY_ORDER")


if __name__ == "__main__":
    unittest.main()
