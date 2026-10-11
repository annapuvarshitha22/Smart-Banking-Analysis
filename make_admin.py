from app import app
from models import db, User

EMAIL_TO_PROMOTE = "varshitha.annapu@gmail.com"

with app.app_context():
    user = User.query.filter_by(email=EMAIL_TO_PROMOTE).first()
    if not user:
        print(f"No user found with email: {EMAIL_TO_PROMOTE}")
    else:
        user.role = "admin"
        db.session.commit()
        print(f"{EMAIL_TO_PROMOTE} is now an admin.")