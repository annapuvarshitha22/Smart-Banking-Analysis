import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"   # tests never touch your real database

import pytest
from app import app
from models import db, User, Customer, Account, Transaction, FraudAlert
from fraud import check_transaction_for_fraud
from loan_decision import recommend_loan


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def make_account():
    user = User(email="t@example.com", role="customer")
    user.set_password("pw12345")
    db.session.add(user)
    db.session.flush()
    customer = Customer(user_id=user.id, full_name="Test User")
    db.session.add(customer)
    db.session.flush()
    account = Account(customer_id=customer.id, account_type="savings", balance=500000.0)
    db.session.add(account)
    db.session.commit()
    return account


def add_txn(account, amount, txn_type="withdrawal"):
    txn = Transaction(account_id=account.id, type=txn_type, amount=amount, category="test")
    db.session.add(txn)
    db.session.commit()
    return txn


# ---------------- fraud rules ----------------

def test_large_transaction_is_flagged(client):
    account = make_account()
    txn = add_txn(account, 150000)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Unusually large transaction amount" in reasons


def test_normal_transaction_is_not_flagged_as_large(client):
    account = make_account()
    txn = add_txn(account, 500)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Unusually large transaction amount" not in reasons


def test_rapid_transactions_are_flagged(client):
    account = make_account()
    for _ in range(4):
        txn = add_txn(account, 100)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Multiple transactions in a short time window" in reasons


# ---------------- loan decision support ----------------

def test_low_credit_score_is_rejected():
    label, _ = recommend_loan(450, 10000, 50000)
    assert label == "Reject"


def test_good_score_and_affordable_loan_is_approved():
    label, _ = recommend_loan(720, 50000, 20000)
    assert label == "Approve"


def test_good_score_but_huge_loan_needs_review():
    label, _ = recommend_loan(720, 500000, 20000)
    assert label == "Review"


def test_borderline_score_needs_review():
    label, _ = recommend_loan(651, 50000, 13000)
    assert label == "Review"


# ---------------- security ----------------

def test_customer_cannot_open_admin_page(client):
    client.post("/register", data={"email": "c@example.com", "password": "pw12345", "full_name": "C"})
    client.post("/login", data={"email": "c@example.com", "password": "pw12345"})
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]