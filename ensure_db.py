from app import app, db
from sqlalchemy import text
import sqlite3, os

print("CWD:", os.getcwd())
print("DB URL is sqlite:///bookings.db (relative to CWD)")

with app.app_context():
    # Create all tables based on models (User, Booking, etc.)
    db.create_all()
    # Verify via SQLAlchemy
    try:
        res = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table';")).fetchall()
        print("Tables via SQLAlchemy:", res)
    except Exception as e:
        print("SQLAlchemy check failed:", e)

# Also verify via sqlite3 directly
conn = sqlite3.connect("bookings.db")
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
print("Tables via sqlite3:", cur.fetchall())
conn.close()
