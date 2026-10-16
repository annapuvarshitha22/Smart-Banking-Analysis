import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"   # tests never touch your real database

import pytest
from app import app
from models import db, User, Customer, Account, Transaction, FraudAlert, Loan, CreditCard
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


    # ======================================================
# PHASE 1 TESTS: core banking features
# ======================================================

LOAN = {"amount": "50000", "interest_rate": "8", "term_months": "12"}


def signup_and_login(client, email="user@example.com", name="Test User"):
    client.post("/register", data={"email": email, "password": "pw12345", "full_name": name})
    client.post("/login", data={"email": email, "password": "pw12345"})


def open_account(client, account_type="savings"):
    client.post("/accounts/new", data={"account_type": account_type})


def all_accounts():
    db.session.expire_all()
    return Account.query.order_by(Account.id).all()


def balance_of(account_id):
    db.session.expire_all()
    return db.session.get(Account, account_id).balance


def first_id(model):
    db.session.expire_all()
    return model.query.first().id


def test_register_creates_user_and_customer_profile(client):
    signup_and_login(client, "a@example.com", "Asha")
    db.session.expire_all()
    user = User.query.filter_by(email="a@example.com").first()
    assert user.role == "customer"
    assert user.customer.full_name == "Asha"
    assert user.password_hash != "pw12345"  # password is stored hashed


def test_duplicate_email_is_rejected(client):
    signup_and_login(client, "dup@example.com")
    client.post("/register", data={"email": "dup@example.com", "password": "x", "full_name": "Other"})
    db.session.expire_all()
    assert User.query.filter_by(email="dup@example.com").count() == 1


def test_wrong_password_cannot_log_in(client):
    client.post("/register", data={"email": "w@example.com", "password": "pw12345", "full_name": "W"})
    client.post("/login", data={"email": "w@example.com", "password": "wrong"})
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_open_savings_and_current_accounts(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    assert [a.account_type for a in all_accounts()] == ["savings", "current"]


def test_deposit_increases_balance(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "1000", "category": "salary"})
    assert balance_of(acc_id) == 1000.0


def test_withdraw_decreases_balance(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "1000", "category": "salary"})
    client.post(f"/accounts/{acc_id}/withdraw", data={"amount": "400", "category": "food"})
    assert balance_of(acc_id) == 600.0


def test_withdraw_more_than_balance_is_rejected(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "500", "category": "salary"})
    client.post(f"/accounts/{acc_id}/withdraw", data={"amount": "900", "category": "food"})
    assert balance_of(acc_id) == 500.0


def test_negative_deposit_is_rejected(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "-50", "category": "x"})
    assert balance_of(acc_id) == 0.0


def test_blank_category_defaults_to_general(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "100", "category": ""})
    db.session.expire_all()
    assert Transaction.query.first().category == "general"


def test_transfer_moves_money_and_records_both_sides(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    first, second = [a.id for a in all_accounts()]
    client.post(f"/accounts/{first}/deposit", data={"amount": "1000", "category": "salary"})
    client.post("/transfer", data={"from_account_id": first, "to_account_id": second, "amount": "300"})
    assert balance_of(first) == 700.0
    assert balance_of(second) == 300.0
    db.session.expire_all()
    assert sorted(t.type for t in Transaction.query.all()) == ["deposit", "transfer_in", "transfer_out"]


def test_transfer_with_insufficient_funds_is_rejected(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    first, second = [a.id for a in all_accounts()]
    client.post(f"/accounts/{first}/deposit", data={"amount": "100", "category": "salary"})
    client.post("/transfer", data={"from_account_id": first, "to_account_id": second, "amount": "500"})
    assert balance_of(first) == 100.0
    assert balance_of(second) == 0.0


def test_cannot_deposit_into_someone_elses_account(client):
    signup_and_login(client, "owner@example.com", "Owner")
    open_account(client)
    acc_id = all_accounts()[0].id
    intruder = app.test_client()
    signup_and_login(intruder, "intruder@example.com", "Intruder")
    intruder.post(f"/accounts/{acc_id}/deposit", data={"amount": "999", "category": "x"})
    assert balance_of(acc_id) == 0.0


def test_loan_application_starts_as_pending(client):
    signup_and_login(client)
    client.post("/loans", data=LOAN)
    db.session.expire_all()
    loan = Loan.query.first()
    assert loan.status == "pending"
    assert loan.amount == 50000.0


def test_customer_cannot_approve_own_loan(client):
    signup_and_login(client)
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    response = client.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "pending"


def test_admin_can_approve_loan(client):
    signup_and_login(client, "cust@example.com", "Cust")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin_user = User(email="admin@example.com", role="admin")
    admin_user.set_password("adminpw1")
    db.session.add(admin_user)
    db.session.commit()
    admin_client = app.test_client()
    admin_client.post("/login", data={"email": "admin@example.com", "password": "adminpw1"})
    admin_client.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "approved"


def test_repaying_full_amount_closes_loan(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "1000"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "closed"


def test_credit_card_is_issued_with_limit(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "20000"})
    db.session.expire_all()
    card = CreditCard.query.first()
    assert card.credit_limit == 20000.0
    assert card.balance == 0.0
    assert len(card.card_number) == 16


def test_card_charge_over_limit_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "800"})
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "500"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 800.0


def test_card_payment_reduces_balance(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "600"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "250"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 350.0

    # ======================================================
# VALIDATION TESTS
# ======================================================

def test_negative_loan_amount_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "-100", "interest_rate": "8", "term_months": "12"})
    db.session.expire_all()
    assert Loan.query.count() == 0


def test_loan_with_zero_term_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "0"})
    db.session.expire_all()
    assert Loan.query.count() == 0


def test_cannot_repay_more_than_remaining(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "1500"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0


def test_negative_repayment_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "-200"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0


def test_card_with_non_positive_limit_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "-500"})
    db.session.expire_all()
    assert CreditCard.query.count() == 0


def test_negative_card_charge_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "-300"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 0.0


def test_negative_card_payment_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "200"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "-100"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 200.0