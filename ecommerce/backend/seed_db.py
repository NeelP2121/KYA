import random
from faker import Faker
from database import SessionLocal, Product, product_collection
import uuid

fake = Faker()

brands = ["Nike", "Adidas", "Jordan", "New Balance", "Asics", "Puma", "Reebok"]
models = ["Air Max", "Dunk Low", "Ultraboost", "Yeezy", "Air Force 1", "550", "990v6", "Gel-Kayano", "Suede", "Club C"]
colors = ["Black", "White", "Red", "Blue", "Green", "Grey", "Volt", "Sail", "Pink", "Orange"]

def generate_product():
    brand = random.choice(brands)
    model = random.choice(models)
    color = random.choice(colors)
    name = f"{brand} {model} '{color}'"
    price = round(random.uniform(90.0, 350.0), 2)
    
    # Use local static placeholder images
    sneaker_images = [
        "http://127.0.0.1:8000/static/image_1.png",
        "http://127.0.0.1:8000/static/image_2.png",
        "http://127.0.0.1:8000/static/image_3.png",
        "http://127.0.0.1:8000/static/image_4.png",
        "http://127.0.0.1:8000/static/image_5.png"
    ]
    image_url = random.choice(sneaker_images)

    description = f"A premium {color.lower()} colorway of the iconic {model} by {brand}. {fake.sentence(nb_words=10)}"
    
    return {
        "name": name,
        "brand": brand,
        "price": price,
        "description": description,
        "image_url": image_url
    }

def seed_database():
    db = SessionLocal()
    
    # Check if already seeded
    if db.query(Product).count() > 0:
        print("Database already seeded. Skipping.")
        return

    print("Generating 150 sneaker products...")
    
    db_products = []
    docs = []
    metadatas = []
    ids = []
    
    for i in range(150):
        p_data = generate_product()
        # Add to SQLite
        product = Product(**p_data)
        db.add(product)
        db.flush() # To get the ID
        db_products.append(product)
        
        # Prepare for ChromaDB
        doc_text = f"{p_data['name']}. {p_data['brand']}. {p_data['description']}"
        docs.append(doc_text)
        metadatas.append({
            "product_id": product.id,
            "name": p_data["name"],
            "brand": p_data["brand"],
            "price": p_data["price"]
        })
        ids.append(str(product.id))

    db.commit()
    
    print("Populating ChromaDB vector database...")
    # Add to ChromaDB in batches if necessary, but 150 is small enough for one go
    product_collection.add(
        documents=docs,
        metadatas=metadatas,
        ids=ids
    )

    print("Database seeding completed successfully.")
    db.close()

if __name__ == "__main__":
    seed_database()
