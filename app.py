import os
import csv
import io
import base64
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # no GUI backend needed on a server
import matplotlib.pyplot as plt

from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from models import db, User, Customer, Account, Transaction, Loan, CreditCard, FraudAlert, Budget
from fraud import check_transaction_for_fraud
from credit_score import get_mock_credit_score
from loan_decision import recommend_loan

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-change-me-in-production"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///smart_banking.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

@app.route('/')
def home():
    if current_user.is_authenticated:
        if getattr(current_user, 'is_admin', False):
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------- AUTH ----

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        full_name = request.form["full_name"].strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("register"))

        user = User(email=email, role="customer")
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # get user.id

        customer = Customer(user_id=user.id, full_name=full_name, phone=phone, address=address)
        db.session.add(customer)
        db.session.commit()

        flash("Registered successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            flash("Logged in successfully.", "success")
            if user.is_admin():
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# ----------------------------------------------------------- CUSTOMER -----

@app.route("/dashboard")
@login_required
def dashboard():
    customer = current_user.customer
    accounts = Account.query.filter_by(customer_id=customer.id).all()
    account_ids = [a.id for a in accounts]
    recent_txns = (
        Transaction.query.filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp.desc())
        .limit(15)
        .all()
        if account_ids
        else []
    )
    total_balance = sum(a.balance for a in accounts)
    return render_template(
        "dashboard.html", accounts=accounts, transactions=recent_txns, total_balance=total_balance
    )


@app.route("/accounts/new", methods=["POST"])
@login_required
def new_account():
    account_type = request.form["account_type"]
    if account_type not in ("savings", "current"):
        flash("Invalid account type.", "error")
        return redirect(url_for("dashboard"))

    account = Account(customer_id=current_user.customer.id, account_type=account_type)
    db.session.add(account)
    db.session.commit()
    flash(f"{account_type.title()} account created.", "success")
    return redirect(url_for("dashboard"))


def _get_own_account_or_none(account_id):
    account = Account.query.get(account_id)
    if not account or account.customer_id != current_user.customer.id:
        return None
    return account


@app.route("/accounts/<int:account_id>/deposit", methods=["POST"])
@login_required
def deposit(account_id):
    account = _get_own_account_or_none(account_id)
    if not account:
        flash("Account not found.", "error")
        return redirect(url_for("dashboard"))

    amount = float(request.form["amount"])
    category = request.form.get("category", "").strip() or "general"
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))

    account.balance += amount
    txn = Transaction(account_id=account.id, type="deposit", amount=amount, category=category)
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)

    flash(f"Deposited ₹{amount:.2f}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/accounts/<int:account_id>/withdraw", methods=["POST"])
@login_required
def withdraw(account_id):
    account = _get_own_account_or_none(account_id)
    if not account:
        flash("Account not found.", "error")
        return redirect(url_for("dashboard"))

    amount = float(request.form["amount"])
    category = request.form.get("category", "").strip() or "general"
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))
    if account.balance < amount:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard"))

    account.balance -= amount
    txn = Transaction(account_id=account.id, type="withdrawal", amount=amount, category=category)
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)

    flash(f"Withdrew ₹{amount:.2f}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/transfer", methods=["POST"])
@login_required
def transfer():
    from_id = int(request.form["from_account_id"])
    to_id = int(request.form["to_account_id"])
    amount = float(request.form["amount"])

    from_account = _get_own_account_or_none(from_id)
    to_account = Account.query.get(to_id)

    if not from_account or not to_account:
        flash("Invalid account(s).", "error")
        return redirect(url_for("dashboard"))
    if from_id == to_id:
        flash("Cannot transfer to the same account.", "error")
        return redirect(url_for("dashboard"))
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))
    if from_account.balance < amount:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard"))

    from_account.balance -= amount
    to_account.balance += amount

    out_txn = Transaction(account_id=from_account.id, type="transfer_out", amount=amount, category="transfer")
    in_txn = Transaction(account_id=to_account.id, type="transfer_in", amount=amount, category="transfer")
    db.session.add_all([out_txn, in_txn])
    db.session.commit()
    check_transaction_for_fraud(out_txn)

    flash(f"Transferred ₹{amount:.2f}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/loans", methods=["GET", "POST"])
@login_required
def loans():
    customer = current_user.customer
    if request.method == "POST":
        loan = Loan(
            customer_id=customer.id,
            amount=float(request.form["amount"]),
            interest_rate=float(request.form["interest_rate"]),
            term_months=int(request.form["term_months"]),
        )
        db.session.add(loan)
        db.session.commit()
        flash("Loan application submitted.", "success")
        return redirect(url_for("loans"))

    my_loans = Loan.query.filter_by(customer_id=customer.id).order_by(Loan.created_at.desc()).all()
    credit_score = get_mock_credit_score(customer)
    return render_template("loans.html", loans=my_loans, credit_score=credit_score)

