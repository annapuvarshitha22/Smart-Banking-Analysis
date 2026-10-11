import random
import string
from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def gen_id(prefix, length=10):
    return prefix + "".join(random.choices(string.digits, k=length))


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="customer")  # 'admin' or 'customer'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", backref="user", uselist=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    accounts = db.relationship("Account", backref="customer", cascade="all, delete-orphan")
    loans = db.relationship("Loan", backref="customer", cascade="all, delete-orphan")
    credit_cards = db.relationship("CreditCard", backref="customer", cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="customer", cascade="all, delete-orphan")


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    account_number = db.Column(db.String(20), unique=True, nullable=False, default=lambda: gen_id("AC"))
    account_type = db.Column(db.String(20), nullable=False)  # 'savings' or 'current'
    balance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship("Transaction", backref="account", cascade="all, delete-orphan")


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # deposit/withdrawal/transfer_in/transfer_out
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), default="general")
    note = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    fraud_alerts = db.relationship("FraudAlert", backref="transaction", cascade="all, delete-orphan")


class Loan(db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    interest_rate = db.Column(db.Float, nullable=False)
    term_months = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending/approved/rejected/closed
    amount_repaid = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CreditCard(db.Model):
    __tablename__ = "credit_cards"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    card_number = db.Column(db.String(20), unique=True, nullable=False, default=lambda: gen_id("", 16))
    credit_limit = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=30))


class FraudAlert(db.Model):
    __tablename__ = "fraud_alerts"

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    severity = db.Column(db.String(10), default="low")  # low/medium/high
    resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)