"""
Hazard class 2/3: IDEMPOTENCY_RISK  --  expected verdict: BLOCK (HIGH)

Every payment provider -- Stripe, Adyen, Razorpay, PayPal -- documents at-least
once webhook delivery. A retry after a timeout, a network blip, or an operator
replaying an event from the dashboard all deliver the *same* event twice.

This handler applies its side effects unconditionally. There is no
de-duplication key, no uniqueness constraint, and no already-processed check,
so a duplicate delivery credits the wallet twice and emails the customer twice.

The bug is invisible in testing, because tests deliver each event exactly once.
It only appears in production, under exactly the conditions the provider
documents as normal.
"""

from decimal import Decimal


def handle_payment_succeeded(db, mailer, event: dict) -> None:
    """Credit a customer's wallet when a payment settles."""
    payload = event["data"]["object"]
    user_id = payload["metadata"]["user_id"]
    amount = Decimal(payload["amount_received"]) / 100

    # No check for whether event["id"] has already been processed.
    db.execute(
        "UPDATE wallets SET balance = balance + %s WHERE user_id = %s",
        (amount, user_id),
    )
    db.execute(
        "INSERT INTO ledger (user_id, amount, kind) VALUES (%s, %s, 'topup')",
        (user_id, amount),
    )
    db.commit()

    mailer.send(user_id, template="topup_confirmed", amount=amount)
