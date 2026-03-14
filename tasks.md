# Agentic Commerce — Implementation Plan & Tasks

---

## Final Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         USER'S MCP CLIENT                                   │
│              (Claude Desktop / Cursor / Any MCP Client)                     │
│                                                                             │
│  Configured MCP Servers:                                                    │
│    1. AR Server (KYC + Agent Registry)  → http://localhost:8000/sse         │
│    2. SoleSpace Ecommerce               → http://localhost:8001/sse         │
│                                                                             │
│  The user's AI agent orchestrates calls to both servers.                    │
│  AR verifies identity. SoleSpace handles shopping + payments.              │
└─────────┬─────────────────────────────────────┬─────────────────────────────┘
          │                                     │
          │  MCP/SSE                            │  MCP/SSE
          ▼                                     ▼
┌──────────────────────┐          ┌─────────────────────────────────────────┐
│   AR MCP Server      │          │     SoleSpace Ecommerce MCP Server     │
│   (Port 8000)        │          │     (Port 8001)                        │
│                      │          │                                         │
│  EXISTING TOOLS:     │          │  EXISTING TOOLS:                       │
│  ✅ register_user    │◄────────►│  ✅ search_products                    │
│  ✅ initiate_kyc     │  Direct  │  ✅ get_product                        │
│  ✅ confirm_kyc_otp  │  DB read │                                         │
│  ✅ verify_and_gen   │          │  NEW TOOLS (Cart):                     │
│  ✅ get_agent_id     │          │  🆕 add_to_cart                        │
│  ✅ check_kyc_status │          │  🆕 view_cart                          │
│  ✅ fetch_profile    │          │  🆕 remove_from_cart                   │
│  ✅ re_verify_kyc    │          │  🆕 update_cart_quantity               │
│  ✅ list_users       │          │                                         │
│  ✅ list_doc_types   │          │  NEW TOOLS (Checkout + Payment):       │
│  ✅ register_agent   │          │  🆕 checkout_cart                      │
│  ✅ verify_agent_cap │          │  🆕 get_payment_status                 │
│                      │          │  🆕 get_orders                         │
│  NEW TOOLS:          │          │  🆕 cancel_order                       │
│  🆕 register_service │          │                                         │
│  🆕 verify_traffic   │          │  INTERNAL (not exposed as MCP tools):  │
│                      │          │  ├─ pinelabs_service.py                │
│  Databases:          │          │  │  ├─ get_access_token()              │
│  ├─ kyc_store.db     │          │  │  ├─ create_checkout_order()         │
│  └─ agent_registry.db│          │  │  └─ get_order_status()              │
│                      │          │  │                                      │
│                      │          │  └─ ar_client.py                        │
│                      │          │     └─ verify_agent() → reads AR DBs   │
│                      │          │                                         │
│                      │          │  Databases:                             │
│                      │          │  ├─ ecommerce.db (products,orders,cart) │
│                      │          │  └─ chroma_db/ (semantic search)        │
└──────────────────────┘          └──────────────┬──────────────────────────┘
                                                 │
                                                 │  HTTPS (server-to-server)
                                                 │  Backend API calls only
                                                 ▼
                                  ┌─────────────────────────────────────────┐
                                  │        Pine Labs UAT API                │
                                  │  (Payment Gateway — Hosted Checkout)    │
                                  │                                         │
                                  │  Token:                                 │
                                  │   POST pluraluat.v2.pinepg.in           │
                                  │        /api/auth/v1/token               │
                                  │                                         │
                                  │  Create Checkout:                       │
                                  │   POST pluraluat.v2.pinepg.in           │
                                  │        /api/checkout/v1/orders          │
                                  │                                         │
                                  │  Get Status:                            │
                                  │   GET  pluraluat.v2.pinepg.in           │
                                  │        /api/checkout/v1/orders/{id}     │
                                  │                                         │
                                  │  Credentials:                           │
                                  │   ID:  e4212815-589d-4075-839a-...     │
                                  │   Key: 3f7a7103badf4c518a0c9186...     │
                                  └─────────────────────────────────────────┘
```

**Key Design Principle:** The ecommerce MCP server is the merchant. It owns ALL Pine Labs integration. The user never touches payment APIs directly — they just say "checkout" and get back a payment link.

---

## End-to-End User Flow

```
PHASE 1 — REGISTRATION (User ↔ AR)
═══════════════════════════════════

User: "Register me as Rahul Sharma, rahul@example.com"
  → AR.register_user(full_name="Rahul Sharma", email="rahul@example.com")
  ← user_id: "abc-123"

User: "Start KYC with Aadhaar 999999999999"
  → AR.initiate_kyc(user_id="abc-123", documents={"AADHAAR": {"aadhaar_number": "999999999999"}})
  ← session_id: "sess-456", "Please confirm OTP"

User: "OTP is 421596"
  → AR.confirm_kyc_otp(user_id="abc-123", session_id="sess-456", otp="421596")
  ← kyc_status: VERIFIED, agent_id: "rahulsharma_agent-565c8f94@pinelabsUPAI"

