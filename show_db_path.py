from app import app
print("Active DB path:", app.config["SQLALCHEMY_DATABASE_URI"])
