import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parent
AR_SERVER = ROOT / "server.py"
ECOM_SERVER = ROOT / "ecommerce" / "backend" / "mcp_server.py"


async def call_text_tool(session: ClientSession, tool_name: str, arguments: dict) -> str:
    result = await session.call_tool(tool_name, arguments=arguments)
    if not result.content:
        raise RuntimeError(f"No content returned from {tool_name}")
    text = getattr(result.content[0], "text", None)
    if text is None:
        raise RuntimeError(f"No text returned from {tool_name}: {result.content!r}")
    return text


async def call_json_tool(session: ClientSession, tool_name: str, arguments: dict) -> dict:
    text = await call_text_tool(session, tool_name, arguments)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{tool_name} did not return JSON: {text}") from exc


def extract_first_product_id(search_text: str) -> int:
    match = re.search(r"- ID: (\d+)", search_text)
    if not match:
        raise RuntimeError(f"Could not extract a product ID from search output:\n{search_text}")
    return int(match.group(1))


async def run() -> None:
    ar_params = StdioServerParameters(
        command=sys.executable,
        args=[str(AR_SERVER)],
    )
    ecommerce_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ECOM_SERVER)],
    )

    async with stdio_client(ar_params) as (ar_read, ar_write):
        async with ClientSession(ar_read, ar_write) as ar_session:
            await ar_session.initialize()

            email = f"buyer_{uuid.uuid4().hex[:8]}@test.com"
            register_user = await call_json_tool(
                ar_session,
                "register_user",
                {"phone": "9876543210", "full_name": "Test Buyer", "email": email},
            )
            user_id = register_user["user"]["user_id"]
            print(f"[PASS] register_user -> {user_id}")

            initiate_kyc = await call_json_tool(
                ar_session,
                "initiate_kyc",
                {"user_id": user_id},
            )
            session_id = initiate_kyc["session_id"]
            print(f"[PASS] initiate_kyc -> {session_id}")

            confirm_kyc = await call_json_tool(
                ar_session,
                "confirm_kyc_otp",
                {"user_id": user_id, "session_id": session_id, "otp": "000000"},
            )
            print(f"[PASS] confirm_kyc_otp -> {confirm_kyc['kyc_status']}")

            register_agent = await call_json_tool(
                ar_session,
                "register_agent",
                {
                    "user_id": user_id,
                    "agent_name": "Test Shopping Bot",
                    "description": "Client-driven shopping agent",
                    "capabilities_json": json.dumps(["ECOMMERCE_ACCESS", "CHECKOUT"]),
                },
            )
            agent_id = register_agent["agent"]["agent_id"]
            print(f"[PASS] register_agent -> {agent_id}")

            register_service = await call_json_tool(
                ar_session,
                "register_service",
                {
                    "service_name": "solespace",
                    "service_url": "http://localhost:8001",
                    "description": "SoleSpace ecommerce MCP server",
                    "capabilities_json": json.dumps(["ECOMMERCE"]),
                },
            )
            print(
                "[PASS] register_service -> "
                f"{register_service['service']['service_name']}"
            )

            async with stdio_client(ecommerce_params) as (ecom_read, ecom_write):
                async with ClientSession(ecom_read, ecom_write) as ecom_session:
                    await ecom_session.initialize()

                    search_text = await call_text_tool(
                        ecom_session,
                        "search_products",
                        {"query": "running shoes"},
                    )
                    product_id = extract_first_product_id(search_text)
                    print(f"[PASS] search_products -> product_id {product_id}")

                    add_text = await call_text_tool(
                        ecom_session,
                        "add_to_cart",
                        {"agent_id": agent_id, "product_id": product_id, "quantity": 1},
                    )
                    print("[PASS] add_to_cart")
                    print(add_text)

                    checkout_text = await call_text_tool(
                        ecom_session,
                        "checkout_cart",
                        {"agent_id": agent_id},
                    )
                    print("[PASS] checkout_cart")
                    print(checkout_text)


if __name__ == "__main__":
    asyncio.run(run())