User: "Register my shopping agent"
  → AR.register_agent(user_id="abc-123", agent_name="Rahul Shopping Bot",
                       capabilities=["ECOMMERCE_ACCESS","CHECKOUT","PAYMENT"])
  ← agent_id: "uuid-789" with capabilities


PHASE 2 — SHOPPING (User ↔ SoleSpace)
══════════════════════════════════════

User: "Show me running shoes under $200"
  → SoleSpace.search_products(query="running shoes under $200")
  ← [Nike Air Max $150, Adidas Ultraboost $180, ...]

User: "Add 2x Nike Air Max (ID 5) to my cart"
  → SoleSpace.add_to_cart(agent_id="uuid-789", product_id=5, quantity=2)
     ↳ SoleSpace internally: ar_client.verify_agent("uuid-789") → ALLOWED
  ← Cart: 2x Nike Air Max = $300.00

User: "Also add the Adidas Ultraboost (ID 12)"
  → SoleSpace.add_to_cart(agent_id="uuid-789", product_id=12, quantity=1)
  ← Cart: 2x Nike Air Max + 1x Adidas Ultraboost = $480.00

User: "Show my cart"
  → SoleSpace.view_cart(agent_id="uuid-789")
  ← Cart with 2 items, total: $480.00


PHASE 3 — CHECKOUT & PAYMENT (User ↔ SoleSpace ↔ Pine Labs)
════════════════════════════════════════════════════════════════

User: "Checkout my cart"
  → SoleSpace.checkout_cart(agent_id="uuid-789")
     ↳ SoleSpace internally:
       1. ar_client.verify_agent("uuid-789", "CHECKOUT") → ALLOWED
       2. Fetch cart items → 2x Nike + 1x Adidas = $480.00
       3. pinelabs_service.get_access_token() → Bearer token
       4. pinelabs_service.create_checkout_order(
            amount=480.00,
            merchant_ref="sole_checkout_a1b2c3d4",
            products=[...],
            callback_url="http://localhost:5173/payment/callback"
          )
       5. Pine Labs returns: { order_id: "PLO_123", redirect_url: "https://..." }
       6. Create Order records in DB with plural_order_id
       7. Mark cart as "checked_out"
  ← "Your order is ready! Total: $480.00
     🔗 Pay here: https://pluraluat...redirect/checkout?token=...

     For UAT testing use:
     Card: 4012 0010 3714 1112 | CVV: 065 | Expiry: any future date"


PHASE 4 — PAYMENT STATUS (User ↔ SoleSpace ↔ Pine Labs)
═════════════════════════════════════════════════════════

User: "What's my payment status?"
  → SoleSpace.get_payment_status(order_id=42)
     ↳ SoleSpace internally:
       1. Look up order in DB → plural_order_id: "PLO_123"
       2. pinelabs_service.get_order_status("PLO_123")
       3. Pine Labs returns: { status: "PROCESSED" }
       4. Update local DB: payment_status = "processed"
  ← "✅ Payment successful! Your order #42 is confirmed.
     Items: 2x Nike Air Max, 1x Adidas Ultraboost
     Total: $480.00 | Status: PROCESSED"
```

---

## Task Groups

---

### TASK 1: Pine Labs Payment Service Module
**File to create:** `ecommerce/backend/pinelabs_service.py`
**Priority:** P0 (Blocker — everything payment-related depends on this)
**Dependencies:** None

#### What to build:
A standalone async module that wraps all Pine Labs UAT API calls. This module is used internally by the ecommerce MCP server and REST API — it is NOT exposed to users.

#### Detailed implementation prompt:

```
Create the file: ecommerce/backend/pinelabs_service.py

This is a standalone Pine Labs payment integration service for UAT environment.
It must use httpx for async HTTP calls (already in requirements.txt).

CREDENTIALS (hardcode for UAT, these are test credentials):
  client_id     = "e4212815-589d-4075-839a-c2c9911f0823"
  client_secret = "3f7a7103badf4c518a0c918621b969af"

UAT BASE URL: https://pluraluat.v2.pinepg.in

Implement these 3 async functions:

1. async def get_access_token() -> str:
   Endpoint: POST {BASE}/api/auth/v1/token
   Auth: Basic auth with base64(client_id:client_secret)
   Body: grant_type=client_credentials (form-urlencoded)
   Content-Type: application/x-www-form-urlencoded

   Returns: access_token string

   IMPORTANT: Cache the token in a module-level variable with its expiry time.
   Only re-fetch when expired (expires_in is 3600 seconds, refresh at 3500).

   Example:
     _cached_token = None
     _token_expiry = 0

   Error handling: Raise a descriptive exception if token acquisition fails.

