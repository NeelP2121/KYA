import asyncio
from mcp_server import call_tool

async def run_test():
    print("Testing place_order tool with REAL Pine Labs integration...")
    result = await call_tool("place_order", {"product_id": 1, "quantity": 1})
    print("\nResult:")
    for text_content in result:
        print(text_content.text)

if __name__ == "__main__":
    asyncio.run(run_test())
