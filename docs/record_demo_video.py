#!/usr/bin/env python3
"""
Build docs/images/sentinel-demo.mp4 - a narrated walkthrough.

Four scenes: the problem, the commit being refused, the remediated commit
passing, and the dashboard animating. Narration is generated with the local
Windows speech synthesiser, and each scene is held on screen for exactly as
long as its own narration takes - so the two never drift apart.

Requirements:
    pip install Pillow
    ffmpeg on PATH
    Windows (System.Speech). On macOS/Linux the `say`/`espeak` path is used.

Usage:
    python docs/record_demo_video.py
    python docs/record_demo_video.py --no-audio      # silent video
"""

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
IMAGES = os.path.join(HERE, "images")

W, H = 1280, 720
BG = (13, 17, 23)
PANEL = (22, 27, 34)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
RED = (248, 81, 73)
GREEN = (63, 185, 80)
BLUE = (88, 166, 255)

# (key, title, accent, narration)
SCENES = [
    ("title", "SentinelCommit", BLUE,
     "Linters catch syntax. They cannot catch a database write that is made "
     "durable before an unguarded network call. SentinelCommit reads your "
     "staged diff on git commit, reasons about it with Claude, and refuses "
     "the commit when it finds a runtime hazard."),
    ("blocked", "The error - commit refused", RED,
     "Here the customer balance is debited and committed, and only then is the "
     "payment gateway called. If that call times out, the debit is already "
     "durable and nothing reverses it. Ruff, flake8 and mypy all pass this "
     "file. SentinelCommit refuses the commit, names the hazard as a "
     "transaction fault, and prints a ready to paste patch."),
    ("passed", "The fix - commit accepted", GREEN,
     "After applying the patch, one transaction now covers the debit and the "
     "gateway call, so a failure rolls the debit back. The same commit is "
     "re-staged and this time all semantic checks pass."),
    ("dashboard", "The dashboard", BLUE,
     "A blocked commit leaves no trace in git history, so the tool's most "
     "valuable moments are invisible to git log. Every verdict is appended to "
     "an audit file, and the dashboard joins it against the commit graph. "
     "Watch the counters, the hazard bars and the audit trail fill in as each "
     "commit is audited."),
]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **kw)


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for p in [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
              r"C:\ffmpeg\bin\ffmpeg.exe"]:
        if os.path.exists(p):
            return p
    sys.exit("error: ffmpeg not found on PATH")


def font(size, bold=False):
    from PIL import ImageFont
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold
                 else ("segoeui.ttf", "arial.ttf")):
        for root in (r"C:\Windows\Fonts", "/Library/Fonts", "/usr/share/fonts"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def narrate(text, dest):
    """Render narration to a WAV using whatever TTS the platform provides."""
    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 0; "
            f"$s.SetOutputToWaveFile('{dest}'); "
            f"$s.Speak(@'\n{text}\n'@); $s.Dispose()"
        )
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    elif sys.platform == "darwin":
        aiff = dest + ".aiff"
        run(["say", "-o", aiff, text])
        run([find_ffmpeg(), "-y", "-i", aiff, dest])
    else:
        run(["espeak", "-w", dest, text])
    return dest


def wav_seconds(path):
    with contextlib.closing(wave.open(path, "rb")) as w:
        return w.getnframes() / float(w.getframerate())


def compose(content, title, accent, caption=""):
    """Place one content image on the standard canvas."""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    d.rectangle([0, 0, W, 64], fill=PANEL)
    d.rectangle([0, 62, W, 65], fill=accent)
    d.text((36, 20), title, font=font(24, bold=True), fill=FG)
    d.text((W - 300, 26), "SentinelCommit", font=font(15), fill=MUTED)

    top, bottom = 92, H - (56 if caption else 28)
    avail_w, avail_h = W - 96, bottom - top
    if content is not None:
        c = content.convert("RGB")
        scale = min(avail_w / c.width, avail_h / c.height, 1.6)
        c = c.resize((max(1, int(c.width * scale)), max(1, int(c.height * scale))),
                     Image.LANCZOS)
        x = (W - c.width) // 2
        y = top + (avail_h - c.height) // 2
        d.rectangle([x - 2, y - 2, x + c.width + 1, y + c.height + 1],
                    outline=(48, 54, 61))
        canvas.paste(c, (x, y))

    if caption:
        f = font(17)
        tw = d.textlength(caption, font=f)
        d.text(((W - tw) / 2, H - 42), caption, font=f, fill=MUTED)
    return canvas