@app.route("/loans/<int:loan_id>/repay", methods=["POST"])
@login_required
def repay_loan(loan_id):
    loan = Loan.query.get(loan_id)
    if not loan or loan.customer_id != current_user.customer.id:
        flash("Loan not found.", "error")
        return redirect(url_for("loans"))
    if loan.status != "approved":
        flash("Loan is not active/approved.", "error")
        return redirect(url_for("loans"))

    amount = float(request.form["amount"])
    loan.amount_repaid += amount
    if loan.amount_repaid >= loan.amount:
        loan.status = "closed"
    db.session.commit()
    flash("Repayment recorded.", "success")
    return redirect(url_for("loans"))


@app.route("/credit-cards", methods=["GET", "POST"])
@login_required
def credit_cards():
    customer = current_user.customer
    if request.method == "POST":
        card = CreditCard(customer_id=customer.id, credit_limit=float(request.form["credit_limit"]))
        db.session.add(card)
        db.session.commit()
        flash("Credit card issued.", "success")
        return redirect(url_for("credit_cards"))

    my_cards = CreditCard.query.filter_by(customer_id=customer.id).all()
    return render_template("credit_cards.html", cards=my_cards)


@app.route("/credit-cards/<int:card_id>/charge", methods=["POST"])
@login_required
def charge_card(card_id):
    card = CreditCard.query.get(card_id)
    if not card or card.customer_id != current_user.customer.id:
        flash("Card not found.", "error")
        return redirect(url_for("credit_cards"))

    amount = float(request.form["amount"])
    if card.balance + amount > card.credit_limit:
        flash("Charge would exceed credit limit.", "error")
    else:
        card.balance += amount
        db.session.commit()
        flash(f"Charged ₹{amount:.2f}.", "success")
    return redirect(url_for("credit_cards"))


@app.route("/credit-cards/<int:card_id>/pay", methods=["POST"])
@login_required
def pay_card(card_id):
    card = CreditCard.query.get(card_id)
    if not card or card.customer_id != current_user.customer.id:
        flash("Card not found.", "error")
        return redirect(url_for("credit_cards"))

    amount = float(request.form["amount"])
    card.balance = max(0.0, card.balance - amount)
    db.session.commit()
    flash(f"Paid ₹{amount:.2f} towards card.", "success")
    return redirect(url_for("credit_cards"))


@app.route("/set-budget", methods=["POST"])
@login_required
def set_budget():
    customer = current_user.customer
    category = request.form["category"].strip().lower()
    amount = float(request.form["amount"])

    budget = Budget.query.filter_by(customer_id=customer.id, category=category).first()
    if budget:
        budget.amount = amount
    else:
        budget = Budget(customer_id=customer.id, category=category, amount=amount)
        db.session.add(budget)
    db.session.commit()
    flash(f"Budget for '{category}' set to ₹{amount:.2f}.", "success")
    return redirect(url_for("analysis"))


@app.route("/analysis")
@login_required
def analysis():
    customer = current_user.customer
    account_ids = [a.id for a in Account.query.filter_by(customer_id=customer.id)]

    txns = Transaction.query.filter(Transaction.account_id.in_(account_ids)).all() if account_ids else []

    # --- Pandas-based analytics (replaces manual loop aggregation) ---
    rows = [
        {"category": t.category, "amount": t.amount, "type": t.type}
        for t in txns
        if t.type in ("withdrawal", "transfer_out")
    ]
    df = pd.DataFrame(rows, columns=["category", "amount", "type"])

    if not df.empty:
        by_category = df.groupby("category")["amount"].sum().sort_values(ascending=False)
        category_breakdown = [{"category": c, "total": float(v)} for c, v in by_category.items()]
        top_category = by_category.index[0]
        total_spent = float(by_category.sum())
    else:
        category_breakdown = []
        top_category = None
        total_spent = 0.0

    total_deposits = sum(t.amount for t in txns if t.type in ("deposit", "transfer_in"))
    net_balance = sum(a.balance for a in Account.query.filter_by(customer_id=customer.id))
    savings_rate = ((total_deposits - total_spent) / total_deposits * 100) if total_deposits > 0 else 0

    # --- Budget vs actual ---
    budgets = Budget.query.filter_by(customer_id=customer.id).all()
    spent_by_cat = {c["category"]: c["total"] for c in category_breakdown}
    budget_progress = []
    for b in budgets:
        spent = spent_by_cat.get(b.category, 0.0)
        pct = (spent / b.amount * 100) if b.amount > 0 else 0
        budget_progress.append({
            "category": b.category, "budget": b.amount, "spent": spent,
            "pct": min(pct, 100), "over": spent > b.amount,
        })
    alerts = [b for b in budget_progress if b["over"]]

    # --- Overall budget ring (sum of all category budgets vs spent) ---
    total_budget = sum(b.amount for b in budgets)
    overall_pct = (total_spent / total_budget * 100) if total_budget > 0 else 0
    overall_pct = round(min(overall_pct, 100), 1)
    on_track = total_spent <= total_budget if total_budget > 0 else True

    # --- Next month forecast (simple projection using NumPy) ---
    forecast_next_month = float(np.round(total_spent * 1.06, 2)) if total_spent > 0 else 0.0

    # --- Financial health score (derived from savings rate) ---
    health_score = int(max(0, min(100, round(60 + savings_rate))))
    if savings_rate >= 20:
        health_label = "Strong foundation"
        health_msg = "You are saving consistently and staying within your main limits."
    elif savings_rate >= 0:
        health_label = "Building steadily"
        health_msg = "Keep this pace to build a stronger savings cushion."
    else:
        health_label = "Needs attention"
        health_msg = "Spending is currently outpacing your deposits this period."

    chart_labels = [c["category"] for c in category_breakdown]
    chart_values = [c["total"] for c in category_breakdown]

    return render_template(
        "analysis.html",
        category_breakdown=category_breakdown,
        top_category=top_category,
        total_spent=total_spent,
        total_deposits=total_deposits,
        net_balance=net_balance,
        total_budget=total_budget,
        overall_pct=overall_pct,
        on_track=on_track,
        forecast_next_month=forecast_next_month,
        health_score=health_score,
        health_label=health_label,
        health_msg=health_msg,
        chart_labels=chart_labels,
        chart_values=chart_values,
        savings_rate=savings_rate,
        budget_progress=budget_progress,
        alerts=alerts,
    )


