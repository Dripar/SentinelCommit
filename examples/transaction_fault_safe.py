"""
Hazard class 1/3: TRANSACTION_FAULT  --  expected verdict: PASS

The remediation is not "add a try/except" -- it is to make the durable write
and the external call share a single atomic outcome:

  * One transaction wraps the debit, the gateway call, and the order row, so
    no partial state can survive.
  * The balance guard (`AND balance >= %s`) is enforced by the database rather
    than by a prior read, which also removes the check-then-act window.
  * A gateway failure rolls the debit back before it is ever observable.
  * The gateway call is the last durable action, so the blast radius of a
    timeout is a rollback rather than a silent inconsistency.
"""

from decimal import Decimal


class InsufficientFunds(Exception):
    """Raised when the debit would take the account below zero."""


def process_order(db, payment_gateway, user_id: int, amount: Decimal) -> None:
    """Debit the customer's balance and charge their card, atomically."""
    try:
        with db.transaction():
            # The database enforces the guard, so no check-then-act window exists.
            rows = db.execute(
                "UPDATE users SET balance = balance - %s "
                "WHERE id = %s AND balance >= %s",
                (amount, user_id, amount),
            ).rowcount
            if rows == 0:
                raise InsufficientFunds(f"user {user_id} cannot cover {amount}")

            db.execute(
                "INSERT INTO orders (user_id, amount, status) "
                "VALUES (%s, %s, 'paid')",
                (user_id, amount),
            )

            # Inside the transaction: a failure here rolls the debit back.
            payment_gateway.charge(user_id, amount)
    except Exception:
        db.rollback()
        raise
