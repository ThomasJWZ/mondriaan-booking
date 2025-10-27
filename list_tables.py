import sqlite3
import os

# Use the instance version
db_path = os.path.join("instance", "bookings.db")
print("Checking:", os.path.abspath(db_path))

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
print(cur.fetchall())
conn.close()
