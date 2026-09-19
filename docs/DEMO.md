# Demo transcript and screenshot guide

Verbatim terminal output from a real run on Windows 11 (Git Bash, Git 2.47.1,
Python 3.12.10, `anthropic` 1.7.0), with ANSI colour codes stripped. Use these
as the reference for what the screenshots in the main README should show.

Everything below was produced with `SENTINEL_MOCK=1`, which is the offline demo
path — no network, no API key, identical output shape to a live run.

---

## Tier 1 — deterministic gate (zero tokens)

Nothing staged:

```console
$ git commit -m "x"
[SentinelCommit] No staged code changes - nothing to audit.
```

Docs and config only — no API call is made at all:

```console
$ git add README.md LICENSE .gitignore .gitattributes
$ git commit -m "docs: add README, licence, gitignore and gitattributes"
[SentinelCommit] Docs/config only (4 file(s)) - no API call made.
[feat/semantic-pre-commit-guardrail ce350f0] docs: add README, licence, gitignore and gitattributes
 4 files changed, 348 insertions(+), 1 deletion(-)
```

---

## Scenario A — unsafe commit is refused

**→ This is SCREENSHOT_1.**

```console
$ cp examples/transaction_fault_unsafe.py payment_service.py
$ git add payment_service.py
$ git commit -m "Add payment deduction logic"

[SentinelCommit] Auditing 1 staged file(s) via cached verdict [MOCK]...

====================================================================
  [COMMIT REJECTED]   Hazard: TRANSACTION_FAULT   Severity: CRITICAL
====================================================================

  File: payment_service.py

  Why this is unsafe
    The balance is debited and committed before the payment gateway
    is called. If the gateway raises or times out, the debit is
    already durable and no compensating action runs, so the customer
    is debited for an order that was never placed.

  Suggested remediation
    | try:
    |     with db.transaction():
    |         db.execute(
    |             "UPDATE users SET balance = balance - %s "
    |             "WHERE id = %s AND balance >= %s",
    |             (amount, user_id, amount),
    |         )
    |         payment_gateway.charge(user_id, amount)
    | except PaymentError:
    |     db.rollback()
    |     raise

  Fix and re-stage, or bypass with: git commit --no-verify
====================================================================
```

Exit status `1`, and `git log` confirms no commit was created.

---

## Scenario B — remediated commit passes

**→ This is SCREENSHOT_2.**

```console
$ git reset
$ cp examples/transaction_fault_safe.py payment_service.py
$ git add payment_service.py
$ git commit -m "Add payment deduction logic with transactional rollback"

[SentinelCommit] Auditing 1 staged file(s) via cached verdict [MOCK]...
[SentinelCommit] All semantic checks passed.
[feat/semantic-pre-commit-guardrail 545feca] Add payment deduction logic with transactional rollback
 1 file changed, 46 insertions(+)
 create mode 100644 payment_service.py
```

---

## Resilience — fail open with no credentials

An unsafe diff, with `ANTHROPIC_API_KEY` unset. The hazard is real, but the
tool cannot reach the model, so it must **allow** the commit rather than block
on its own unavailability:

```console
$ unset ANTHROPIC_API_KEY
$ git add payment_service.py
$ python sentinel.py
[SentinelCommit] ANTHROPIC_API_KEY not set - skipping audit, commit allowed.
$ echo $?
0
```

---

## Unit tests

```console
$ python -m unittest discover -s tests -v
test_case_is_insensitive (test_sentinel.TestDocsOnlyGate) ... ok
test_dotfiles_are_docs (test_sentinel.TestDocsOnlyGate) ... ok
test_empty_list_is_vacuously_docs (test_sentinel.TestDocsOnlyGate) ... ok
test_extensionless_licence_is_docs (test_sentinel.TestDocsOnlyGate) ... ok
test_markdown_only_is_docs (test_sentinel.TestDocsOnlyGate) ... ok
test_nested_paths_match_on_basename (test_sentinel.TestDocsOnlyGate) ... ok
test_one_code_file_taints_the_batch (test_sentinel.TestDocsOnlyGate) ... ok
test_python_is_code (test_sentinel.TestDocsOnlyGate) ... ok
test_unknown_extensionless_file_is_treated_as_code (test_sentinel.TestDocsOnlyGate) ... ok
test_only_added_lines_are_considered (test_sentinel.TestMockVerdict) ... ok
test_transactional_write_passes (test_sentinel.TestMockVerdict) ... ok
test_unguarded_write_blocks (test_sentinel.TestMockVerdict) ... ok
test_verdict_matches_the_declared_schema (test_sentinel.TestMockVerdict) ... ok
test_never_returns_empty_list (test_sentinel.TestTextWrapping) ... ok
test_preserves_every_word (test_sentinel.TestTextWrapping) ... ok
test_respects_width (test_sentinel.TestTextWrapping) ... ok

----------------------------------------------------------------------
Ran 16 tests in 0.003s

OK
```

---

## Bugs this exercise actually caught

Both were found by running the scenarios above, not by reading the code — worth
mentioning if a judge asks how it was tested.

1. **The hook blocked every commit on Windows.** Interpreter selection used
   `command -v python3`, which on Windows resolves to the Microsoft Store stub
   `python3.exe`. That stub exists, is executable, prints "Python was not
   found" and exits non-zero — so Git treated it as a hook failure and refused
   the commit. Fixed by *probing* each candidate (`python -c "import sys"`)
   instead of trusting the lookup, and by treating any exit status other than
   `0`/`1` as fail-open.

2. **Docs-only commits were billed for an API call.**
   `os.path.splitext("LICENSE")` and `os.path.splitext(".gitignore")` both
   return an empty extension, so extensionless files and dotfiles fell straight
   through the zero-token gate. Fixed with a filename allowlist checked before
   the extension lookup. `tests/test_sentinel.py` covers both cases.

---

## Images

The README embeds `images/commit-blocked.svg` and `images/commit-passed.svg`,
which are generated from the transcript above by
[`render_demo_svg.py`](render_demo_svg.py):

```bash
python docs/render_demo_svg.py
```

SVG rather than PNG on purpose: it stays sharp at any zoom, renders inline on
GitHub, and shows up as a readable diff in review instead of an opaque binary
blob. Edit the `BLOCKED` / `PASSED` line lists in the generator and re-run to
update them.

### If you want real photographic screenshots instead

The rendered images reproduce output captured with `SENTINEL_MOCK=1`. To
capture a genuine live-API run:

1. Set a real key so the verdict comes from the model, not the cache:
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   unset SENTINEL_MOCK
   ```
2. Raise the terminal font to 18pt or more — projectors flatten anything
   smaller.
3. Run Scenario A, capture the red banner in full; run Scenario B, capture the
   green pass line and the resulting commit.
4. Save them under `images/` and point the two `![...]` lines in the main
   [README](../README.md) at your files instead of the generated SVGs.
