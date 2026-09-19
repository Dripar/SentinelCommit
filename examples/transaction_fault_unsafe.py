"""
Hazard class 1/3: TRANSACTION_FAULT  --  expected verdict: BLOCK (CRITICAL)

A linter sees nothing wrong here. Every name resolves, every type checks, and
there is no unreachable code. The defect is entirely in the *ordering of
durable effects*:

    1. The balance is debited and the transaction is committed.
    2. Only then is the payment gateway called.

If step 2 raises -- a timeout, a 5xx, a declined card, a dropped connection --
step 1 has already been made durable and nothing reverses it. The customer is
debited for an order that was never placed. The failure is silent, it only
manifests under partial failure, and it corrupts financial state.
"""

from decimal import Decimal


def process_order(db, payment_gateway, user_id: int, amount: Decimal) -> None:
    """Debit the customer's balance and charge their card."""
    db.execute(
        "UPDATE users SET balance = balance - %s WHERE id = %s",
        (amount, user_id),
    )
    db.commit()  # <-- durable, and now irreversible

    # If this raises, the debit above has already been committed.
    payment_gateway.charge(user_id, amount)

    db.execute(
        "INSERT INTO orders (user_id, amount, status) VALUES (%s, %s, 'paid')",
        (user_id, amount),
    )
    db.commit()