2. async def create_checkout_order(
       amount_inr: float,
       merchant_reference: str,
       product_details: list[dict],  # [{"name": str, "code": str, "amount": float, "quantity": int}]
       customer_email: str = "",
       customer_name: str = "",
       customer_phone: str = "",
       callback_url: str = "http://localhost:5173/payment/callback",
       failure_callback_url: str = "http://localhost:5173/payment/failure",
   ) -> dict:

   Endpoint: POST {BASE}/api/checkout/v1/orders
   Headers:
     Authorization: Bearer {token}
     Content-Type: application/json
     Accept: application/json
     Request-ID: {uuid4}
     Request-Timestamp: {ISO 8601 with timezone, e.g. 2026-03-14T10:30:00+05:30}

   Body:
   {
     "merchant_order_reference": merchant_reference,   # string, 1-50 chars, alphanumeric + -_
     "order_amount": {
       "value": int(amount_inr * 100),                 # amount in PAISE
       "currency": "INR"
     },
     "pre_auth": false,
     "purchase_details": {
       "customer": {
         "email_id": customer_email,
         "first_name": customer_name.split()[0] if customer_name else "",
         "last_name": " ".join(customer_name.split()[1:]) if customer_name else "",
         "customer_id": merchant_reference,
         "mobile_number": customer_phone
       }
     },
     "callback_url": callback_url,
     "failure_callback_url": failure_callback_url
   }

   Returns dict with:
   {
     "plural_order_id": response["order_id"],
     "redirect_url": response["redirect_url"],
     "token": response.get("token", ""),
     "merchant_reference": merchant_reference
   }

   Error handling:
   - Log the full error response body for debugging
   - Raise ValueError with descriptive message on failure
   - If response doesn't contain redirect_url, raise error

3. async def get_order_status(plural_order_id: str) -> dict:

   Endpoint: GET {BASE}/api/checkout/v1/orders/{plural_order_id}
   Headers:
     Authorization: Bearer {token}
     Accept: application/json
     Request-ID: {uuid4}
     Request-Timestamp: {ISO 8601}

   Returns dict with:
   {
     "plural_order_id": str,
     "status": str,         # CREATED | PROCESSED | FAILED | AUTHORIZED
     "amount": float,       # in INR (convert from paise)
     "merchant_reference": str,
     "raw_response": dict   # full response for debugging
   }

   Error handling: Return {"status": "UNKNOWN", "error": str(e)} on failure.

Add module docstring at top with:
- What this module does
- UAT endpoints
- Test card numbers:
    VISA:       4012 0010 3714 1112, CVV: 065, any future expiry
    MASTERCARD: 5200 0000 0000 1096, CVV: 123, any future expiry

Do NOT use urllib.request. Use httpx.AsyncClient.
Do NOT expose this as MCP tools — it's internal to the ecommerce server.
```

---

### TASK 2: Database Schema Enhancement
**File to modify:** `ecommerce/backend/database.py`
**Priority:** P0 (Blocker — cart and order features need this)
**Dependencies:** None

#### Detailed implementation prompt:

```
Modify the file: ecommerce/backend/database.py

Keep ALL existing code (Product, Order, engine, SessionLocal, ChromaDB setup).
Add the following changes:

1. ADD new Cart model:

class Cart(Base):
    __tablename__ = "carts"
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, index=True, nullable=False)  # AR agent_id
    status = Column(String, default="active")  # active | checked_out | abandoned
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")

2. ADD new CartItem model:

