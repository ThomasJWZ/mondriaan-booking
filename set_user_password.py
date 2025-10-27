from app import app, db, User

USERNAME = "mondriaan_maastricht"            # change me
NEW_PASSWORD = "maastrichtpython "  # change me

with app.app_context():
    u = User.query.filter_by(username=USERNAME).first()
    if not u:
        print("User not found:", USERNAME)
    else:
        u.set_password(NEW_PASSWORD)
        db.session.commit()
        print("Password updated for", USERNAME)
