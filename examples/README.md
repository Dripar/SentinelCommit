# Example use cases

Each hazard class the engine detects has a matching `*_unsafe.py` / `*_safe.py`
pair. Every pair is written so that **both files pass `ruff`, `flake8`, `mypy`
and `pylint` cleanly** — the defect is in runtime behaviour, not in syntax or
types, which is precisely the gap SentinelCommit exists to cover.

| Hazard | Unsafe | Safe | Scenario |
| --- | --- | --- | --- |
| `TRANSACTION_FAULT` | [`transaction_fault_unsafe.py`](transaction_fault_unsafe.py) | [`transaction_fault_safe.py`](transaction_fault_safe.py) | Balance debited and committed *before* the payment gateway is called |
| `IDEMPOTENCY_RISK` | [`idempotency_unsafe.py`](idempotency_unsafe.py) | [`idempotency_safe.py`](idempotency_safe.py) | At-least-once payment webhook credits a wallet with no de-duplication key |
| `RACE_CONDITION` | [`race_condition_unsafe.py`](race_condition_unsafe.py) | [`race_condition_safe.py`](race_condition_safe.py) | Check-then-act seat reservation oversells; non-atomic counter duplicates references |

## Try one

From the repository root, with the hook installed:

```bash
cp examples/transaction_fault_unsafe.py payment_service.py
git add payment_service.py
git commit -m "Add order payment flow"
```

Expected — the commit is refused and a remediation patch is printed:

```
====================================================================
  [COMMIT REJECTED]   Hazard: TRANSACTION_FAULT   Severity: CRITICAL
====================================================================

  File: payment_service.py

  Why this is unsafe
    The balance is debited and committed before the payment gateway is
    called. If the gateway raises or times out the debit is already
    durable and no compensating action runs, so the customer is debited
    for an order that was never placed.

  Suggested remediation
    | try:
    |     with db.transaction():
    ...

  Fix and re-stage, or bypass with: git commit --no-verify
====================================================================
```

Now apply the fix and retry:

```bash
cp examples/transaction_fault_safe.py payment_service.py
git add payment_service.py
git commit -m "Add order payment flow"
```

```
[SentinelCommit] All semantic checks passed.
[feat/... 1a2b3c4] Add order payment flow
```

> **Note:** `git add` stages, it does not *un*stage. If you experiment with
> several examples in a row, run `git reset` between them so a previously
> staged unsafe file is not still part of the diff being audited.

## Why a linter cannot find these

| | Linter / type checker | SentinelCommit |
| --- | --- | --- |
| Undefined name, bad type, unused import | ✅ | not its job |
| Durable write before an unguarded network call | ❌ | ✅ |
| Handler that is not safe to deliver twice | ❌ | ✅ |
| Check-then-act window between a read and a write | ❌ | ✅ |

A linter reasons about one file's syntax tree. Each defect above needs a model
of what happens *at runtime, under partial failure or concurrent execution* —
which is a semantic question, not a syntactic one.