class CartItem(Base):
    __tablename__ = "cart_items"
    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("carts.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    cart = relationship("Cart", back_populates="items")
    product = relationship("Product")

3. ENHANCE existing Order model — add these NEW columns after the existing ones:
   (Keep existing: id, product_id, quantity, status, created_at, product relationship)

    agent_id = Column(String, nullable=True, index=True)
    plural_order_id = Column(String, nullable=True, index=True)
    merchant_reference = Column(String, nullable=True)
    payment_url = Column(String, nullable=True)
    payment_status = Column(String, default="pending")  # pending | processed | failed | created
    total_amount = Column(Float, nullable=True)          # Total in INR for this line item

Do NOT remove or rename any existing columns or models.
The Base.metadata.create_all(bind=engine) at the bottom already handles table creation.

IMPORTANT: You may need to delete the existing ecommerce.db file and re-run
seed_db.py since SQLite doesn't support ALTER TABLE ADD COLUMN well with
SQLAlchemy's create_all. Add a comment about this.
```

---

### TASK 3: AR Client for Ecommerce Server
**File to create:** `ecommerce/backend/ar_client.py`
**Priority:** P0 (Blocker — ecommerce needs to verify agents)
**Dependencies:** None (reads existing AR databases)

#### Detailed implementation prompt:

```
Create the file: ecommerce/backend/ar_client.py

This module lets the SoleSpace ecommerce server verify that incoming
agent_ids are legitimate by directly reading the AR's SQLite databases.

Both servers run on the same machine, so we read the DB files directly
instead of making MCP-over-SSE calls (which would be needlessly complex).

DATABASE PATHS (relative to this file):
  KYC_DB_PATH = os.path.join(os.path.dirname(__file__), "../../kyc_store.db")
  AGENT_REGISTRY_DB_PATH = os.path.join(os.path.dirname(__file__), "../../agent_registry.db")

Implement these functions:

1. def verify_agent(agent_id: str, required_capability: str = "ECOMMERCE_ACCESS") -> dict:
   """
   Verify an agent is registered with AR and has the required capability.

   Steps:
   a) Open kyc_store.db
   b) Query: SELECT * FROM agents WHERE id = ? AND status = 'ACTIVE'
   c) If not found, return {"allowed": False, "reason": "Agent not registered with AR"}
   d) Parse capabilities JSON: json.loads(agent["capabilities"])
   e) Check if required_capability is in the capabilities list
   f) If not, return {"allowed": False, "reason": f"Agent lacks {required_capability} capability"}
   g) Get user info: SELECT full_name, email, phone FROM users WHERE id = agent["user_id"]
   h) Return:
      {
        "allowed": True,
        "agent_id": agent_id,
        "agent_name": agent["agent_name"],
        "user_id": agent["user_id"],
        "user_name": user["full_name"],
        "user_email": user["email"],
        "user_phone": user.get("phone", ""),
        "capabilities": capabilities_list
      }
   """

2. def get_agent_user_info(agent_id: str) -> dict | None:
   """
   Get the user profile associated with an agent_id.
   Used to populate customer details in Pine Labs checkout.

   Returns: {"full_name": str, "email": str, "phone": str} or None
   """

Use sqlite3 directly (not SQLAlchemy) since we're reading another server's DB.
Set row_factory = sqlite3.Row for dict-like access.
Handle FileNotFoundError gracefully — if AR DB doesn't exist, return
{"allowed": False, "reason": "AR database not available"}.
```

---

### TASK 4: Ecommerce MCP Server Rewrite
**File to modify:** `ecommerce/backend/mcp_server.py`
**Priority:** P0 (Core deliverable)
**Dependencies:** Tasks 1, 2, 3

#### Detailed implementation prompt:

```
REWRITE the file: ecommerce/backend/mcp_server.py

This is the main SoleSpace ecommerce MCP server. Currently it uses STDIO
transport and the low-level Server() class. Rewrite it to:
  - Use FastMCP (like the AR server does)
  - Use SSE transport on port 8001
  - Add cart management tools
  - Add checkout with Pine Labs integration
  - Add payment status checking
  - Verify agents with AR before cart/checkout operations

IMPORTS:
  import os, json, uuid, asyncio
  from mcp.server.fastmcp import FastMCP
  from database import SessionLocal, Product, Order, Cart, CartItem, product_collection
  import pinelabs_service
  import ar_client

SERVER SETUP:
  mcp = FastMCP(
      name="solespace-ecommerce",
      instructions=(
          "SoleSpace — Premium sneaker store. "
          "Browse and search 150+ sneakers, manage your shopping cart, "
          "and checkout with secure Pine Labs payments. "
          "Flow: search_products → add_to_cart → view_cart → checkout_cart → get_payment_status. "
          "All cart and checkout operations require a valid agent_id from the AR server."
      ),
      host="0.0.0.0",
      port=8001,
  )

TOOLS TO IMPLEMENT (use @mcp.tool() decorator like the AR server does):

─────────────────────────────────────────────
TOOL 1: search_products(query: str) -> str
─────────────────────────────────────────────
Keep existing logic: ChromaDB semantic search, return product list.
No auth required (browsing is open).
Return formatted text with ID, name, brand, price for each match.
Use n_results=10.

─────────────────────────────────────────────
TOOL 2: get_product(product_id: int) -> str
─────────────────────────────────────────────
Keep existing logic: fetch by ID from SQLite.
No auth required.

─────────────────────────────────────────────
TOOL 3: add_to_cart(agent_id: str, product_id: int, quantity: int = 1) -> str
─────────────────────────────────────────────
Steps:
  a) Call ar_client.verify_agent(agent_id, "ECOMMERCE_ACCESS")
     If not allowed, return error with message from AR
  b) Validate product exists
  c) Find active cart: db.query(Cart).filter(Cart.agent_id == agent_id, Cart.status == "active").first()
  d) If no active cart, create one: Cart(agent_id=agent_id)
  e) Check if product already in cart:
     existing = db.query(CartItem).filter(CartItem.cart_id == cart.id, CartItem.product_id == product_id).first()
     If yes, increment quantity: existing.quantity += quantity
     If no, create new CartItem
  f) Return cart summary showing all items with subtotals and total

  Return format:
  "Added {quantity}x {product.name} to cart.

  🛒 Your Cart:
  1. {name} — {qty}x @ ${price} = ${subtotal}
  2. {name} — {qty}x @ ${price} = ${subtotal}
  ─────────────────────
  Total: ${total}"

─────────────────────────────────────────────
TOOL 4: view_cart(agent_id: str) -> str
─────────────────────────────────────────────
  a) Find active cart for agent_id
  b) If no cart or empty, return "Your cart is empty"
  c) List all items with product details, quantities, subtotals
  d) Show cart total

  Same format as add_to_cart response.

─────────────────────────────────────────────
TOOL 5: remove_from_cart(agent_id: str, product_id: int) -> str
─────────────────────────────────────────────
  a) Find active cart
  b) Find CartItem with matching product_id
  c) Delete it
  d) Return updated cart summary

─────────────────────────────────────────────
TOOL 6: update_cart_quantity(agent_id: str, product_id: int, quantity: int) -> str
─────────────────────────────────────────────
  a) If quantity <= 0, remove item from cart
  b) Otherwise update the CartItem quantity
  c) Return updated cart summary

