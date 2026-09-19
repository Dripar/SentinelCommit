#!/usr/bin/env python3
"""
SentinelCommit - AI-powered semantic pre-commit guardrail.

Two-tier gate on `git commit`:
  1. Deterministic gate (zero tokens): skip empty diffs and docs-only changes.
  2. Semantic gate (Claude): analyse the staged diff for high-severity runtime
     hazards that linters and type checkers structurally cannot see.

Exit codes:
  0  commit allowed (safe, skipped, or fail-open on infrastructure error)
  1  commit blocked (semantic hazard detected)
"""

import json
import os
import subprocess
import sys

MODEL = os.environ.get("SENTINEL_MODEL", "claude-opus-5")
EFFORT = os.environ.get("SENTINEL_EFFORT", "medium")
TIMEOUT_S = float(os.environ.get("SENTINEL_TIMEOUT", "45"))
MAX_DIFF_CHARS = int(os.environ.get("SENTINEL_MAX_DIFF", "60000"))

# Extensions that cannot contain a runtime hazard -> never worth a token.
DOC_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc", ".markdown",
    ".json", ".lock", ".cfg", ".ini", ".toml", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf",
}

# Extensionless files and dotfiles never match DOC_EXTENSIONS, because
# os.path.splitext("LICENSE") and splitext(".gitignore") both return an empty
# extension. Those are matched by name instead. Anything not listed here is
# treated as code and audited, which is the safe default.
DOC_FILENAMES = {
    "license", "licence", "copying", "notice", "authors", "contributors",
    "changelog", "codeowners", "readme",
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
    ".env.example", ".npmrc", ".nvmrc", ".python-version",
}


# --------------------------------------------------------------------------
# Terminal formatting
# --------------------------------------------------------------------------

def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    # Git may hand a hook a non-tty stdout; the hook sets this so the demo
    # banners still render in colour.
    if os.environ.get("SENTINEL_FORCE_COLOR") == "1":
        try:
            import colorama
            colorama.just_fix_windows_console()
        except Exception:
            pass
        return True
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        try:
            import colorama
            colorama.just_fix_windows_console()
            return True
        except Exception:
            # Windows Terminal and Git Bash handle ANSI natively; legacy conhost won't.
            return os.environ.get("WT_SESSION") is not None or "TERM" in os.environ
    return True


_COLOR = _supports_color()


def _c(code, text):
    return "\033[" + code + "m" + text + "\033[0m" if _COLOR else text


def red(t):
    return _c("1;31", t)


def green(t):
    return _c("1;32", t)


def yellow(t):
    return _c("1;33", t)


def cyan(t):
    return _c("1;36", t)


def dim(t):
    return _c("2", t)


# --------------------------------------------------------------------------
# Tier 1: deterministic gate
# --------------------------------------------------------------------------

def _git(*args):
    result = subprocess.run(
        ["git"] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + " failed: " + result.stderr.strip())
    return result.stdout


def get_staged_files():
    return [line for line in _git("diff", "--cached", "--name-only").splitlines() if line]


def get_staged_diff():
    # Exclude lockfiles and vendored trees so a dependency bump doesn't burn tokens.
    return _git(
        "diff", "--cached", "--no-color", "--unified=8",
        "--", ".",
        ":(exclude)*.lock",
        ":(exclude)package-lock.json",
        ":(exclude)node_modules/**",
        ":(exclude)venv/**",
    ).strip()


def is_docs_only(files):
    """True when nothing staged can plausibly contain a runtime hazard."""
    for path in files:
        name = os.path.basename(path).lower()
        if name in DOC_FILENAMES:
            continue
        if os.path.splitext(name)[1] in DOC_EXTENSIONS:
            continue
        return False
    return True


# --------------------------------------------------------------------------
# Tier 2: semantic gate
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a staff engineer performing a pre-commit review of a git diff.

You hunt for exactly three classes of high-severity defect that linters and type
checkers cannot detect, because each is a property of runtime behaviour rather
than syntax:

1. TRANSACTION_FAULT - a database write (or other durable state mutation) whose
   enclosing operation can fail partway through without a rollback or
   compensating action, leaving persisted state inconsistent. The classic shape
   is a durable local write followed by an unguarded network or third-party call.

2. IDEMPOTENCY_RISK - a handler that can legitimately be delivered more than
   once (webhook, payment capture, message-queue consumer, retried job) but
   applies its side effect unconditionally, with no de-duplication key,
   uniqueness constraint, or already-processed check.

3. RACE_CONDITION - unsynchronised access to shared mutable state, or a
   check-then-act (TOCTOU) sequence where the checked condition can change
   before it is acted upon. A read-modify-write on a shared counter or balance
   without a lock, an atomic operation, or a row-level SELECT ... FOR UPDATE counts.

Rules that keep you useful rather than annoying:
- Judge only the ADDED lines (prefixed +). Surrounding context is background.
- Set should_block=true ONLY for a concrete, demonstrable risk of data
  corruption, financial loss, or a production outage. Severity must be HIGH or
  CRITICAL to justify blocking.
- Style, naming, formatting, missing docstrings, absent tests, performance nits
  and general "could be better" observations are NOT blocking. If that is all
  you find, return should_block=false with hazard_type NONE.
- Code that lives under a tests/ or spec/ directory, or whose body is purely
  assertions and mock setup, is not a production path - do not block it for
  hazards that only matter in production. Judge this by the code itself, not
  by the filename.
- If uncertain, do not block. A false positive that stops a developer mid-flow
  is worse than a missed low-confidence finding.

