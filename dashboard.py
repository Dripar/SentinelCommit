#!/usr/bin/env python3
"""
SentinelCommit dashboard - a single-page local view of the repository.

Shows, on one page:
  * headline stats (commits, audits, blocks, free Tier 1 skips)
  * the commit DAG, drawn as a lane graph with branch and tag refs
  * every commit, with the audit verdict that gated it
  * the full SentinelCommit audit trail, including commits that never happened

Standard library only - no Flask, no npm, no build step.

Usage:
    python dashboard.py                 # serve on http://127.0.0.1:8765
    python dashboard.py --port 9000
    python dashboard.py --no-browser
"""

import argparse
import html
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

SEP = "\x1f"  # unit separator - safe inside commit messages
REC = "\x1e"  # record separator

LANE_COLOURS = [
    "#58a6ff", "#3fb950", "#d29922", "#bc8cff",
    "#f85149", "#39c5cf", "#db6d28", "#7ee787",
]


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

def git(*args):
    result = subprocess.run(
        ["git"] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + ": " + result.stderr.strip())
    return result.stdout


def repo_root():
    return git("rev-parse", "--show-toplevel").strip()


def current_branch():
    try:
        return git("rev-parse", "--abbrev-ref", "HEAD").strip()
    except Exception:
        return "(detached)"


def load_commits(limit):
    """Read the commit DAG. Returns newest-first."""
    fmt = SEP.join(["%H", "%h", "%P", "%an", "%ae", "%aI", "%s", "%D"]) + REC
    try:
        raw = git("log", "--all", "--date-order", "-n", str(limit), "--pretty=format:" + fmt)
    except RuntimeError:
        return []  # no commits yet

    commits = []
    for chunk in raw.split(REC):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, short, parents, an, ae, when, subject, refs = chunk.split(SEP)
        commits.append({
            "sha": sha,
            "short": short,
            "parents": parents.split() if parents else [],
            "author": an,
            "email": ae,
            "date": when,
            "subject": subject,
            "refs": [r.strip() for r in refs.split(",") if r.strip()],
        })

    # Attach per-commit diff stats in one batch call rather than N calls.
    for c in commits:
        try:
            out = git("show", "--stat=200", "--format=", c["sha"])
            tail = [l for l in out.strip().splitlines() if l.strip()]
            c["stat"] = tail[-1].strip() if tail else ""
            c["files"] = max(len(tail) - 1, 0)
        except Exception:
            c["stat"], c["files"] = "", 0
    return commits


def assign_lanes(commits):
    """
    Lay the DAG out in vertical lanes, the way `git log --graph` does.

    Walking newest-first, each commit takes the leftmost lane already
    reserved for it (by a child), or the leftmost free lane. Its first parent
    inherits that lane so mainline stays straight; further parents (merges)
    branch into new lanes.
    """
    active = []          # active[i] = sha expected to appear in lane i, or None
    for c in commits:
        if c["sha"] in active:
            lane = active.index(c["sha"])
        else:
            lane = active.index(None) if None in active else len(active)
            if lane == len(active):
                active.append(None)
        c["lane"] = lane

        # Lanes this commit connects down to.
        active[lane] = None
        outgoing = []
        for i, parent in enumerate(c["parents"]):
            if parent in active:
                plane = active.index(parent)
            elif i == 0:
                plane = lane
                active[lane] = parent
            else:
                plane = active.index(None) if None in active else len(active)
                if plane == len(active):
                    active.append(None)
                active[plane] = parent
            outgoing.append(plane)
        c["outgoing"] = outgoing

        while active and active[-1] is None:
            active.pop()
    return commits


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------

def load_audit():
    path = os.environ.get("SENTINEL_AUDIT_LOG", "")
    if not path:
        try:
            path = os.path.join(git("rev-parse", "--absolute-git-dir").strip(),
                                "sentinel-audit.jsonl")
        except Exception:
            return []
    if not os.path.exists(path):
        return []

    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a partially written final line
    events.reverse()  # newest first
    return events


