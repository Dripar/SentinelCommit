"""
Hazard class 2/3: IDEMPOTENCY_RISK  --  expected verdict: PASS

Idempotency is enforced by the database, not by application logic:

  * `processed_events.event_id` carries a PRIMARY KEY (or UNIQUE) constraint.
  * The INSERT of the provider's event id happens *first*, inside the same
    transaction as the side effects. `ON CONFLICT DO NOTHING` makes the second
    delivery a no-op rather than an error.
  * Because the claim and the effects share one transaction, two concurrent
    deliveries cannot both win -- the loser's transaction sees the conflict.

A prior `SELECT ... WHERE event_id = ?` check would NOT be sufficient: two
concurrent deliveries could both read "not processed" before either writes.
The uniqueness constraint is what makes this correct under concurrency.

The email is sent only after the transaction commits, so a rollback cannot
leave a customer notified about a credit that was reversed.
"""

from decimal import Decimal


def handle_payment_succeeded(db, mailer, event: dict) -> None:
    """Credit a customer's wallet exactly once, however often we are called."""
    payload = event["data"]["object"]
    user_id = payload["metadata"]["user_id"]
    amount = Decimal(payload["amount_received"]) / 100

    with db.transaction():
        # Claim the event. A duplicate delivery inserts zero rows and exits.
        claimed = db.execute(
            "INSERT INTO processed_events (event_id, received_at) "
            "VALUES (%s, NOW()) ON CONFLICT (event_id) DO NOTHING",
            (event["id"],),
        ).rowcount
        if claimed == 0:
            return  # Already handled; this is a retry.

        db.execute(
            "UPDATE wallets SET balance = balance + %s WHERE user_id = %s",
            (amount, user_id),
        )
        db.execute(
            "INSERT INTO ledger (user_id, amount, kind, idempotency_key) "
            "VALUES (%s, %s, 'topup', %s)",
            (user_id, amount, event["id"]),
        )

    # Only notify once the credit is durably committed.
    mailer.send(user_id, template="topup_confirmed", amount=amount)
