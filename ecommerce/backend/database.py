import os
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import chromadb

# SQLite Setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'ecommerce.db')}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    brand = Column(String, index=True)
    price = Column(Float)
    description = Column(String)
    image_url = Column(String)

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=1)
    status = Column(String, default="pending")  # pending, shipped, cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product")

# Create tables
Base.metadata.create_all(bind=engine)

# ChromaDB Setup
chroma_client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "chroma_db"))
product_collection = chroma_client.get_or_create_collection(name="products")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
