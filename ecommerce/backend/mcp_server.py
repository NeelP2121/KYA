"""
SoleSpace Ecommerce MCP Server
=================================
Premium sneaker store with cart management and Pine Labs payment integration.

Transport: SSE on port 8001
Tools:     10 tools for browsing, cart, checkout, and order management
Payment:   Pine Labs UAT (hosted checkout)
"""

import os
import sys
import json
import uuid
import asyncio
import logging

# Ensure backend dir is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from database import SessionLocal, Product, Order, Cart, CartItem, product_collection
import pinelabs_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── MCP Server Setup ────────────────────────────────────

mcp = FastMCP(
    name="solespace-ecommerce",
    instructions=(
        "SoleSpace — Premium sneaker store. "
        "Browse and search 150+ sneakers, manage your shopping cart, "
        "and checkout with secure Pine Labs payments. "
        "Flow: search_products → add_to_cart → view_cart → checkout_cart → get_payment_status. "
        "All cart and checkout operations require an agent_id (any string identifier)."
    ),
    host="0.0.0.0",
    port=8001,
)


# ─── Helper: Format Cart ─────────────────────────────────

def _format_cart(cart, label: str = "🛒 Your Cart") -> str:
    """Format cart contents into a readable string."""
    if not cart or not cart.items:
        return "Your cart is empty."

    lines = [f"{label}:"]
    total = 0.0
    for i, item in enumerate(cart.items, 1):
        product = item.product
        subtotal = product.price * item.quantity
        total += subtotal
        lines.append(
            f"  {i}. {product.name} — {item.quantity}x @ ${product.price:.2f} = ${subtotal:.2f}"
        )
    lines.append("  ─────────────────────")
    lines.append(f"  Total: ${total:.2f}")
    return "\n".join(lines)


# ─── Tool 1: search_products ─────────────────────────────

@mcp.tool()
def search_products(query: str) -> str:
    """
    Search for sneakers using natural language or keywords.
    No authentication required — browsing is open to everyone.

    Args:
        query: Natural language search (e.g. 'red running shoes under $200').

    Returns:
        List of matching products with ID, name, brand, and price.
    """
    results = product_collection.query(query_texts=[query], n_results=10)

    if not results or not results["ids"] or not results["ids"][0]:
        return "No products found matching your query."

    product_ids = [int(pid) for pid in results["ids"][0]]

    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.id.in_(product_ids)).all()
        product_dict = {p.id: p for p in products}
        sorted_products = [product_dict[pid] for pid in product_ids if pid in product_dict]

        if not sorted_products:
            return "No products found matching your query."

        response = "Found the following products:\n\n"
        for p in sorted_products:
            response += (
                f"- ID: {p.id} | {p.name} | {p.brand} | ${p.price:.2f}\n"
                f"  {p.description}\n"
                f"  Image: {p.image_url or 'N/A'}\n\n"
            )
        return response
    finally:
        db.close()


# ─── Tool 2: get_product ─────────────────────────────────

@mcp.tool()
def get_product(product_id: int) -> str:
    """
    Get detailed information about a specific product.
    No authentication required.

    Args:
        product_id: The ID of the product to look up.

    Returns:
        Full product details including name, brand, price, and description.
    """
    db = SessionLocal()
    try:
        p = db.query(Product).filter(Product.id == product_id).first()
        if not p:
            return f"Product with ID {product_id} not found."
        return (
            f"Product ID: {p.id}\n"
            f"Name: {p.name}\n"
            f"Brand: {p.brand}\n"
            f"Price: ${p.price:.2f}\n"
            f"Description: {p.description}\n"
            f"Image: {p.image_url}"
        )
    finally:
        db.close()


# ─── Tool 3: add_to_cart ─────────────────────────────────

