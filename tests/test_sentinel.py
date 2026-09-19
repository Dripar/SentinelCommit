"""
Unit tests for the deterministic (Tier 1) gate and the offline mock verdict.

The Tier 2 gate is not covered here: it calls a live model, so it is exercised
by the scenarios in examples/README.md rather than in CI.

Run with:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentinel  # noqa: E402


class TestDocsOnlyGate(unittest.TestCase):
    """Tier 1 decides whether a diff is worth spending a token on."""

    def test_markdown_only_is_docs(self):
        self.assertTrue(sentinel.is_docs_only(["README.md", "docs/guide.md"]))

    def test_extensionless_licence_is_docs(self):
        # os.path.splitext("LICENSE") -> ("LICENSE", ""), so an extension
        # lookup alone would misclassify this as code and bill for it.
        self.assertTrue(sentinel.is_docs_only(["LICENSE"]))

    def test_dotfiles_are_docs(self):
        # splitext(".gitignore") -> (".gitignore", "") - same trap.
        self.assertTrue(sentinel.is_docs_only([".gitignore", ".gitattributes"]))

    def test_nested_paths_match_on_basename(self):
        self.assertTrue(sentinel.is_docs_only(["a/b/c/CHANGELOG", "x/y/.editorconfig"]))

    def test_case_is_insensitive(self):
        self.assertTrue(sentinel.is_docs_only(["Licence", "ReadMe.MD"]))

    def test_python_is_code(self):
        self.assertFalse(sentinel.is_docs_only(["payment_service.py"]))

    def test_one_code_file_taints_the_batch(self):
        self.assertFalse(sentinel.is_docs_only(["README.md", "LICENSE", "app.py"]))

    def test_unknown_extensionless_file_is_treated_as_code(self):
        # Safe default: audit anything we do not positively recognise.
        self.assertFalse(sentinel.is_docs_only(["Dockerfile"]))
        self.assertFalse(sentinel.is_docs_only(["scripts/deploy"]))

    def test_empty_list_is_vacuously_docs(self):
        self.assertTrue(sentinel.is_docs_only([]))


class TestMockVerdict(unittest.TestCase):
    """SENTINEL_MOCK=1 must drive both demo scenarios with no network."""

    def test_unguarded_write_blocks(self):
        diff = (
            "+def process_order(db, gateway, user_id, amount):\n"
            "+    db.execute('UPDATE users SET balance = balance - %s', (amount,))\n"
            "+    db.commit()\n"
            "+    gateway.charge(user_id, amount)\n"
        )
        verdict = sentinel.mock_verdict(diff)
        self.assertTrue(verdict["should_block"])
        self.assertEqual(verdict["hazard_type"], "TRANSACTION_FAULT")
        self.assertTrue(verdict["suggested_fix"].strip())

    def test_transactional_write_passes(self):
        diff = (
            "+def process_order(db, gateway, user_id, amount):\n"
            "+    try:\n"
            "+        with db.transaction():\n"
            "+            db.execute('UPDATE users SET balance = balance - %s', (amount,))\n"
            "+            gateway.charge(user_id, amount)\n"
            "+    except Exception:\n"
            "+        db.rollback()\n"
            "+        raise\n"
        )
        verdict = sentinel.mock_verdict(diff)
        self.assertFalse(verdict["should_block"])
        self.assertEqual(verdict["hazard_type"], "NONE")

    def test_only_added_lines_are_considered(self):
        # A rollback that is being REMOVED must not count as remediation.
        diff = (
            "-        db.rollback()\n"
            "+        pass\n"
        )
        self.assertTrue(sentinel.mock_verdict(diff)["should_block"])

    def test_verdict_matches_the_declared_schema(self):
        required = set(sentinel.VERDICT_SCHEMA["required"])
        for diff in ("+db.commit()", "+with db.transaction(): pass"):
            verdict = sentinel.mock_verdict(diff)
            self.assertEqual(set(verdict), required)
            self.assertIn(
                verdict["hazard_type"],
                sentinel.VERDICT_SCHEMA["properties"]["hazard_type"]["enum"],
            )
            self.assertIn(
                verdict["severity"],
                sentinel.VERDICT_SCHEMA["properties"]["severity"]["enum"],
            )


class TestMockClassifiesEveryExample(unittest.TestCase):
    """
    Each examples/*_unsafe.py must map to its own hazard class, and each
    *_safe.py must clear. This caught a real ordering bug: `event_id` is a
    parameter in the race-condition example, so it matched the idempotency
    rule first and reported the wrong hazard.
    """

    EXAMPLES = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")

    CASES = [
        ("transaction_fault", "TRANSACTION_FAULT"),
        ("idempotency", "IDEMPOTENCY_RISK"),
        ("race_condition", "RACE_CONDITION"),
    ]

    def _diff_for(self, name):
        path = os.path.join(self.EXAMPLES, name + ".py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        header = "+++ b/examples/" + name + ".py\n"
        return header + "\n".join("+" + l for l in body.splitlines())

    def test_unsafe_examples_block_with_the_right_hazard(self):
        for stem, hazard in self.CASES:
            with self.subTest(example=stem):
                verdict = sentinel.mock_verdict(self._diff_for(stem + "_unsafe"))
                self.assertTrue(verdict["should_block"])
                self.assertEqual(verdict["hazard_type"], hazard)
                self.assertTrue(verdict["suggested_fix"].strip())

    def test_safe_examples_clear(self):
        for stem, _ in self.CASES:
            with self.subTest(example=stem):
                verdict = sentinel.mock_verdict(self._diff_for(stem + "_safe"))
                self.assertFalse(verdict["should_block"])
                self.assertEqual(verdict["hazard_type"], "NONE")

    def test_blocked_verdicts_name_the_file(self):
        verdict = sentinel.mock_verdict(self._diff_for("transaction_fault_unsafe"))
        self.assertEqual(verdict["file"], "examples/transaction_fault_unsafe.py")

    def test_fallback_rule_has_no_signals(self):
        # The last rule must match unconditionally, or some diffs get no verdict.
        self.assertEqual(sentinel.MOCK_RULES[-1][2], ())
        self.assertEqual(sentinel.MOCK_RULES[-1][0], "TRANSACTION_FAULT")

    def test_every_rule_has_a_clear_reason(self):
        for rule in sentinel.MOCK_RULES:
            self.assertIn(rule[0], sentinel.CLEAR_REASON)


class TestAuditLog(unittest.TestCase):
    def test_logging_can_be_disabled(self):
        os.environ["SENTINEL_AUDIT_LOG"] = "off"
        try:
            self.assertIsNone(sentinel.audit_log_path())
        finally:
            del os.environ["SENTINEL_AUDIT_LOG"]

    def test_explicit_path_is_honoured(self):
        os.environ["SENTINEL_AUDIT_LOG"] = "/tmp/custom-audit.jsonl"
        try:
            self.assertEqual(sentinel.audit_log_path(), "/tmp/custom-audit.jsonl")
        finally:
            del os.environ["SENTINEL_AUDIT_LOG"]

    def test_record_never_raises_on_an_unwritable_path(self):
        os.environ["SENTINEL_AUDIT_LOG"] = "/nonexistent-dir/nope/audit.jsonl"
        try:
            sentinel.record("passed", files=["a.py"])  # must not raise
        finally:
            del os.environ["SENTINEL_AUDIT_LOG"]


class TestTextWrapping(unittest.TestCase):
    def test_never_returns_empty_list(self):
        self.assertEqual(sentinel._wrap("", 40), [""])

    def test_respects_width(self):
        text = "the balance is debited before the gateway is called and never reversed"
        self.assertTrue(all(len(line) <= 30 for line in sentinel._wrap(text, 30)))

    def test_preserves_every_word(self):
        text = "one two three four five six seven"
        self.assertEqual(" ".join(sentinel._wrap(text, 9)), text)


if __name__ == "__main__":
    unittest.main()
