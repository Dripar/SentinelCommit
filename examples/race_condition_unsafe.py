"""
Hazard class 3/3: RACE_CONDITION  --  expected verdict: BLOCK (HIGH)

Two independent check-then-act (TOCTOU) windows, both invisible to a linter
because each line is correct in isolation and only their *interleaving* is
wrong.

1. `reserve_seat` reads the remaining seat count, decides, then writes. Two
   concurrent requests can both read `remaining = 1`, both pass the check, and
   both write -- overselling the event. The read and the write are separate
   statements with no lock between them.

2. `_next_reference` increments a module-level counter without a lock. CPython's
   GIL does not make `+= 1` atomic: it compiles to a load, an add, and a store,
   and a thread switch between the load and the store loses an increment,
   handing two bookings the same reference.
"""

_reference_counter = 0


def _next_reference() -> str:
    """Allocate a booking reference."""
    global _reference_counter
    _reference_counter += 1  # load, add, store -- not atomic
    return f"BK-{_reference_counter:08d}"


def reserve_seat(db, event_id: int, user_id: int) -> str:
    """Reserve one seat for an event."""
    remaining = db.execute(
        "SELECT seats_remaining FROM events WHERE id = %s",
        (event_id,),
    ).scalar()

    # Between this check and the write below, another request can do the same.
    if remaining < 1:
        raise ValueError("sold out")

    db.execute(
        "UPDATE events SET seats_remaining = seats_remaining - 1 WHERE id = %s",
        (event_id,),
    )

    reference = _next_reference()
    db.execute(
        "INSERT INTO bookings (event_id, user_id, reference) VALUES (%s, %s, %s)",
        (event_id, user_id, reference),
    )
    db.commit()
    return reference