When you do block, suggested_fix must be a concrete, ready-to-paste code snippet
in the same language as the diff that actually remediates the defect - not prose
describing what to do."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "should_block": {
            "type": "boolean",
            "description": "True only for a HIGH or CRITICAL runtime hazard.",
        },
        "severity": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        },
        "hazard_type": {
            "type": "string",
            "enum": [
                "TRANSACTION_FAULT",
                "IDEMPOTENCY_RISK",
                "RACE_CONDITION",
                "NONE",
            ],
        },
        "file": {
            "type": "string",
            "description": "Path of the offending file, or empty string if none.",
        },
        "reason": {
            "type": "string",
            "description": "Two or three sentences naming the exact failure sequence.",
        },
        "suggested_fix": {
            "type": "string",
            "description": "Ready-to-paste remediated code, or empty string if none.",
        },
    },
    "required": [
        "should_block", "severity", "hazard_type", "file", "reason", "suggested_fix",
    ],
    "additionalProperties": False,
}

MOCK_FIX = """try:
    with db.transaction():
        db.execute(
            "UPDATE users SET balance = balance - %s "
            "WHERE id = %s AND balance >= %s",
            (amount, user_id, amount),
        )
        payment_gateway.charge(user_id, amount)
except PaymentError:
    db.rollback()
    raise"""


def _first_changed_path(diff_text):
    """Pull the first file path out of a unified diff, for reporting."""
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            return line[len("+++ b/"):].strip()
    return ""


def mock_verdict(diff_text):
    """Offline verdict for demos on unreliable venue Wi-Fi (SENTINEL_MOCK=1)."""
    # Skip the `+++ b/path` header: it starts with '+' but is not added code,
    # and a filename containing a marker word would otherwise skew the verdict.
    added = "\n".join(
        l for l in diff_text.splitlines()
        if l.startswith("+") and not l.startswith("+++")
    ).lower()
    remediated = any(
        marker in added
        for marker in ("rollback", "idempotency_key", "for update", "with lock",
                       "db.transaction()", "select ... for update")
    )
    if remediated:
        return {
            "should_block": False,
            "severity": "LOW",
            "hazard_type": "NONE",
            "file": "",
            "reason": "Durable writes are wrapped in an explicit transaction with a "
                      "rollback on the failure path. No unguarded side effects found.",
            "suggested_fix": "",
        }
    return {
        "should_block": True,
        "severity": "CRITICAL",
        "hazard_type": "TRANSACTION_FAULT",
        "file": _first_changed_path(diff_text),
        "reason": "The balance is debited and committed before the payment gateway is "
                  "called. If the gateway raises or times out, the debit is already "
                  "durable and no compensating action runs, so the customer is debited "
                  "for an order that was never placed.",
        "suggested_fix": MOCK_FIX,
    }


def analyze_diff(diff_text):
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=1)

    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + "\n\n[diff truncated by SentinelCommit]"

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        messages=[{
            "role": "user",
            "content": "Review this staged git diff:\n\n```diff\n" + diff_text + "\n```",
        }],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to review this diff")

    # output_config.format guarantees a text block holding schema-valid JSON.
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

BAR = "=" * 68


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def report_blocked(v):
    print()
    print(red(BAR))
    print(red("  [COMMIT REJECTED]   Hazard: " + str(v.get("hazard_type"))
              + "   Severity: " + str(v.get("severity"))))
    print(red(BAR))
    if v.get("file"):
        print("\n  " + dim("File:") + " " + cyan(v["file"]))
    print("\n  " + yellow("Why this is unsafe"))
    for line in _wrap(v.get("reason", ""), 64):
        print("    " + line)
    fix = (v.get("suggested_fix") or "").strip()
    if fix:
        print("\n  " + green("Suggested remediation"))
        for line in fix.splitlines():
            print("    " + dim("|") + " " + line)
    print("\n  " + dim("Fix and re-stage, or bypass with: git commit --no-verify"))
    print(red(BAR))
    print()


def report_passed(v):
    detail = ""
    if v.get("hazard_type") != "NONE" and v.get("severity") in ("LOW", "MEDIUM"):
        detail = dim("  (noted, non-blocking: " + str(v.get("hazard_type")) + ")")
    print(green("[SentinelCommit] All semantic checks passed.") + detail)


def allow(message):
    """Fail open: never hold a developer hostage to our own infrastructure."""
    print(yellow("[SentinelCommit] " + message + " - skipping audit, commit allowed."))
    sys.exit(0)


# --------------------------------------------------------------------------

def main():
    try:
        files = get_staged_files()
        diff = get_staged_diff()
    except Exception as exc:
        allow("could not read staged changes (" + str(exc) + ")")
        return

    # --- Tier 1: deterministic gate, zero tokens ---------------------------
    if not files or not diff:
        print(dim("[SentinelCommit] No staged code changes - nothing to audit."))
        sys.exit(0)

    if is_docs_only(files):
        print(dim("[SentinelCommit] Docs/config only ("
                  + str(len(files)) + " file(s)) - no API call made."))
        sys.exit(0)

    mock = os.environ.get("SENTINEL_MOCK") == "1"
    has_creds = bool(os.environ.get("ANTHROPIC_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    if not mock and not has_creds:
        allow("ANTHROPIC_API_KEY not set")
        return

    # --- Tier 2: semantic gate --------------------------------------------
    label = "cached verdict [MOCK]" if mock else "Claude (" + MODEL + ")"
    print(cyan("[SentinelCommit] Auditing " + str(len(files))
               + " staged file(s) via " + label + "..."))

    try:
        verdict = mock_verdict(diff) if mock else analyze_diff(diff)
    except Exception as exc:
        allow("audit unavailable (" + type(exc).__name__ + ": " + str(exc) + ")")
        return

    if verdict.get("should_block"):
        report_blocked(verdict)
        sys.exit(1)

    report_passed(verdict)
    sys.exit(0)


if __name__ == "__main__":
    main()
