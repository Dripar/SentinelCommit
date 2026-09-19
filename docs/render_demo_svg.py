#!/usr/bin/env python3
"""
Render the demo terminal sessions in docs/DEMO.md to SVG.

GitHub renders SVG inline in Markdown, so these stay crisp at any zoom and
remain diffable in review, unlike a PNG screenshot. The content is a faithful
transcription of the real output captured in docs/DEMO.md.

Usage:  python docs/render_demo_svg.py
Writes: docs/images/commit-blocked.svg, docs/images/commit-passed.svg
"""

import os

# GitHub dark palette, so the images sit naturally in a README.
BG = "#0d1117"
FG = "#c9d1d9"
DIM = "#8b949e"
RED = "#f85149"
GREEN = "#3fb950"
YELLOW = "#d29922"
CYAN = "#39c5cf"
PROMPT = "#7ee787"

CHAR_W = 8.4
LINE_H = 20
FONT_SIZE = 14
PAD_X = 20
PAD_Y = 34

# A line is a list of (text, colour, bold) segments. () means a blank line.
BLOCKED = [
    [("$ ", PROMPT, 0), ("cp examples/transaction_fault_unsafe.py payment_service.py", FG, 0)],
    [("$ ", PROMPT, 0), ("git add payment_service.py", FG, 0)],
    [("$ ", PROMPT, 0), ('git commit -m "Add payment deduction logic"', FG, 0)],
    [],
    [("[SentinelCommit] Auditing 1 staged file(s) via Claude (claude-opus-5)...", CYAN, 1)],
    [],
    [("=" * 68, RED, 1)],
    [("  [COMMIT REJECTED]   Hazard: TRANSACTION_FAULT   Severity: CRITICAL", RED, 1)],
    [("=" * 68, RED, 1)],
    [],
    [("  File: ", DIM, 0), ("payment_service.py", CYAN, 1)],
    [],
    [("  Why this is unsafe", YELLOW, 1)],
    [("    The balance is debited and committed before the payment gateway", FG, 0)],
    [("    is called. If the gateway raises or times out, the debit is", FG, 0)],
    [("    already durable and no compensating action runs, so the customer", FG, 0)],
    [("    is debited for an order that was never placed.", FG, 0)],
    [],
    [("  Suggested remediation", GREEN, 1)],
    [("    | ", DIM, 0), ("try:", FG, 0)],
    [("    | ", DIM, 0), ("    with db.transaction():", FG, 0)],
    [("    | ", DIM, 0), ("        db.execute(", FG, 0)],
    [("    | ", DIM, 0), ('            "UPDATE users SET balance = balance - %s "', FG, 0)],
    [("    | ", DIM, 0), ('            "WHERE id = %s AND balance >= %s",', FG, 0)],
    [("    | ", DIM, 0), ("            (amount, user_id, amount),", FG, 0)],
    [("    | ", DIM, 0), ("        )", FG, 0)],
    [("    | ", DIM, 0), ("        payment_gateway.charge(user_id, amount)", FG, 0)],
    [("    | ", DIM, 0), ("except PaymentError:", FG, 0)],
    [("    | ", DIM, 0), ("    db.rollback()", FG, 0)],
    [("    | ", DIM, 0), ("    raise", FG, 0)],
    [],
    [("  Fix and re-stage, or bypass with: git commit --no-verify", DIM, 0)],
    [("=" * 68, RED, 1)],
    [],
    [("$ ", PROMPT, 0), ("git log --oneline -1", FG, 0)],
    [("710d81e docs: touch examples readme", FG, 0),
     ("   <- no commit was created", DIM, 0)],
]

PASSED = [
    [("$ ", PROMPT, 0), ("cp examples/transaction_fault_safe.py payment_service.py", FG, 0)],
    [("$ ", PROMPT, 0), ("git add payment_service.py", FG, 0)],
    [("$ ", PROMPT, 0),
     ('git commit -m "Add payment deduction logic with transactional rollback"', FG, 0)],
    [],
    [("[SentinelCommit] Auditing 1 staged file(s) via Claude (claude-opus-5)...", CYAN, 1)],
    [("[SentinelCommit] All semantic checks passed.", GREEN, 1)],
    [("[feat/semantic-pre-commit-guardrail 545feca] Add payment deduction logic "
      "with transactional rollback", FG, 0)],
    [(" 1 file changed, 46 insertions(+)", FG, 0)],
    [(" create mode 100644 payment_service.py", FG, 0)],
    [],
    [("$ ", PROMPT, 0), ("git log --oneline -1", FG, 0)],
    [("545feca Add payment deduction logic with transactional rollback", FG, 0),
     ("   <- committed", DIM, 0)],
]


def escape(text):
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def render(lines, title, accent):
    widest = max((sum(len(t) for t, _, _ in line) for line in lines if line), default=0)
    width = int(widest * CHAR_W) + PAD_X * 2
    height = len(lines) * LINE_H + PAD_Y + PAD_Y // 2

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        f'<rect width="{width}" height="{height}" rx="8" fill="{BG}"/>',
        f'<rect width="{width}" height="26" rx="8" fill="#161b22"/>',
        f'<rect y="18" width="{width}" height="8" fill="#161b22"/>',
        '<circle cx="16" cy="13" r="5" fill="#ff5f56"/>',
        '<circle cx="34" cy="13" r="5" fill="#ffbd2e"/>',
        '<circle cx="52" cy="13" r="5" fill="#27c93f"/>',
        f'<text x="70" y="17" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,'
        f'monospace" font-size="11" fill="{accent}">{escape(title)}</text>',
        '<g font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
        f'font-size="{FONT_SIZE}">',
    ]

    for row, line in enumerate(lines):
        y = PAD_Y + row * LINE_H
        x = PAD_X
        for text, colour, bold in line:
            weight = ' font-weight="bold"' if bold else ""
            out.append(
                f'<text x="{x:.1f}" y="{y}" fill="{colour}"{weight} '
                f'xml:space="preserve">{escape(text)}</text>'
            )
            x += len(text) * CHAR_W

    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "images")
    os.makedirs(out_dir, exist_ok=True)

    targets = [
        ("commit-blocked.svg", BLOCKED, "SentinelCommit - commit blocked", RED),
        ("commit-passed.svg", PASSED, "SentinelCommit - commit passed", GREEN),
    ]
    for name, lines, title, accent in targets:
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(lines, title, accent))
        print("wrote", os.path.relpath(path, os.path.dirname(here)))


if __name__ == "__main__":
    main()