@app.route("/export/csv")
@login_required
def export_csv():
    customer = current_user.customer
    account_ids = [a.id for a in Account.query.filter_by(customer_id=customer.id)]
    txns = (
        Transaction.query.filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp.desc())
        .all()
        if account_ids
        else []
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Category", "Amount", "Note"])
    for t in txns:
        writer.writerow([t.timestamp.strftime("%Y-%m-%d %H:%M"), t.type, t.category, t.amount, t.note or ""])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


# --------------------------------------------------------------- ADMIN ----

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    total_customers = Customer.query.count()
    total_balance = db.session.query(db.func.sum(Account.balance)).scalar() or 0.0
    pending_loans = Loan.query.filter_by(status="pending").count()
    unresolved_alerts = FraudAlert.query.filter_by(resolved=False).count()

    # --- Matplotlib chart: bank-wide spending by category (admin view) ---
    all_txns = Transaction.query.filter(Transaction.type.in_(["withdrawal", "transfer_out"])).all()
    df = pd.DataFrame([{"category": t.category, "amount": t.amount} for t in all_txns])
    chart_base64 = None
    if not df.empty:
        by_cat = df.groupby("category")["amount"].sum().sort_values(ascending=False)
        fig, ax = plt.subplots(figsize=(6, 4))
        fig.patch.set_facecolor("#121f45")
        ax.set_facecolor("#121f45")
        ax.bar(by_cat.index, by_cat.values, color="#10E0C4")
        ax.set_ylabel("Total spent (₹)", color="white")
        ax.set_title("Bank-wide spending by category", color="white")
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_color("#2a3a6a")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        chart_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    # --- Loan decision support ---
    all_loans = Loan.query.order_by(Loan.created_at.desc()).all()
    loan_recommendations = {}
    for loan in all_loans:
        score = get_mock_credit_score(loan.customer)
        balance = sum(a.balance for a in loan.customer.accounts)
        label, reason = recommend_loan(score, loan.amount, balance)
        loan_recommendations[loan.id] = {"score": score, "label": label, "reason": reason}

    return render_template(
        "admin_dashboard.html",
        total_customers=total_customers,
        total_balance=total_balance,
        pending_loans=pending_loans,
        unresolved_alerts=unresolved_alerts,
        chart_base64=chart_base64,
        customers=Customer.query.all(),
        all_loans=all_loans,
        loan_recommendations=loan_recommendations,
        fraud_alerts=FraudAlert.query.filter_by(resolved=False).order_by(FraudAlert.created_at.desc()).all(),
    )


@app.route("/admin/loans/<int:loan_id>/status", methods=["POST"])
@login_required
@admin_required
def update_loan_status(loan_id):
    loan = Loan.query.get(loan_id)
    if not loan:
        flash("Loan not found.", "error")
        return redirect(url_for("admin_dashboard"))

    loan.status = request.form["status"]
    db.session.commit()
    flash(f"Loan #{loan.id} set to {loan.status}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/fraud-alerts/<int:alert_id>/resolve", methods=["POST"])
@login_required
@admin_required
def resolve_alert(alert_id):
    alert = FraudAlert.query.get(alert_id)
    if not alert:
        flash("Alert not found.", "error")
        return redirect(url_for("admin_dashboard"))

    alert.resolved = True
    db.session.commit()
    flash("Fraud alert resolved.", "success")
    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)