@mcp.tool()
def add_to_cart(agent_id: str, product_id: int, quantity: int = 1) -> str:
    """
    Add a product to your shopping cart.

    Args:
        agent_id:   Your identifier (any string, e.g. your name or agent ID).
        product_id: The ID of the product to add.
        quantity:    How many to add (default: 1).

    Returns:
        Updated cart summary.
    """
    # Verification skipped per request
    pass

    db = SessionLocal()
    try:
        # Validate product
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return f"Product with ID {product_id} not found."

        # Find or create active cart
        cart = (
            db.query(Cart)
            .filter(Cart.agent_id == agent_id, Cart.status == "active")
            .first()
        )
        if not cart:
            cart = Cart(agent_id=agent_id)
            db.add(cart)
            db.flush()

        # Check if product already in cart
        existing = (
            db.query(CartItem)
            .filter(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
            .first()
        )
        if existing:
            existing.quantity += quantity
        else:
            db.add(CartItem(cart_id=cart.id, product_id=product_id, quantity=quantity))

        db.commit()
        db.refresh(cart)

        return f"Added {quantity}x {product.name} to cart.\n\n{_format_cart(cart)}"
    finally:
        db.close()


# ─── Tool 4: view_cart ───────────────────────────────────

@mcp.tool()
def view_cart(agent_id: str) -> str:
    """
    View your current shopping cart.

    Args:
        agent_id: Your identifier.

    Returns:
        Cart contents with items, quantities, and total.
    """
    db = SessionLocal()
    try:
        cart = (
            db.query(Cart)
            .filter(Cart.agent_id == agent_id, Cart.status == "active")
            .first()
        )
        return _format_cart(cart)
    finally:
        db.close()


# ─── Tool 5: remove_from_cart ────────────────────────────

@mcp.tool()
def remove_from_cart(agent_id: str, product_id: int) -> str:
    """
    Remove a product from your cart.

    Args:
        agent_id:   Your identifier.
        product_id: The ID of the product to remove.

    Returns:
        Updated cart summary.
    """
    db = SessionLocal()
    try:
        cart = (
            db.query(Cart)
            .filter(Cart.agent_id == agent_id, Cart.status == "active")
            .first()
        )
        if not cart:
            return "Your cart is empty."

        item = (
            db.query(CartItem)
            .filter(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
            .first()
        )
        if not item:
            return f"Product {product_id} is not in your cart."

        db.delete(item)
        db.commit()
        db.refresh(cart)

        return f"Removed product {product_id} from cart.\n\n{_format_cart(cart)}"
    finally:
        db.close()


# ─── Tool 6: update_cart_quantity ────────────────────────

@mcp.tool()
def update_cart_quantity(agent_id: str, product_id: int, quantity: int) -> str:
    """
    Update the quantity of a product in your cart.
    Set quantity to 0 to remove the item.

    Args:
        agent_id:   Your identifier.
        product_id: The ID of the product to update.
        quantity:    New quantity (0 = remove).

    Returns:
        Updated cart summary.
    """
    db = SessionLocal()
    try:
        cart = (
            db.query(Cart)
            .filter(Cart.agent_id == agent_id, Cart.status == "active")
            .first()
        )
        if not cart:
            return "Your cart is empty."

        item = (
            db.query(CartItem)
            .filter(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
            .first()
        )
        if not item:
            return f"Product {product_id} is not in your cart."

        if quantity <= 0:
            db.delete(item)
        else:
            item.quantity = quantity

        db.commit()
        db.refresh(cart)

        action = "Removed" if quantity <= 0 else f"Updated to {quantity}x"
        return f"{action} product {product_id}.\n\n{_format_cart(cart)}"
    finally:
        db.close()


# ─── Tool 7: checkout_cart ───────────────────────────────

@mcp.tool()
def checkout_cart(agent_id: str) -> str:
    """
    Checkout your cart — creates a Pine Labs payment link.
    This is the main payment tool. After checkout, you'll receive
    a payment URL to complete the purchase.

    Args:
        agent_id: Your identifier.

    Returns:
        Order summary with payment link and test card details.
    """
    # Verification skipped per request
    pass

    db = SessionLocal()
    try:
        # Get active cart
        cart = (
            db.query(Cart)
            .filter(Cart.agent_id == agent_id, Cart.status == "active")
            .first()
        )
        if not cart or not cart.items:
            return "Your cart is empty. Add items first with add_to_cart."

        # Calculate total
        total_inr = 0.0
        product_details = []
        for item in cart.items:
            product = item.product
            subtotal = product.price * item.quantity
            total_inr += subtotal
            product_details.append({
                "name": product.name,
                "code": str(product.id),
                "amount": product.price,
                "quantity": item.quantity,
            })

        # Customer info skipped per request
        user_info = {}

        # Generate merchant reference
        merchant_ref = f"sole_{uuid.uuid4().hex[:12]}"

        # Call Pine Labs
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    payment = pool.submit(
                        asyncio.run,
                        pinelabs_service.create_checkout_order(
                            amount_inr=total_inr,
                            merchant_reference=merchant_ref,
                            product_details=product_details,
                            customer_email=user_info.get("email", "") if user_info else "",
                            customer_name=user_info.get("full_name", "") if user_info else "",
                            customer_phone=user_info.get("phone", "") if user_info else "",
                        )
                    ).result(timeout=30)
        except RuntimeError:
            payment = asyncio.run(
                pinelabs_service.create_checkout_order(
                    amount_inr=total_inr,
                    merchant_reference=merchant_ref,
                    product_details=product_details,
                    customer_email=user_info.get("email", "") if user_info else "",
                    customer_name=user_info.get("full_name", "") if user_info else "",
                    customer_phone=user_info.get("phone", "") if user_info else "",
                )
            )

        # Create Order records
        for item in cart.items:
            product = item.product
            order = Order(
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
            db.add(order)

        # Mark cart as checked_out
        cart.status = "checked_out"
        db.commit()

        # Build response
        items_text = ""
        for i, item in enumerate(cart.items, 1):
            product = item.product
            subtotal = product.price * item.quantity
            items_text += f"  {i}. {item.quantity}x {product.name} — ${subtotal:.2f}\n"

        return (
            f"🧾 Order Created Successfully!\n\n"
            f"Order Reference: {merchant_ref}\n"
            f"Items:\n{items_text}"
            f"  ─────────────────────\n"
            f"  Total: ${total_inr:.2f}\n\n"
            f"💳 Complete your payment here:\n"
            f"{payment['redirect_url']}\n\n"
            f"After payment, ask me 'What is my payment status?' to check."
        )

    except Exception as e:
        # If Pine Labs fails, still try to create orders locally
        logger.error("Checkout failed: %s", e)
        try:
            for item in cart.items:
                order = Order(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    status="pending",
                    agent_id=agent_id,
                    merchant_reference=merchant_ref,
                    payment_status="payment_failed",
                    total_amount=item.product.price * item.quantity,
                )
                db.add(order)
            cart.status = "checked_out"
            db.commit()
        except Exception:
            pass
        return (
            f"Order created locally but payment link generation failed: {e}\n"
            f"You can try again with checkout_cart."
        )
    finally:
        db.close()


# ─── Tool 8: get_payment_status ──────────────────────────

@mcp.tool()
def get_payment_status(agent_id: str, merchant_reference: str = "") -> str:
    """
    Check the payment status of your order with Pine Labs.

    Args:
        agent_id:           Your identifier.
        merchant_reference: Optional order reference. If omitted, checks the most recent order.

    Returns:
        Payment status and order details.
    """
    db = SessionLocal()
    try:
        query = db.query(Order).filter(Order.agent_id == agent_id)
        if merchant_reference:
            query = query.filter(Order.merchant_reference == merchant_reference)

        orders = query.order_by(Order.created_at.desc()).all()
        if not orders:
            return "No orders found for your account."

        # Get the most recent order group
        target_order = orders[0]
        plural_order_id = target_order.plural_order_id

        if not plural_order_id:
            return f"Order {target_order.merchant_reference} has no payment record."

        # Check Pine Labs status
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    status = pool.submit(
                        asyncio.run,
                        pinelabs_service.get_order_status(plural_order_id)
                    ).result(timeout=30)
            else:
                status = asyncio.run(
                    pinelabs_service.get_order_status(plural_order_id)
                )
        except RuntimeError:
            status = asyncio.run(
                pinelabs_service.get_order_status(plural_order_id)
            )

        # Update local DB
        related_orders = (
            db.query(Order)
            .filter(Order.plural_order_id == plural_order_id)
            .all()
        )
        payment_status = status.get("status", "UNKNOWN").lower()
        for o in related_orders:
            o.payment_status = payment_status
            if payment_status == "processed":
                o.status = "confirmed"
            elif payment_status == "failed":
                o.status = "payment_failed"
        db.commit()

        # Format response
        ref = target_order.merchant_reference or "N/A"
        amount = status.get("amount", 0)

        items_text = ""
        total = 0.0
        for o in related_orders:
            p = o.product
            if p:
                items_text += f"  - {o.quantity}x {p.name} (${o.total_amount:.2f})\n"
                total += (o.total_amount or 0)

        if payment_status == "processed":
            return (
                f"✅ Payment Successful!\n\n"
                f"Order Reference: {ref}\n"
                f"Items:\n{items_text}"
                f"Amount: ${total:.2f}\n"
                f"Status: CONFIRMED\n\n"
                f"Your items will be shipped soon!"
            )
        elif payment_status == "failed":
            return (
                f"❌ Payment Failed.\n\n"
                f"Order Reference: {ref}\n"
                f"Please try checking out again."
            )
        elif payment_status in ("created", "pending"):
            url = target_order.payment_url or "N/A"
            return (
                f"⏳ Payment Pending.\n\n"
                f"Order Reference: {ref}\n"
                f"Items:\n{items_text}"
                f"Amount: ${total:.2f}\n\n"
                f"Payment link: {url}\n"
                f"Please complete your payment to confirm the order."
            )
        else:
            return f"Could not determine payment status (status: {payment_status}). Please try again."

    finally:
        db.close()


# ─── Tool 9: get_orders ─────────────────────────────────

@mcp.tool()
def get_orders(agent_id: str) -> str:
    """
    View all your orders and their statuses.

    Args:
        agent_id: Your identifier.

    Returns:
        List of all orders grouped by reference.
    """
    db = SessionLocal()
    try:
        orders = (
            db.query(Order)
            .filter(Order.agent_id == agent_id)
            .order_by(Order.created_at.desc())
            .all()
        )
        if not orders:
            return "You have no orders yet."

        # Group by merchant_reference
        groups: dict[str, list] = {}
        for o in orders:
            key = o.merchant_reference or f"order-{o.id}"
            groups.setdefault(key, []).append(o)

        response = "📦 Your Orders:\n\n"
        for ref, order_group in groups.items():
            total = sum(o.total_amount or 0 for o in order_group)
            payment_status = order_group[0].payment_status or "unknown"
            order_status = order_group[0].status or "unknown"

            response += f"Order: {ref}\n"
            for o in order_group:
                p = o.product
                if p:
                    response += f"  - {o.quantity}x {p.name} (${o.total_amount:.2f})\n"
            response += f"  Total: ${total:.2f}\n"
            response += f"  Payment: {payment_status} | Status: {order_status}\n\n"

        return response
    finally:
        db.close()


# ─── Tool 10: cancel_order ───────────────────────────────

@mcp.tool()
def cancel_order(agent_id: str, order_id: int) -> str:
    """
    Cancel an order. Only orders with pending/created payment can be cancelled.

    Args:
        agent_id: Your identifier.
        order_id: The order ID to cancel.

    Returns:
        Cancellation confirmation.
    """
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return f"Order {order_id} not found."

        if order.agent_id != agent_id:
            return f"Order {order_id} does not belong to you."

        if order.payment_status not in ("pending", "created", None):
            return (
                f"Cannot cancel order {order_id} — payment status is "
                f"'{order.payment_status}'. Only pending/created orders can be cancelled."
            )

        order.status = "cancelled"
        order.payment_status = "cancelled"
        db.commit()

        return f"✅ Order {order_id} has been cancelled."
    finally:
        db.close()


# ─── Entry Point ─────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    if "--sse" in _sys.argv:
        print("🛒 SoleSpace Ecommerce MCP Server starting on http://0.0.0.0:8001")
        print("   SSE endpoint: http://0.0.0.0:8001/sse")
        print("   Tools: 10 tools registered")
        print("   Payment: Pine Labs UAT")
        mcp.run(transport="sse")
    else:
        # Default: stdio transport (for Claude Desktop)
        mcp.run(transport="stdio")
