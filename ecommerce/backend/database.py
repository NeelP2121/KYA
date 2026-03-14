"""
Ecommerce Database Models & Setup
===================================
SQLAlchemy models for Products, Orders, Cart, CartItems.
ChromaDB vector store for semantic product search.

NOTE: If you add new columns to existing tables, you must delete
ecommerce.db and re-run seed_db.py — SQLite + create_all() does
not support ALTER TABLE ADD COLUMN reliably.
"""

import os
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import chromadb

# ─── SQLite Setup ─────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'ecommerce.db')}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ─── Models ───────────────────────────────────────────────

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    brand = Column(String, index=True)
    price = Column(Float)
    description = Column(String)
    image_url = Column(String)


class Cart(Base):
    __tablename__ = "carts"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, index=True, nullable=False)
    status = Column(String, default="active")  # active | checked_out | abandoned
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"

    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("carts.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

    cart = relationship("Cart", back_populates="items")
    product = relationship("Product")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=1)
    status = Column(String, default="pending")  # pending, confirmed, shipped, cancelled, payment_failed
    created_at = Column(DateTime, default=datetime.utcnow)

    # Payment & agent fields
    agent_id = Column(String, nullable=True, index=True)
    plural_order_id = Column(String, nullable=True, index=True)
    merchant_reference = Column(String, nullable=True)
    payment_url = Column(String, nullable=True)
    payment_status = Column(String, default="pending")  # pending | created | processed | failed
    total_amount = Column(Float, nullable=True)

    product = relationship("Product")


# ─── Create Tables ────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─── ChromaDB Setup ──────────────────────────────────────
chroma_client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "chroma_db"))
product_collection = chroma_client.get_or_create_collection(name="products")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
