#!/usr/bin/env python3
"""
Record docs/images/dashboard-demo.gif - the dashboard populating live.

Drives a real session: clears the audit log, then stages each example in turn
and runs the real engine against it, screenshotting the running dashboard
after every step. The result is a genuine recording, not a mock-up.

Prerequisites:
    pip install Pillow
    python dashboard.py --no-browser        # in another terminal

Usage:
    python docs/record_dashboard_gif.py
    python docs/record_dashboard_gif.py --width 1000 --frame-ms 1400

The audit log is backed up and restored, so running this does not destroy
whatever history you already had.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
IMAGES = os.path.join(HERE, "images")

# (example stem, working filename, caption for the log)
STEPS = [
    ("transaction_fault_unsafe", "payment_service.py", "unsafe payment flow"),
    ("idempotency_unsafe", "webhook_handler.py", "unsafe webhook handler"),
    ("race_condition_unsafe", "booking_service.py", "unsafe seat reservation"),
    ("race_condition_safe", "booking_service.py", "remediated reservation"),
    ("idempotency_safe", "webhook_handler.py", "remediated webhook"),
    ("transaction_fault_safe", "payment_service.py", "remediated payment flow"),
]

EDGE_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles(x86)", ""),
                 "Microsoft", "Edge", "Application", "msedge.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""),
                 "Microsoft", "Edge", "Application", "msedge.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]


def find_browser():
    for path in EDGE_CANDIDATES:
        if path and os.path.exists(path):
            return path
    sys.exit("error: no Chromium-based browser found for headless capture")


def git(*args):
    subprocess.run(["git"] + list(args), cwd=REPO,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def audit_path():
    out = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=REPO,
                         stdout=subprocess.PIPE, text=True)
    return os.path.join(out.stdout.strip(), "sentinel-audit.jsonl")


def shoot(browser, url, dest, width, height, profile):
    """
    Capture one frame.

    Two things here are load-bearing. The profile directory must be unique per
    call: Chromium initialises a fresh profile asynchronously, and reusing one
    lets the launcher exit before the screenshot is written - the frame then
    lands on disk with whatever the page showed *later*, silently
    desynchronising the recording. And because the write can outlive the
    process, we wait for the file to appear and its size to settle rather than
    trusting the exit.
    """
    if os.path.exists(dest):
        os.remove(dest)
    subprocess.run([
        browser, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--virtual-time-budget=5000", f"--user-data-dir={profile}",
        f"--screenshot={dest}", f"--window-size={width},{height}", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)

    deadline = time.time() + 30
    last = -1
    while time.time() < deadline:
        if os.path.exists(dest):
            size = os.path.getsize(dest)
            if size > 0 and size == last:
                return
            last = size
        time.sleep(0.25)
    sys.exit(f"error: headless capture did not finish writing {dest}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--width", type=int, default=1000, help="output GIF width")
    ap.add_argument("--shot-height", type=int, default=1180)
    ap.add_argument("--frame-ms", type=int, default=1500)
    ap.add_argument("--hold-ms", type=int, default=3200, help="pause on last frame")
    ap.add_argument("--keep-frames", metavar="DIR",
                    help="also write the raw PNG frames here, for debugging")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        sys.exit("error: Pillow is required -> pip install Pillow")

    try:
        urllib.request.urlopen(args.url, timeout=10).read(1)
    except (urllib.error.URLError, OSError):
        sys.exit(f"error: no dashboard at {args.url}\n"
                 f"       start one with: python dashboard.py --no-browser")

    browser = find_browser()
    os.makedirs(IMAGES, exist_ok=True)
    log = audit_path()
    backup = log + ".bak" if os.path.exists(log) else None
    if backup:
        shutil.copy2(log, backup)

    env = dict(os.environ, SENTINEL_MOCK="1")
    tmp = tempfile.mkdtemp(prefix="sentinel-gif-")
    def profile_for(n):
        return os.path.join(tmp, f"profile{n:02d}")
    frames = []

    try:
        if os.path.exists(log):
            os.remove(log)
        git("reset")

        # Frame 0: the empty dashboard, before anything has been audited.
        first = os.path.join(tmp, "frame00.png")
        shoot(browser, args.url, first, 1280, args.shot_height, profile_for(0))
        frames.append(first)
        print("  frame 00  (empty dashboard)")

        for i, (stem, filename, caption) in enumerate(STEPS, start=1):
            shutil.copy2(os.path.join(REPO, "examples", stem + ".py"),
                         os.path.join(REPO, filename))
            git("reset")
            git("add", filename)
            result = subprocess.run([sys.executable, "sentinel.py"], cwd=REPO,
                                    env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            time.sleep(0.4)  # let the log flush before the page reads it
            dest = os.path.join(tmp, f"frame{i:02d}.png")
            shoot(browser, args.url, dest, 1280, args.shot_height, profile_for(i))
            frames.append(dest)
            verdict = "BLOCKED" if result.returncode == 1 else "passed "
            lines = sum(1 for _ in open(log, encoding="utf-8")) if os.path.exists(log) else 0
            print(f"  frame {i:02d}  {verdict}  {caption}  (log: {lines} entries)")

        durations = [args.frame_ms] * len(frames)
        durations[0] = 2200
        durations[-1] = args.hold_ms

        images = []
        for path, ms in zip(frames, durations):
            im = Image.open(path).convert("RGB")
            if im.width != args.width:
                h = round(im.height * args.width / im.width)
                im = im.resize((args.width, h), Image.LANCZOS)
            im = im.convert("P", palette=Image.ADAPTIVE, colors=128)
            # Stamp the delay on each frame as well as passing the list below.
            # Pillow reads per-frame `info["duration"]` when encoding, and
            # relying on the list alone silently gave every frame the same
            # delay - the whole loop ran at the hold duration.
            im.info["duration"] = ms
            images.append(im)

        if args.keep_frames:
            os.makedirs(args.keep_frames, exist_ok=True)
            for path in frames:
                shutil.copy2(path, os.path.join(args.keep_frames,
                                                os.path.basename(path)))
            print(f"  raw frames -> {args.keep_frames}")

        out = os.path.join(IMAGES, "dashboard-demo.gif")
        images[0].save(out, save_all=True, append_images=images[1:],
                       duration=durations, loop=0, optimize=True, disposal=2)
        size_mb = os.path.getsize(out) / 1_000_000
        print(f"\nwrote {os.path.relpath(out, REPO)} "
              f"({len(images)} frames, {size_mb:.2f} MB)")
        if size_mb > 9:
            print("warning: GitHub refuses files over 100MB and throttles large "
                  "images; consider --width 800.")
    finally:
        for name in ("payment_service.py", "webhook_handler.py", "booking_service.py"):
            p = os.path.join(REPO, name)
            if os.path.exists(p):
                os.remove(p)
        git("reset")
        if backup:
            shutil.move(backup, log)
        elif os.path.exists(log):
            os.remove(log)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
