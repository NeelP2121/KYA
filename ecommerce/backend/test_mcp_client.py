import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # List tools
            tools = await session.list_tools()
            print(f"Tools available: {[t.name for t in tools.tools]}")
            
            # Test getting products
            print("\nSearching products for 'premium':")
            response = await session.call_tool("search_products", arguments={"query": "premium"})
            print(response.content[0].text)
            
            # Mock testing place_order
            print("\nAttempting to mock-place order on product ID 1:")
            try:
                # We'll assume product id 1 exists since we seeded 150 items
                order_response = await session.call_tool("place_order", arguments={"product_id": 1, "quantity": 1})
                print(order_response.content[0].text)
            except Exception as e:
                print(f"Failed to place order: {e}")

if __name__ == "__main__":
    asyncio.run(main())
