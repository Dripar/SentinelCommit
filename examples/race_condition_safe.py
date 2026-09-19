"""
Hazard class 3/3: RACE_CONDITION  --  expected verdict: PASS

Both check-then-act windows are closed by making the check and the act a single
atomic operation rather than two steps:

1. Overselling -- the seat guard moves into the UPDATE's WHERE clause
   (`AND seats_remaining > 0`). The database evaluates the condition and
   performs the decrement in one statement, under a row lock it takes itself.
   A zero `rowcount` means another request won the race, and we surface that
   as "sold out" instead of writing a negative count. `SELECT ... FOR UPDATE`
   would be the equivalent fix where the guard cannot be expressed inline.

2. Duplicate references -- the in-process counter is replaced by a database
   sequence, which is transactional and concurrency-safe by construction.
   A `threading.Lock` would only have fixed this within one process; a
   sequence is also correct across the multiple workers that any real
   deployment runs.
"""


class SoldOut(Exception):
    """Raised when no seats remain for the event."""


def reserve_seat(db, event_id: int, user_id: int) -> str:
    """Reserve one seat for an event, safely under concurrency."""
    with db.transaction():
        # Check and act in a single atomic statement, guarded by the database.
        claimed = db.execute(
            "UPDATE events SET seats_remaining = seats_remaining - 1 "
            "WHERE id = %s AND seats_remaining > 0",
            (event_id,),
        ).rowcount
        if claimed == 0:
            raise SoldOut(f"event {event_id} is sold out")

        # A sequence is atomic across threads *and* across worker processes.
        reference = db.execute(
            "SELECT 'BK-' || LPAD(nextval('booking_reference_seq')::text, 8, '0')"
        ).scalar()

        db.execute(
            "INSERT INTO bookings (event_id, user_id, reference) "
            "VALUES (%s, %s, %s)",
            (event_id, user_id, reference),
        )

    return reference