─────────────────────────────────────────────
TOOL 7: checkout_cart(agent_id: str) -> str
─────────────────────────────────────────────
THIS IS THE MAIN PAYMENT TOOL. Steps:

  a) Verify agent with AR:
     result = ar_client.verify_agent(agent_id, "CHECKOUT")
     If blocked, return error. Tell user they need CHECKOUT capability.

  b) Get active cart:
     cart = db.query(Cart).filter(Cart.agent_id == agent_id, Cart.status == "active").first()
     If no cart or empty, return "Your cart is empty. Add items first."

  c) Calculate total:
     total_inr = 0
     product_details = []
     for item in cart.items:
         product = item.product
         subtotal = product.price * item.quantity
         total_inr += subtotal
         product_details.append({
             "name": product.name,
             "code": str(product.id),
             "amount": product.price,
             "quantity": item.quantity
         })

  d) Get customer info from AR:
     user_info = ar_client.get_agent_user_info(agent_id)

  e) Generate merchant reference:
     merchant_ref = f"sole_{uuid.uuid4().hex[:12]}"

  f) Call Pine Labs to create checkout order:
     payment = await pinelabs_service.create_checkout_order(
         amount_inr=total_inr,
         merchant_reference=merchant_ref,
         product_details=product_details,
         customer_email=user_info.get("email", "") if user_info else "",
         customer_name=user_info.get("full_name", "") if user_info else "",
         customer_phone=user_info.get("phone", "") if user_info else "",
     )

  g) Create Order records in DB:
     For each cart item, create an Order:
       Order(
         product_id=item.product_id,
         quantity=item.quantity,
         status="pending",
         agent_id=agent_id,
         plural_order_id=payment["plural_order_id"],
         merchant_reference=merchant_ref,
         payment_url=payment["redirect_url"],
         payment_status="created",
         total_amount=product.price * item.quantity,
       )

  h) Mark cart as checked_out:
     cart.status = "checked_out"

  i) Return response:
     "🧾 Order Created Successfully!

     Order Reference: {merchant_ref}
     Items:
     1. {qty}x {name} — ${subtotal}
     2. {qty}x {name} — ${subtotal}
     ─────────────────────
     Total: ${total_inr:.2f}

     💳 Complete your payment here:
     {payment['redirect_url']}

     🧪 UAT Test Card Details:
     Card Number: 4012 0010 3714 1112
     CVV: 065
     Expiry: Any future date (e.g., 12/2028)

     After payment, ask me 'What is my payment status?' to check."

  Error handling:
  - If Pine Labs fails, still create orders with payment_status="payment_failed"
  - Return: "Order created locally but payment link generation failed: {error}.
    You can try again with checkout_cart."

─────────────────────────────────────────────
TOOL 8: get_payment_status(agent_id: str, merchant_reference: str = "") -> str
─────────────────────────────────────────────
  a) Find orders for this agent_id.
     If merchant_reference provided, filter by that too.
     Otherwise get the most recent order group (same plural_order_id).

  b) Get the plural_order_id from the order record.

  c) Call Pine Labs:
     status = await pinelabs_service.get_order_status(plural_order_id)

  d) Update all orders with this plural_order_id:
     order.payment_status = status["status"].lower()
     If status is "PROCESSED": order.status = "confirmed"
     If status is "FAILED": order.status = "payment_failed"

  e) Return based on status:
     PROCESSED → "✅ Payment Successful!
       Order Reference: {ref}
       Amount: ${amount}
       Status: CONFIRMED
       Your items will be shipped soon!"

     FAILED → "❌ Payment Failed.
       Order Reference: {ref}
       Please try checking out again."

     CREATED → "⏳ Payment Pending.
       Order Reference: {ref}
       Payment link: {url}
       Please complete your payment to confirm the order."

     UNKNOWN → "Could not fetch payment status. Please try again in a moment."

─────────────────────────────────────────────
TOOL 9: get_orders(agent_id: str) -> str
─────────────────────────────────────────────
  a) Query all orders for agent_id, ordered by created_at DESC
  b) Group by merchant_reference
  c) For each order group, show:
     - Merchant reference
     - Items with quantities
     - Total amount
     - Payment status
     - Order status

─────────────────────────────────────────────
TOOL 10: cancel_order(agent_id: str, order_id: int) -> str
─────────────────────────────────────────────
  a) Find order, verify it belongs to this agent_id
  b) Only allow cancellation if payment_status is "pending" or "created"
  c) Set status = "cancelled"
  d) Return confirmation

─────────────────────────────────────────────
ENTRY POINT:
─────────────────────────────────────────────

if __name__ == "__main__":
    print("🛒 SoleSpace Ecommerce MCP Server starting on http://0.0.0.0:8001")
    print("   SSE endpoint: http://0.0.0.0:8001/sse")
    print("   Tools: 10 tools registered")
    print("   Payment: Pine Labs UAT")
    mcp.run(transport="sse")

IMPORTANT NOTES:
- Use @mcp.tool() decorator (FastMCP style), NOT the low-level Server/list_tools pattern
- Each tool function should have proper docstrings (these become tool descriptions)
- For async Pine Labs calls inside sync tool functions, use:
    import asyncio
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(pinelabs_service.create_checkout_order(...))
  OR make the tool functions async if FastMCP supports it.
