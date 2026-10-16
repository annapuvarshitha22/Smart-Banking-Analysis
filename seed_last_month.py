from datetime import datetime
from app import app
from models import db, User, Transaction

with app.app_context():
    user = User.query.filter_by(email="test1@gmail.com").first()
    account = user.customer.accounts[0]
    now = datetime.utcnow()
    if now.month == 1:
        when = datetime(now.year - 1, 12, 15, 12, 0)
    else:
        when = datetime(now.year, now.month - 1, 15, 12, 0)
    db.session.add(Transaction(account_id=account.id, type="withdrawal", amount=3000, category="food", timestamp=when))
    db.session.commit()
    print("Added a last-month transaction for test1.")