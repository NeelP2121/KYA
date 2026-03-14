import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolRequest
from database import SessionLocal, Product, Order, product_collection

app = Server("ecommerce_mcp_server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available ecommerce tools."""
    return [
        Tool(
            name="search_products",
            description="Search for products using natural language or keywords.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query (e.g., 'red running shoes')."
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_product",
            description="Get details of a specific product by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product."}
                },
                "required": ["product_id"]
            }
        ),
        Tool(
            name="place_order",
            description="Place an order for a product.",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product to order."},
                    "quantity": {"type": "integer", "description": "Quantity to order. Default is 1."}
                },
                "required": ["product_id"]
            }
        ),
        Tool(
            name="get_orders",
            description="Get a list of all your current orders.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="cancel_order",
            description="Cancel an existing order by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "The ID of the order to cancel."}
                },
                "required": ["order_id"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    db = SessionLocal()
    try:
        if name == "search_products":
            query = arguments["query"]
            results = product_collection.query(query_texts=[query], n_results=5)
            
            if not results or not results['ids'] or not results['ids'][0]:
                return [TextContent(type="text", text="No products found matching your query.")]

            product_ids = [int(pid) for pid in results['ids'][0]]
            products = db.query(Product).filter(Product.id.in_(product_ids)).all()
            
            response_text = "Found the following products:\n\n"
            for p in products:
                response_text += f"- ID: {p.id} | Name: {p.name} | Brand: {p.brand} | Price: ${p.price}\n  Description: {p.description}\n\n"
            
            return [TextContent(type="text", text=response_text)]

        elif name == "get_product":
            product_id = arguments["product_id"]
            p = db.query(Product).filter(Product.id == product_id).first()
            if not p:
                return [TextContent(type="text", text=f"Product with ID {product_id} not found.")]
            
            details = f"Product ID: {p.id}\nName: {p.name}\nBrand: {p.brand}\nPrice: ${p.price}\nDescription: {p.description}\nImage URL: {p.image_url}"
            return [TextContent(type="text", text=details)]

        elif name == "place_order":
            product_id = arguments["product_id"]
            quantity = arguments.get("quantity", 1)
            
            p = db.query(Product).filter(Product.id == product_id).first()
            if not p:
                return [TextContent(type="text", text=f"Product with ID {product_id} not found. Cannot place order.")]
                
            order = Order(product_id=product_id, quantity=quantity)
            db.add(order)
            db.commit()
            db.refresh(order)
            
            # --- Live Pine Labs UAT Integration ---
            try:
                import urllib.request
                import json
                import base64
                import uuid

                client_id = "e4212815-589d-4075-839a-c2c9911f0823"
                client_secret = "3f7a7103badf4c518a0c918621b969af"
                
                # 1. Generate Oauth Token
                b64_creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
                token_req = urllib.request.Request(
                    "https://pluraluat.v2.pinepg.in/api/auth/v1/token",
                    headers={
                        "Authorization": f"Basic {b64_creds}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    data=b"grant_type=client_credentials",
                    method="POST"
                )
                
                try:
                    with urllib.request.urlopen(token_req) as response:
                        token_data = json.loads(response.read().decode())
                        access_token = token_data.get("access_token")
                        
                    print(f"Token acquired. Generating order...")
                except urllib.error.HTTPError as e:
                    error_body = e.read().decode()
                    print(f"Token Endpoint Failed: {e.code} - {error_body}")
                    raise e

                # 2. Create Order
                amount_in_paise = int(p.price * quantity * 100)
                merchant_ref = f"sole_ord_{order.id}_{uuid.uuid4().hex[:6]}"
                
                order_payload = {
                    "merchant_data": {
                        "merchant_order_reference": merchant_ref,
                        "merchant_return_url": "http://127.0.0.1:5173"
                    },
                    "payment_data": {
                        "amount": amount_in_paise,
                        "currency_code": "INR"
                    },
                    "product_data": {
                        "product_details": [
                            {
                                "product_code": str(p.id),
                                "product_amount": int(p.price * 100),
                                "product_quantity": quantity
                            }
                        ]
                    }
                }
                
                order_req = urllib.request.Request(
                    "https://pluraluat.v2.pinepg.in/api/v1/orders",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    },
                    data=json.dumps(order_payload).encode("utf-8"),
                    method="POST"
                )
                
                try:
                    with urllib.request.urlopen(order_req) as response:
                        order_response_data = json.loads(response.read().decode())
                        plural_order_id = order_response_data.get("plural_order_id", "NOT_RETURNED")
                        payment_url = order_response_data.get("payment_url", "NOT_RETURNED")
                    
                    return [TextContent(type="text", text=f"Successfully placed order for {quantity}x {p.name}!\n\nLocal DB Order ID: {order.id}\nPine Labs Plural Order ID: {plural_order_id}\nPine Labs Payment URL: {payment_url}\n\nPlease ask the user to finalize their purchase by clicking the payment URL.")]
                except urllib.error.HTTPError as e:
                    error_body = e.read().decode()
                    print(f"Create Order Endpoint Failed: {e.code} - {error_body}")
                    raise e
            
            except Exception as e:
                # If Pine Labs fails, we at least have the local database order saved.
                return [TextContent(type="text", text=f"Successfully placed local order for {quantity}x {p.name} (Order ID: {order.id}), but the Pine Labs Payment API failed to generate a checkout link. Error: {str(e)}")]

        elif name == "get_orders":
            orders = db.query(Order).all()
            if not orders:
                return [TextContent(type="text", text="You have no current orders.")]
                
            response = "Your Current Orders:\n"
            for o in orders:
                response += f"- Order {o.id}: Product ID {o.product_id} (Qty: {o.quantity}) - Status: {o.status}\n"
            return [TextContent(type="text", text=response)]

        elif name == "cancel_order":
            order_id = arguments["order_id"]
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                return [TextContent(type="text", text=f"Order {order_id} not found.")]
            if order.status == "cancelled":
                return [TextContent(type="text", text=f"Order {order_id} is already cancelled.")]
                
            order.status = "cancelled"
            db.commit()
            return [TextContent(type="text", text=f"Successfully cancelled Order {order_id}.")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    finally:
        db.close()

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