- Always close DB sessions in a try/finally block
- Return strings (not TextContent) — FastMCP handles the wrapping
```

---

### TASK 5: AR Server Enhancement — Service Registration & Traffic Verification
**Files to modify:** `server.py`, `kyc_service.py`, `db/database.py`
**Priority:** P1
**Dependencies:** None (can run in parallel with Tasks 1-3)

#### Detailed implementation prompt:

```
This task adds 2 new tools to the AR MCP server so that:
1. Ecommerce services can register themselves with AR
2. Ecommerce can verify incoming traffic through AR

──────────────────────────────────
STEP 1: Add DB table in db/database.py
──────────────────────────────────

Add this table to the init_db() function's CREATE TABLE block:

    CREATE TABLE IF NOT EXISTS registered_services (
        id              TEXT PRIMARY KEY,
        service_name    TEXT UNIQUE NOT NULL,
        service_url     TEXT NOT NULL,
        description     TEXT DEFAULT '',
        capabilities    TEXT NOT NULL DEFAULT '[]',
        api_key         TEXT,
        status          TEXT NOT NULL DEFAULT 'ACTIVE',
        registered_at   TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_services_name ON registered_services(service_name);

Add these functions to db/database.py:

def register_service(service_name, service_url, description, capabilities, api_key=None):
    sid = new_id()
    ts = now_iso()
    with get_connection() as conn:
        # Check if service already exists
        existing = conn.execute(
            "SELECT * FROM registered_services WHERE service_name = ?",
            (service_name,)
        ).fetchone()
        if existing:
            return dict(existing)  # Return existing registration
        conn.execute(
            "INSERT INTO registered_services "
            "(id, service_name, service_url, description, capabilities, api_key, status, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)",
            (sid, service_name, service_url, description, json.dumps(capabilities), api_key, ts)
        )
    return get_service_by_id(sid)

def get_service_by_id(service_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM registered_services WHERE id = ?", (service_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["capabilities"] = json.loads(d["capabilities"]) if d["capabilities"] else []
    return d

def get_service_by_name(service_name):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM registered_services WHERE service_name = ?", (service_name,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["capabilities"] = json.loads(d["capabilities"]) if d["capabilities"] else []
    return d

──────────────────────────────────
STEP 2: Add service functions in kyc_service.py
──────────────────────────────────

def register_service(service_name: str, service_url: str, description: str = "",
                     capabilities: list[str] | None = None) -> dict:
    """Register an ecommerce or other service with AR."""
    service_name = service_name.strip()
    service_url = service_url.strip()
    if not service_name:
        return _err("service_name is required.")
    if not service_url:
        return _err("service_url is required.")

    caps = capabilities or ["ECOMMERCE"]
    service = kyc_db.register_service(service_name, service_url, description, caps)
    kyc_db.audit("SERVICE_REGISTERED", detail={"service_name": service_name, "service_url": service_url})

    return {
        "success": True,
        "message": f"Service '{service_name}' registered with AR.",
        "service": {
            "service_id": service["id"],
            "service_name": service["service_name"],
            "service_url": service["service_url"],
            "capabilities": service["capabilities"],
            "status": service["status"],
        }
    }

def verify_traffic(agent_id: str, service_name: str) -> dict:
    """
    Verify that an agent is allowed to access a specific registered service.
    Checks: agent exists, is active, has matching capability.
    """
    agent_id = agent_id.strip()
    service_name = service_name.strip()

    if not agent_id:
        return {"success": False, "allowed": False, "reason": "agent_id is required"}
    if not service_name:
        return {"success": False, "allowed": False, "reason": "service_name is required"}

    # Check service exists
    service = kyc_db.get_service_by_name(service_name)
    if not service:
        return {"success": False, "allowed": False, "reason": f"Service '{service_name}' not registered with AR"}

    # Check agent exists and is active
    agent = kyc_db.get_agent_by_id(agent_id)
    if not agent:
        return {"success": False, "allowed": False, "reason": "Agent not registered with AR"}
    if agent["status"] != "ACTIVE":
        return {"success": False, "allowed": False, "reason": "Agent is not active"}

    # Check capability match — agent needs ECOMMERCE_ACCESS for ecommerce services
    required_cap = "ECOMMERCE_ACCESS"
    if required_cap not in agent["capabilities"]:
        return {"success": False, "allowed": False,
                "reason": f"Agent lacks {required_cap} capability"}

    kyc_db.audit("TRAFFIC_VERIFIED", user_id=agent["user_id"],
                 detail={"agent_id": agent_id, "service": service_name, "decision": "ALLOW"})

    return {
        "success": True,
        "allowed": True,
        "agent_id": agent_id,
        "agent_name": agent["agent_name"],
        "service_name": service_name,
        "message": f"Agent verified for {service_name}."
    }

──────────────────────────────────
STEP 3: Add MCP tools in server.py
──────────────────────────────────

Add these 2 new tools (after the existing verify_agent_capability tool):

@mcp.tool()
def register_service(
    service_name: str,
    service_url: str,
    description: str = "",
    capabilities_json: str = "",
) -> str:
    """
    Register an ecommerce or other service with AR for traffic verification.

    Args:
        service_name:      Unique name (e.g. "solespace", "payment-gateway").
        service_url:       Base URL of the service (e.g. "http://localhost:8001").
        description:       What this service does.
        capabilities_json: JSON array of capabilities offered, e.g. '["ECOMMERCE"]'

    Returns:
        JSON with service registration details.
    """
    capabilities = None
    if capabilities_json.strip():
        try:
            capabilities = json.loads(capabilities_json)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid capabilities_json: {e}"})
    result = svc.register_service(service_name, service_url, description, capabilities)
    return json.dumps(result, indent=2)

@mcp.tool()
def verify_traffic(agent_id: str, service_name: str) -> str:
    """
    Verify that an agent's traffic is legitimate before allowing access to a service.
    Called by ecommerce or other services to gate incoming requests.

    Args:
        agent_id:     The agent_id to verify.
        service_name: The service the agent is trying to access (e.g. "solespace").

    Returns:
        JSON with allow/block decision.
    """
    result = svc.verify_traffic(agent_id, service_name)
    return json.dumps(result, indent=2)

Update the MCP instructions string to mention the new tools.
Update the print statement at bottom to say "12 tools registered".
```

---

### TASK 6: Update REST API
**File to modify:** `ecommerce/backend/main.py`
**Priority:** P2 (Nice to have — MCP is the primary interface)
**Dependencies:** Tasks 1, 2

#### Detailed implementation prompt:

```
Modify: ecommerce/backend/main.py

Refactor the /api/checkout endpoint to use pinelabs_service.py instead of
the inline urllib code. Also add a payment status endpoint.

Changes:

1. Replace the import block in the checkout function.
   Remove: urllib.request, json inline imports, base64, ssl
   Add at top of file:
     import asyncio
     from pinelabs_service import create_checkout_order, get_order_status, get_access_token

2. Refactor /api/checkout endpoint:
   - Use pinelabs_service.create_checkout_order() instead of inline urllib code
   - Since FastAPI supports async, make the endpoint async:
     @app.post("/api/checkout", response_model=CheckoutResponse)
     async def create_checkout_session(request: CheckoutRequest, db: Session = Depends(get_db)):
   - Call: payment = await create_checkout_order(amount, merchant_ref, products)
   - Use payment["redirect_url"] for the response

3. Add new endpoint:
   @app.get("/api/payment-status/{plural_order_id}")
   async def payment_status(plural_order_id: str):
       status = await get_order_status(plural_order_id)
       return status

4. Add cart REST endpoints (for frontend if needed later):
   - POST /api/cart/{agent_id}/add  (body: {product_id, quantity})
   - GET  /api/cart/{agent_id}
   - DELETE /api/cart/{agent_id}/item/{product_id}

5. Update the CheckoutResponse model to include:
   class CheckoutResponse(BaseModel):
       redirect_url: str
       order_ids: List[int]
       merchant_reference: str
       plural_order_id: str
       total_amount: float

Import Cart, CartItem from database.
```

---

### TASK 7: End-to-End Test Script
**File to create:** `test_e2e_flow.py` (in project root)
**Priority:** P1
**Dependencies:** Tasks 1-5

#### Detailed implementation prompt:

```
Create: test_e2e_flow.py

An end-to-end test that exercises the full user journey by making
MCP calls via HTTP/SSE to both the AR and SoleSpace servers.

You can reference the existing test_flow.py for the MCP client pattern
(it uses httpx to connect to SSE, gets session URL, then sends JSON-RPC).

Test flow:

1. SETUP: Ensure both servers are running
   - AR on http://localhost:8000/sse
   - SoleSpace on http://localhost:8001/sse

2. Register user with AR:
   call_tool("register_user", {"full_name": "Test Buyer", "email": f"buyer_{uuid4().hex[:6]}@test.com"})
   Extract user_id from response

3. Initiate KYC:
   call_tool("initiate_kyc", {"user_id": user_id, "documents_json": '{"AADHAAR": {"aadhaar_number": "999999999999"}}'})
   Extract session_id

4. Confirm OTP:
   call_tool("confirm_kyc_otp", {"user_id": user_id, "session_id": session_id, "otp": "421596"})
   Verify kyc_status == "VERIFIED"

5. Register agent:
   call_tool("register_agent", {"user_id": user_id, "agent_name": "Test Shopping Bot",
             "capabilities_json": '["ECOMMERCE_ACCESS", "CHECKOUT", "PAYMENT"]'})
   Extract agent_id

6. Register service with AR:
   call_tool("register_service", {"service_name": "solespace", "service_url": "http://localhost:8001"})

7. Switch to SoleSpace MCP, search products:
   call_tool("search_products", {"query": "running shoes"}, server="solespace")
   Extract first product_id

8. Add to cart:
   call_tool("add_to_cart", {"agent_id": agent_id, "product_id": product_id, "quantity": 2})

9. View cart:
   call_tool("view_cart", {"agent_id": agent_id})

10. Checkout:
    call_tool("checkout_cart", {"agent_id": agent_id})
    Extract payment_url from response — verify it starts with https://

11. Check payment status:
    call_tool("get_payment_status", {"agent_id": agent_id})
    Verify status is "CREATED" (payment not yet made)

Print clear PASS/FAIL for each step.
Print the payment URL at the end so a human can test it manually.

Use the MCPClient class pattern from test_flow.py but parameterize
the server URL so we can talk to both AR and SoleSpace.
```

---

### TASK 8: Update README
**File to modify:** `README.md`
**Priority:** P1
**Dependencies:** All other tasks (do this last)

#### Detailed implementation prompt:

```
Update: README.md

Restructure the README to cover the full Agentic Commerce system.
Keep it practical and concise.

Sections:

1. # Agentic Commerce Platform
   One-paragraph overview: 3 components (AR, SoleSpace, Pine Labs),
   what the system does.

2. ## Architecture
   Include the ASCII architecture diagram from the top of this tasks.md file.

3. ## Quick Start
   Step-by-step to get everything running:

   a) Install dependencies:
      pip install -r requirements.txt
      cd ecommerce/backend && pip install -r requirements.txt  (if separate)

   b) Seed the product database:
      cd ecommerce/backend && python seed_db.py

   c) Start AR server (terminal 1):
      python server.py
      → Running on http://localhost:8000/sse

   d) Start SoleSpace server (terminal 2):
      cd ecommerce/backend && python mcp_server.py
      → Running on http://localhost:8001/sse

   e) Configure your MCP client:
      {
        "mcpServers": {
          "ar-kyc-server": {
            "url": "http://localhost:8000/sse"
          },
          "solespace-ecommerce": {
            "url": "http://localhost:8001/sse"
          }
        }
      }