def summarise(commits, events):
    outcomes = {}
    for e in events:
        outcomes[e.get("outcome", "?")] = outcomes.get(e.get("outcome", "?"), 0) + 1
    hazards = {}
    for e in events:
        if e.get("outcome") == "blocked":
            h = e.get("hazard_type", "UNKNOWN")
            hazards[h] = hazards.get(h, 0) + 1
    # Only a real verdict costs a call. Mock runs reach Tier 2 but never leave
    # the machine, so counting them here would overstate spend.
    api_calls = sum(1 for e in events
                    if e.get("outcome") in ("blocked", "passed") and not e.get("mock"))
    mocked = sum(1 for e in events if e.get("mock"))
    return {
        "commits": len(commits),
        "audits": len(events),
        "blocked": outcomes.get("blocked", 0),
        "passed": outcomes.get("passed", 0),
        "skipped_docs": outcomes.get("skipped_docs", 0),
        "failed_open": outcomes.get("failed_open", 0),
        "api_calls": api_calls,
        "mocked": mocked,
        "hazards": hazards,
        "branch": current_branch(),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

ROW_H = 56
GRAPH_X0 = 22
LANE_W = 20


def e(text):
    return html.escape(str(text if text is not None else ""))


def render_graph(commits):
    """SVG DAG: one row per commit, edges drawn to each parent's row."""
    if not commits:
        return '<p class="empty">No commits yet.</p>'

    row_of = {c["sha"]: i for i, c in enumerate(commits)}
    lanes = max((c["lane"] for c in commits), default=0) + 1
    width = GRAPH_X0 + lanes * LANE_W + 10
    height = len(commits) * ROW_H + 20

    def cx(lane):
        return GRAPH_X0 + lane * LANE_W

    def cy(row):
        return row * ROW_H + ROW_H // 2

    parts = [f'<svg class="dag" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" aria-hidden="true">']

    # Edges first, so nodes sit on top.
    for i, c in enumerate(commits):
        for parent, plane in zip(c["parents"], c.get("outgoing", [])):
            j = row_of.get(parent)
            colour = LANE_COLOURS[plane % len(LANE_COLOURS)]
            x1, y1 = cx(c["lane"]), cy(i)
            if j is None:
                # Parent is outside the loaded window: stub the edge downward.
                parts.append(f'<path d="M{x1} {y1} V{y1 + ROW_H // 2}" '
                             f'stroke="{colour}" stroke-width="2" fill="none" '
                             f'stroke-dasharray="3 3" opacity=".5"/>')
                continue
            x2, y2 = cx(plane), cy(j)
            if x1 == x2:
                d = f"M{x1} {y1} V{y2}"
            else:
                mid = y1 + ROW_H // 2
                d = (f"M{x1} {y1} V{mid - 8} "
                     f"Q{x1} {mid} {x1 + (8 if x2 > x1 else -8)} {mid} "
                     f"H{x2 - (8 if x2 > x1 else -8)} "
                     f"Q{x2} {mid} {x2} {mid + 8} V{y2}")
            parts.append(f'<path d="{d}" stroke="{colour}" stroke-width="2" fill="none"/>')

    for i, c in enumerate(commits):
        colour = LANE_COLOURS[c["lane"] % len(LANE_COLOURS)]
        merge = len(c["parents"]) > 1
        parts.append(
            f'<circle cx="{cx(c["lane"])}" cy="{cy(i)}" r="{6 if merge else 5}" '
            f'fill="{"#0d1117" if merge else colour}" stroke="{colour}" stroke-width="2.5"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_ref(ref):
    cls = "ref-branch"
    if ref.startswith("HEAD"):
        cls = "ref-head"
    elif ref.startswith("tag:"):
        cls = "ref-tag"
    elif ref.startswith("origin/"):
        cls = "ref-remote"
    return f'<span class="ref {cls}">{e(ref)}</span>'


def render_rows(commits, events):
    """Commit rows, aligned 1:1 with the SVG graph rows."""
    # Match audits to commits by subject where we can - the audit fires before
    # the commit exists, so there is no sha to join on.
    passed_by_subject = {}
    for ev in reversed(events):
        if ev.get("outcome") in ("passed", "skipped_docs"):
            passed_by_subject.setdefault(ev.get("detail", ""), ev)

    out = []
    for c in commits:
        refs = "".join(render_ref(r) for r in c["refs"])
        out.append(
            f'<li class="row">'
            f'<div class="row-main">'
            f'<code class="sha">{e(c["short"])}</code>'
            f'<span class="subject">{e(c["subject"])}</span>'
            f'{refs}'
            f'</div>'
            f'<div class="row-meta">'
            f'<span>{e(c["author"])}</span>'
            f'<span class="dot">&middot;</span>'
            f'<time datetime="{e(c["date"])}">{e(c["date"][:16].replace("T", " "))}</time>'
            f'{"<span class=dot>&middot;</span><span>" + e(c["stat"]) + "</span>" if c["stat"] else ""}'
            f'</div>'
            f'</li>'
        )
    return "".join(out)


OUTCOME_META = {
    "blocked": ("blocked", "COMMIT BLOCKED"),
    "passed": ("passed", "passed"),
    "skipped_docs": ("skipped", "Tier 1 skip - no API call"),
    "failed_open": ("failopen", "failed open"),
}


def render_audit(events):
    if not events:
        return ('<p class="empty">No audits recorded yet. Make a commit with the '
                'hook installed, then refresh.</p>')
    out = []
    for ev in events:
        cls, label = OUTCOME_META.get(ev.get("outcome", ""), ("skipped", ev.get("outcome", "?")))
        hazard = ev.get("hazard_type")
        badge = (f'<span class="hazard">{e(hazard)}</span>'
                 if hazard and hazard != "NONE" else "")
        sev = (f'<span class="sev sev-{e(str(ev.get("severity","")).lower())}">'
               f'{e(ev.get("severity"))}</span>' if ev.get("severity") else "")
        mock = '<span class="mock">MOCK</span>' if ev.get("mock") else ""
        files = ev.get("files") or []
        filelist = ("".join(f'<code>{e(f)}</code>' for f in files[:6])
                    + (f'<span class="more">+{len(files) - 6}</span>' if len(files) > 6 else ""))
        reason = f'<p class="reason">{e(ev.get("reason"))}</p>' if ev.get("reason") else ""
        detail = (f'<p class="detail">{e(ev.get("detail"))}</p>'
                  if ev.get("detail") and not ev.get("reason") else "")
        fix = ev.get("suggested_fix")
        fixblock = (f'<details><summary>Suggested remediation</summary>'
                    f'<pre>{e(fix)}</pre></details>') if fix else ""
        out.append(
            f'<li class="audit {cls}">'
            f'<div class="audit-head">'
            f'<span class="status">{e(label)}</span>{badge}{sev}{mock}'
            f'<time>{e(str(ev.get("ts", "")).replace("T", " ").replace("+00:00", "Z"))}</time>'
            f'</div>'
            f'<div class="files">{filelist}</div>'
            f'{reason}{detail}{fixblock}'
            f'</li>'
        )
    return "".join(out)


def render_hazards(stats):
    if not stats["hazards"]:
        return ""
    total = sum(stats["hazards"].values())
    bars = []
    for hazard, n in sorted(stats["hazards"].items(), key=lambda kv: -kv[1]):
        pct = n * 100 // max(total, 1)
        bars.append(
            f'<div class="bar-row"><span class="bar-label">{e(hazard)}</span>'
            f'<span class="bar"><span class="bar-fill" style="width:{max(pct, 4)}%"></span></span>'
            f'<span class="bar-n">{n}</span></div>'
        )
    return f'<section class="card"><h2>Hazards blocked</h2>{"".join(bars)}</section>'


def page(commits, events, stats, root):
    saved = stats["skipped_docs"]
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SentinelCommit &middot; {e(os.path.basename(root))}</title>
<style>
:root {{
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --muted:#8b949e;
  --blue:#58a6ff; --green:#3fb950; --red:#f85149; --yellow:#d29922; --purple:#bc8cff;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
code,pre,.sha,.dag+ol .subject {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
header {{ padding:22px 28px; border-bottom:1px solid var(--line); background:var(--panel);
  display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }}
header h1 {{ margin:0; font-size:17px; letter-spacing:.2px; }}
header .repo {{ color:var(--muted); font-size:13px; }}
header .branch {{ margin-left:auto; color:var(--blue); background:#131d2e;
  border:1px solid #1f6feb55; padding:2px 10px; border-radius:20px; font-size:12px; }}
main {{ max-width:1180px; margin:0 auto; padding:24px 28px 64px; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px; margin-bottom:26px; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.stat b {{ display:block; font-size:26px; line-height:1.15; font-variant-numeric:tabular-nums; }}
.stat span {{ color:var(--muted); font-size:12px; }}
.stat.red b {{ color:var(--red); }} .stat.green b {{ color:var(--green); }}
.stat.blue b {{ color:var(--blue); }} .stat.yellow b {{ color:var(--yellow); }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:18px 20px; margin-bottom:22px; }}
.card h2 {{ margin:0 0 14px; font-size:13px; text-transform:uppercase;
  letter-spacing:.9px; color:var(--muted); font-weight:600; }}
.graph-wrap {{ display:flex; align-items:flex-start; overflow-x:auto; }}
.dag {{ flex:0 0 auto; }}
.rows {{ list-style:none; margin:0; padding:0; flex:1 1 auto; min-width:0; }}
.row {{ height:{ROW_H}px; display:flex; flex-direction:column; justify-content:center;
  padding:0 6px; border-bottom:1px solid #21262d; }}
.row:last-child {{ border-bottom:0; }}
.row-main {{ display:flex; align-items:center; gap:10px; min-width:0; }}
.sha {{ color:var(--yellow); font-size:12.5px; flex:0 0 auto; }}
.subject {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.row-meta {{ color:var(--muted); font-size:12px; display:flex; gap:7px; flex-wrap:wrap; }}
.dot {{ opacity:.5; }}
.ref {{ font-size:11px; padding:1px 8px; border-radius:20px; white-space:nowrap; flex:0 0 auto; }}
.ref-head {{ background:#132d1c; color:var(--green); border:1px solid #2ea04355; }}
.ref-branch {{ background:#131d2e; color:var(--blue); border:1px solid #1f6feb55; }}
.ref-remote {{ background:#2b2213; color:var(--yellow); border:1px solid #9e6a0355; }}
.ref-tag {{ background:#241a35; color:var(--purple); border:1px solid #8957e555; }}
.audits {{ list-style:none; margin:0; padding:0; }}
.audit {{ border-left:3px solid var(--line); padding:12px 0 12px 14px; margin-bottom:12px;
  background:#0f141b; border-radius:0 8px 8px 0; }}
.audit.blocked {{ border-left-color:var(--red); }}
.audit.passed {{ border-left-color:var(--green); }}
.audit.skipped {{ border-left-color:var(--muted); }}
.audit.failopen {{ border-left-color:var(--yellow); }}
.audit-head {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }}
.status {{ font-weight:600; font-size:12.5px; }}
.blocked .status {{ color:var(--red); }} .passed .status {{ color:var(--green); }}
.skipped .status {{ color:var(--muted); }} .failopen .status {{ color:var(--yellow); }}
.audit time {{ margin-left:auto; color:var(--muted); font-size:11.5px; }}
.hazard {{ font-size:11px; background:#2d1518; color:#ff9492; border:1px solid #f8514955;
  padding:1px 8px; border-radius:20px; }}
.sev {{ font-size:11px; padding:1px 8px; border-radius:20px; border:1px solid var(--line);
  color:var(--muted); }}
.sev-critical {{ color:#ff9492; border-color:#f8514955; }}
.sev-high {{ color:#ffa657; border-color:#db6d2855; }}
.mock {{ font-size:10px; letter-spacing:.7px; color:var(--muted);
  border:1px dashed var(--line); padding:1px 6px; border-radius:4px; }}
.files {{ margin:7px 0 0; display:flex; gap:6px; flex-wrap:wrap; }}
.files code {{ font-size:11.5px; background:#161b22; border:1px solid var(--line);
  padding:1px 7px; border-radius:5px; color:var(--muted); }}
.more {{ color:var(--muted); font-size:11.5px; }}
.reason {{ margin:9px 0 0; color:#c9d1d9; max-width:80ch; }}
.detail {{ margin:7px 0 0; color:var(--muted); font-size:12.5px; }}
details {{ margin-top:9px; }}
summary {{ cursor:pointer; color:var(--blue); font-size:12.5px; }}
pre {{ background:#0d1117; border:1px solid var(--line); border-radius:7px;
  padding:12px 14px; overflow:auto; font-size:12.5px; margin:9px 0 0; }}
.bar-row {{ display:flex; align-items:center; gap:12px; margin-bottom:9px; }}
.bar-label {{ flex:0 0 170px; font-size:12.5px; color:var(--muted); }}
.bar {{ flex:1; height:9px; background:#0d1117; border-radius:5px; overflow:hidden; }}
.bar-fill {{ display:block; height:100%; background:linear-gradient(90deg,#f85149,#db6d28); }}
.bar-n {{ flex:0 0 28px; text-align:right; font-variant-numeric:tabular-nums; }}
.empty {{ color:var(--muted); margin:0; }}
footer {{ color:var(--muted); font-size:12px; text-align:center; padding:0 0 28px; }}
</style></head><body>
<header>
  <h1>SentinelCommit</h1>
  <span class="repo">{e(root)}</span>
  <span class="branch">{e(stats["branch"])}</span>
</header>
<main>
  <div class="stats">
    <div class="stat blue"><b>{stats["commits"]}</b><span>commits</span></div>
    <div class="stat red"><b>{stats["blocked"]}</b><span>commits blocked</span></div>
    <div class="stat green"><b>{stats["passed"]}</b><span>audits passed</span></div>
    <div class="stat yellow"><b>{saved}</b><span>Tier 1 skips (free)</span></div>
    <div class="stat"><b>{stats["api_calls"]}</b><span>API calls made{
        ' &middot; ' + str(stats["mocked"]) + ' mocked' if stats["mocked"] else ''}</span></div>
    <div class="stat"><b>{stats["failed_open"]}</b><span>failed open</span></div>
  </div>

  {render_hazards(stats)}

  <section class="card">
    <h2>Commit graph</h2>
    <div class="graph-wrap">
      {render_graph(commits)}
      <ol class="rows">{render_rows(commits, events)}</ol>
    </div>
  </section>

  <section class="card">
    <h2>Audit trail</h2>
    <ol class="audits">{render_audit(events)}</ol>
  </section>
</main>
<footer>Auto-refreshes every 10s &middot; served locally by dashboard.py</footer>
<script>setTimeout(function () {{ location.reload(); }}, 10000);</script>
</body></html>
"""


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    limit = 80

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._json()
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            commits = assign_lanes(load_commits(self.limit))
            events = load_audit()
            body = page(commits, events, summarise(commits, events), repo_root())
        except Exception as exc:  # surface errors in the page, not the console
            body = (f"<!DOCTYPE html><meta charset=utf-8>"
                    f"<body style='background:#0d1117;color:#f85149;"
                    f"font:14px monospace;padding:40px'>"
                    f"<h2>dashboard error</h2><pre>{html.escape(str(exc))}</pre>")
        self._send(body.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self):
        try:
            commits = assign_lanes(load_commits(self.limit))
            events = load_audit()
            payload = {"commits": commits, "audit": events,
                       "stats": summarise(commits, events)}
            self._send(json.dumps(payload, indent=2).encode("utf-8"),
                       "application/json; charset=utf-8")
        except Exception as exc:
            self._send(json.dumps({"error": str(exc)}).encode("utf-8"),
                       "application/json; charset=utf-8", status=500)

    def _send(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep the terminal clean


def main():
    ap = argparse.ArgumentParser(description="Serve the SentinelCommit dashboard.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--limit", type=int, default=80, help="commits to load")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        root = repo_root()
    except Exception:
        print("error: not inside a git repository", file=sys.stderr)
        return 1

    Handler.limit = args.limit
    try:
        server = HTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"error: cannot bind {args.host}:{args.port} ({exc})", file=sys.stderr)
        print("hint: pass --port to pick another.", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}/"
    print(f"SentinelCommit dashboard  ->  {url}")
    print(f"repository: {root}")
    print("Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