def title_card():
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    d.text((80, 232), "SentinelCommit", font=font(72, bold=True), fill=FG)
    d.rectangle([80, 330, 320, 335], fill=BLUE)
    d.text((80, 368), "A Git pre-commit hook that catches semantic bugs",
           font=font(30), fill=MUTED)
    d.text((80, 410), "your linter structurally cannot.", font=font(30), fill=MUTED)
    for i, (label, colour) in enumerate([("TRANSACTION_FAULT", RED),
                                         ("IDEMPOTENCY_RISK", RED),
                                         ("RACE_CONDITION", RED)]):
        x = 80 + i * 250
        d.rounded_rectangle([x, 500, x + 230, 540], 20, outline=colour)
        d.text((x + 18, 511), label, font=font(15), fill=colour)
    d.text((80, 620), "Powered by Claude  ·  claude-opus-5",
           font=font(18), fill=MUTED)
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--fallback-seconds", type=float, default=7.0,
                    help="per-scene hold when narration is disabled")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        sys.exit("error: Pillow is required -> pip install Pillow")

    ffmpeg = find_ffmpeg()
    tmp = tempfile.mkdtemp(prefix="sentinel-video-")
    src = os.path.join(os.environ.get("TEMP", tmp), "vidsrc")

    try:
        blocked = os.path.join(src, "blocked.png")
        passed = os.path.join(src, "passed.png")
        for p in (blocked, passed):
            if not os.path.exists(p):
                sys.exit(f"error: missing {p}\n"
                         f"       rasterise the SVGs first (see docs/DEMO.md)")

        gif = Image.open(os.path.join(IMAGES, "dashboard-demo.gif"))
        gif_frames = []
        for i in range(gif.n_frames):
            gif.seek(i)                       # seek, do not cache the iterator:
            gif_frames.append(gif.convert("RGB").copy())   # every entry would
                                                           # otherwise be the
                                                           # last frame.

        segments = []   # (png path, seconds)
        wavs = []
        for idx, (key, title, accent, text) in enumerate(SCENES):
            secs = args.fallback_seconds
            if not args.no_audio:
                wav = os.path.join(tmp, f"n{idx}.wav")
                narrate(text, wav)
                secs = wav_seconds(wav) + 0.6
                wavs.append(wav)
                print(f"  narration {idx}: {secs:5.1f}s  {title}")

            if key == "title":
                img = title_card()
            elif key == "blocked":
                img = compose(Image.open(blocked), title, accent,
                              "git commit  ->  refused, with a ready-to-paste patch")
            elif key == "passed":
                img = compose(Image.open(passed), title, accent,
                              "git commit  ->  accepted")
            else:
                img = None

            if key == "dashboard":
                per = secs / len(gif_frames)
                for fi, frame in enumerate(gif_frames):
                    p = os.path.join(tmp, f"s{idx}_{fi:02d}.png")
                    compose(frame, title, accent,
                            "live audit trail, commit graph and hazard counters").save(p)
                    segments.append((p, per))
            else:
                p = os.path.join(tmp, f"s{idx}.png")
                img.save(p)
                segments.append((p, secs))

        listing = os.path.join(tmp, "concat.txt")
        with open(listing, "w", encoding="utf-8") as fh:
            for p, secs in segments:
                fh.write(f"file '{p.replace(os.sep, '/')}'\nduration {secs:.3f}\n")
            fh.write(f"file '{segments[-1][0].replace(os.sep, '/')}'\n")

        out = os.path.join(IMAGES, "sentinel-demo.mp4")
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listing]

        if wavs:
            track = os.path.join(tmp, "narration.wav")
            alist = os.path.join(tmp, "audio.txt")
            with open(alist, "w", encoding="utf-8") as fh:
                for w in wavs:
                    fh.write(f"file '{w.replace(os.sep, '/')}'\n")
            run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", alist,
                 "-c", "copy", track])
            cmd += ["-i", track, "-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd += ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                       f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,fps=25",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        run(cmd)

        mb = os.path.getsize(out) / 1_000_000
        total = sum(s for _, s in segments)
        print(f"\nwrote {os.path.relpath(out, REPO)}  "
              f"({total:.1f}s, {mb:.2f} MB, {len(segments)} segments)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
