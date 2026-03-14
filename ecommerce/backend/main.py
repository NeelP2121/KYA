from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db, Product, Order, product_collection
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Ecommerce Sneakers API")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models for requests/responses
class ProductResponse(BaseModel):
    id: int
    name: str
    brand: str
    price: float
    description: str
    image_url: str

    class Config:
        from_attributes = True

class OrderCreate(BaseModel):
    product_id: int
    quantity: int = 1

class OrderResponse(BaseModel):
    id: int
    product_id: int
    quantity: int
    status: str
    product: ProductResponse

    class Config:
        from_attributes = True

class CartItem(BaseModel):
    product_id: int
    quantity: int

class CheckoutRequest(BaseModel):
    items: List[CartItem]

class CheckoutResponse(BaseModel):
    redirect_url: str
    order_ids: List[int]

@app.get("/api/products", response_model=List[ProductResponse])
def get_products(
    skip: int = 0, 
    limit: int = 50, 
    brand: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Product)
    if brand:
        query = query.filter(Product.brand.ilike(f"%{brand}%"))
    products = query.offset(skip).limit(limit).all()
    return products

@app.get("/api/products/search", response_model=List[ProductResponse])
def search_products(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    # Use ChromaDB for semantic search
    results = product_collection.query(
        query_texts=[q],
        n_results=10
    )
    
    if not results or not results['ids'] or not results['ids'][0]:
        return []

    # results['ids'][0] contains a list of ID strings
    product_ids = [int(pid) for pid in results['ids'][0]]
    
    # Fetch from SQLite to get full details and preserve relevance order
    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    
    # Sort products according to the order returned by ChromaDB
    product_dict = {p.id: p for p in products}
    sorted_products = [product_dict[pid] for pid in product_ids if pid in product_dict]
    
    return sorted_products

@app.get("/api/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@app.post("/api/orders", response_model=OrderResponse)
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == order.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    db_order = Order(product_id=order.product_id, quantity=order.quantity)
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    return db_order

@app.post("/api/checkout", response_model=CheckoutResponse)
def create_checkout_session(request: CheckoutRequest, db: Session = Depends(get_db)):
    if not request.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    import urllib.request
    import json
    import base64
    import uuid
    import ssl

    ssl._create_default_https_context = ssl._create_unverified_context
    client_id = "e4212815-589d-4075-839a-c2c9911f0823"
    client_secret = "3f7a7103badf4c518a0c918621b969af"

    try:
        # 1. Create Oauth Token
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
        with urllib.request.urlopen(token_req) as response:
            token_data = json.loads(response.read().decode())
            access_token = token_data.get("access_token")
            
        # 2. Gather products and compute total, create local orders
        total_paise = 0
        product_details = []
        created_order_ids = []
        
        for item in request.items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
                
            db_order = Order(product_id=item.product_id, quantity=item.quantity)
            db.add(db_order)
            db.commit()
            db.refresh(db_order)
            created_order_ids.append(db_order.id)
            
            total_paise += int(product.price * item.quantity * 100)
            product_details.append({
                "product_code": str(product.id),
                "product_amount": int(product.price * 100),
                "product_quantity": item.quantity
            })
            
        # 3. Create Pine Labs Order
        merchant_ref = f"sole_checkout_{uuid.uuid4().hex[:8]}"
        order_payload = {
            "merchant_data": {
                "merchant_order_reference": merchant_ref,
                "merchant_return_url": "http://localhost:5173/payment/callback"
            },
            "payment_data": {
                "amount": total_paise,
                "currency_code": "INR"
            },
            "product_data": {
                "product_details": product_details
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
        
        with urllib.request.urlopen(order_req) as response:
            order_data = json.loads(response.read().decode())
            payment_url = order_data.get("payment_url")
            
            # PineLabs Hosted Checkout expects a redirect to /checkout?token=... instead of simple payment_url sometimes
            # We'll return the payment_url which is standard for Hosted integration
            if not payment_url:
                raise HTTPException(status_code=500, detail="Missing payment_url in Pine Labs response")
                
            return CheckoutResponse(redirect_url=payment_url, order_ids=created_order_ids)

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"Pine Labs API Error: {e.code} - {error_body}")
        raise HTTPException(status_code=502, detail=f"Payment Gateway Error: {error_body}")
    except Exception as e:
        print(f"Internal Check Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orders", response_model=List[OrderResponse])
def get_orders(db: Session = Depends(get_db)):
    orders = db.query(Order).all()
    return orders

@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order.status = "cancelled"
    db.commit()
    return {"status": "success", "message": f"Order {order_id} cancelled"}