4. ## User Flow
   The end-to-end flow diagram from this file.

5. ## MCP Tools Reference
   Two tables listing all tools for each server.

6. ## Pine Labs Payment Integration
   - UAT credentials (test only)
   - Test card numbers
   - API endpoints used
   - How checkout works (the ecommerce server handles everything)

7. ## Test Data
   - KYC: Aadhaar 999999999999, PAN ABCDE1234F, OTP 421596
   - Payment: VISA 4012001037141112, CVV 065
   - Products: 150 seeded sneakers

8. ## Running Tests
   python test_e2e_flow.py

DO NOT include:
- Internal implementation details
- Database schemas
- Code snippets longer than 5 lines
```

---

## Execution Order & Parallelism

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  PHASE 1 — Run in PARALLEL (no dependencies between them):     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │   TASK 1    │  │   TASK 2    │  │   TASK 3    │            │
│  │  PineLabs   │  │  Database   │  │  AR Client  │            │
│  │  Service    │  │  Schema     │  │             │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│                                                                 │
│  Also parallel:                                                 │
│  ┌─────────────┐                                               │
│  │   TASK 5    │                                               │
│  │  AR Server  │                                               │
│  │  Enhancement│                                               │
│  └─────────────┘                                               │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  PHASE 2 — Depends on Phase 1:                                 │
│  ┌─────────────────────────────────────────────┐               │
│  │              TASK 4                          │               │
│  │    Ecommerce MCP Server Rewrite             │               │
│  │    (needs Tasks 1 + 2 + 3)                  │               │
│  └─────────────────────────────────────────────┘               │
│                                                                 │
│  Also Phase 2:                                                 │
│  ┌─────────────┐                                               │
│  │   TASK 6    │                                               │
│  │  REST API   │  (needs Tasks 1 + 2)                          │
│  └─────────────┘                                               │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  PHASE 3 — Depends on Phase 2:                                 │
│  ┌─────────────┐  ┌─────────────┐                              │
│  │   TASK 7    │  │   TASK 8    │                              │
│  │  E2E Tests  │  │  README     │                              │
│  └─────────────┘  └─────────────┘                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pine Labs UAT Quick Reference

| Item | Value |
|------|-------|
| Client ID | `e4212815-589d-4075-839a-c2c9911f0823` |
| Client Secret | `3f7a7103badf4c518a0c918621b969af` |
| Token Endpoint | `POST https://pluraluat.v2.pinepg.in/api/auth/v1/token` |
| Checkout Endpoint | `POST https://pluraluat.v2.pinepg.in/api/checkout/v1/orders` |
| Status Endpoint | `GET https://pluraluat.v2.pinepg.in/api/checkout/v1/orders/{id}` |
| Test VISA | `4012 0010 3714 1112`, CVV `065`, any future expiry |
| Test Mastercard | `5200 0000 0000 1096`, CVV `123`, any future expiry |

## Files Changed/Created Summary

| File | Action | Task |
|------|--------|------|
| `ecommerce/backend/pinelabs_service.py` | CREATE | Task 1 |
| `ecommerce/backend/database.py` | MODIFY | Task 2 |
| `ecommerce/backend/ar_client.py` | CREATE | Task 3 |
| `ecommerce/backend/mcp_server.py` | REWRITE | Task 4 |
| `db/database.py` | MODIFY | Task 5 |
| `kyc_service.py` | MODIFY | Task 5 |
| `server.py` | MODIFY | Task 5 |
| `ecommerce/backend/main.py` | MODIFY | Task 6 |
| `test_e2e_flow.py` | CREATE | Task 7 |
| `README.md` | REWRITE | Task 8 |
