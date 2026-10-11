REJECT_BELOW = 500
APPROVE_AT_OR_ABOVE = 700
MAX_LOAN_TO_BALANCE_RATIO = 5


def recommend_loan(credit_score, loan_amount, total_balance):
    """Rule-based decision support for the admin. Returns (label, reason)."""
    if credit_score < REJECT_BELOW:
        return "Reject", "Credit score below 500"
    if credit_score >= APPROVE_AT_OR_ABOVE and loan_amount <= total_balance * MAX_LOAN_TO_BALANCE_RATIO:
        return "Approve", "Good credit score and loan is affordable for their balance"
    return "Review", "Borderline score or loan size, needs a manual check"