import random


def get_mock_credit_score(customer):
    """
    Mock credit score generator (stand-in for a real Credit Score API,
    which the project brief explicitly allows).
    """
    base_score = 650

    for loan in customer.loans:
        if loan.status == "closed":
            base_score += 20
        elif loan.status == "approved" and loan.amount_repaid < loan.amount * 0.5:
            base_score -= 10
        elif loan.status == "rejected":
            base_score -= 5

    base_score += random.randint(-15, 15)

    return max(300, min(900, base_score))