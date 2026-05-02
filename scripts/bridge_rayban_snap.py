"""Meta Ray-Ban → LiveRecall bridge (screenshot-loop mode).

Captures periodic screenshots of a screen region (typically the Meta AI
iPhone-mirror window showing the Ray-Ban POV) and POSTs them to the
backend's ``/snap`` endpoint with ``capture_mode="glasses"``. This is the
same code path Path A already exercises end-to-end — this script just
drives it on a timer.

Why this shape vs the MP4-into-LiveKit bridge:

  - Ray-Ban Meta v2 + the Meta AI app don't expose a public live-video
    endpoint we can subscribe to. Mirroring the iPhone to macOS and
    grabbing screenshots is the only in-reach way to pipe the real
    POV frames into the demo during a hackathon.
  - ``/snap`` is already the deterministic, judge-proof entry point: it
    runs Vision synchronously, writes ``scene_context``, warms the Local
    Retrieval cache, and optionally fires a question. Dropping
    screenshots in on a timer gives you a continuous-capture feel without
    needing a real WebRTC bridge.

Runs on macOS only (uses the built-in ``screencapture`` binary). No extra
Python deps beyond what's already in ``backend/requirements.txt``.

Usage::

    # pick the region once (opens a full-screen translucent picker)
    python -m scripts.bridge_rayban_snap --pick
    # → prints: REGION x,y,w,h  →  copy it and reuse:

    python -m scripts.bridge_rayban_snap --region 120,90,960,540 \\
        --backend http://localhost:8001 \\
        --interval 5 \\
        --session-id demo

    # or: no region → whole main display
    python -m scripts.bridge_rayban_snap --fullscreen

Each screenshot is downscaled to ≤640 px on the long edge (cheaper Vision
call, faster /snap round trip) before being base64-encoded and POSTed.
Press Ctrl-C to stop.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from PIL import Image

log = logging.getLogger("bridge_rayban_snap")


MAX_EDGE_PX = 640               # cap long edge before upload; Vision doesn't need more
JPEG_QUALITY = 72               # good-enough for OCR + object recognition
DEFAULT_INTERVAL_S = 3.0        # matches README demo cadence
DEFAULT_BACKEND = "http://localhost:8001"
DEFAULT_SESSION = "demo"


# --- Screen capture ---------------------------------------------------------

def _screencapture_region(region: str | None, out_path: Path) -> None:
    """Wrap macOS ``screencapture`` for a non-interactive region/fullscreen grab.

    ``region`` is either ``"x,y,w,h"`` or ``None`` for whole main display.
    ``-x`` silences the shutter sound, ``-t jpg`` writes JPEG directly so we
    don't re-encode twice.
    """
    cmd = ["screencapture", "-x", "-t", "jpg"]
    if region:
        cmd += ["-R", region]
    else:
        cmd += ["-m"]  # main monitor only
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True)


def _encode_for_snap(path: Path) -> tuple[str, str]:
    """Downsize → JPEG → base64. Returns (b64_str, content_hash)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, MAX_EDGE_PX / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        data = buf.getvalue()
    b64 = base64.b64encode(data).decode("ascii")
    h = hashlib.sha1(data).hexdigest()[:12]
    return b64, h


# --- Region picker (tkinter overlay) ----------------------------------------

def pick_region_interactive() -> tuple[int, int, int, int]:
    """Full-screen translucent overlay; user drags a rectangle.

    Returns (x, y, w, h) in *screen* pixel coordinates suitable for
    ``screencapture -R``. We divide tk's logical coords by 1.0 since tk on
    macOS reports points == screen pixels for the menubar-less root window.
    """
    try:
        import tkinter as tk
    except ImportError as e:  # pragma: no cover
        raise SystemExit(f"tkinter required for --pick: {e}") from e

    selection: dict[str, int] = {}

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.configure(bg="#000000")
    root.title("Ray-Ban bridge — drag to select region")

    canvas = tk.Canvas(root, bg="#000000", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    label = tk.Label(
        root,
        text="Drag a rectangle over the Meta AI / iPhone-mirror window. "
             "Press ESC to cancel.",
        bg="#111827",
        fg="#e5e7eb",
        font=("Helvetica", 14),
    )
    label.place(relx=0.5, y=30, anchor="n")

    state = {"x0": 0, "y0": 0, "rect_id": None}

    def on_press(e):
        state["x0"], state["y0"] = e.x_root, e.y_root
        if state["rect_id"]:
            canvas.delete(state["rect_id"])
        state["rect_id"] = canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#a78bfa", width=2
        )

    def on_drag(e):
        if not state["rect_id"]:
            return
        x0 = state["x0"] - root.winfo_rootx()
        y0 = state["y0"] - root.winfo_rooty()
        canvas.coords(state["rect_id"], x0, y0, e.x, e.y)

    def on_release(e):
        x1, y1 = e.x_root, e.y_root
        x0, y0 = state["x0"], state["y0"]
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 10 or h < 10:
            label.config(text="Too small — drag a bigger rectangle.")
            return
        selection.update({"x": x, "y": y, "w": w, "h": h})
        root.destroy()

    def on_escape(_):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)
    root.mainloop()

    if not selection:
        raise SystemExit("cancelled")
    return selection["x"], selection["y"], selection["w"], selection["h"]


# --- HTTP client ------------------------------------------------------------

def post_snap(
    client: httpx.Client,
    backend: str,
    session_id: str,
    image_b64: str,
    question: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "image_b64": image_b64,
        "session_id": session_id,
        "capture_mode": "glasses",
    }
    if question:
        body["question"] = question
    r = client.post(f"{backend.rstrip('/')}/snap", json=body, timeout=30.0)
    r.raise_for_status()
    return r.json()


def prime_session(client: httpx.Client, backend: str, session_id: str) -> None:
    """Ensure the session doc is stamped ``capture_mode="glasses"``.

    ``/snap`` itself will do this when given ``capture_mode`` in the body,
    but calling ``/token`` first is cheaper (no Vision), gets us the same
    result, and makes the dashboard light up immediately rather than
    waiting for the first screenshot round-trip.
    """
    try:
        r = client.post(
            f"{backend.rstrip('/')}/token",
            json={
                "identity": f"rayban-snap-{session_id}",
                "room": f"liverecall-{session_id}",
                "capture_mode": "glasses",
            },
            timeout=10.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("prime_session skipped (/token failed: %s)", e)


# --- Main loop --------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    load_dotenv()

    if args.pick:
        x, y, w, h = pick_region_interactive()
        print(f"REGION {x},{y},{w},{h}")
        return 0

    region: str | None
    if args.fullscreen:
        region = None
        log.info("region: full main display")
    elif args.region:
        parts = [p.strip() for p in args.region.split(",")]
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            log.error("--region expects 'x,y,w,h' with integers; got %r", args.region)
            return 2
        region = ",".join(parts)
        log.info("region: %s", region)
    else:
        log.error("must pass --region x,y,w,h or --fullscreen (or --pick to select)")
        return 2

    interval = max(0.5, float(args.interval))
    backend = args.backend or DEFAULT_BACKEND
    session_id = args.session_id or DEFAULT_SESSION
    question = args.question  # may be None

    tmpdir = Path(tempfile.mkdtemp(prefix="rayban-snap-"))

    # --- One-shot mode -------------------------------------------------------
    # Intended for hotkey-driven workflows: user scrubs a pre-recorded Ray-Ban
    # MP4 to a frame of interest (QuickTime / VLC / any player window), binds
    # a macOS Shortcut (or Hammerspoon) to this command, and gets exactly one
    # /snap per keypress. Same HTTP path Path A uses — nothing new server-side.
    if args.once:
        log.info(
            "one-shot snap → %s (session=%s, q=%r)", backend, session_id, question
        )
        shot = tmpdir / "shot_once.jpg"
        with httpx.Client() as client:
            prime_session(client, backend, session_id)
            t0 = time.time()
            try:
                _screencapture_region(region, shot)
                b64, _ = _encode_for_snap(shot)
                resp = post_snap(client, backend, session_id, b64, question)
            except subprocess.CalledProcessError as e:
                log.error("screencapture failed: %s", e.stderr.decode() if e.stderr else e)
                return 3
            except httpx.HTTPError as e:
                log.error("/snap failed: %s", e)
                return 4
            finally:
                try:
                    shot.unlink(missing_ok=True)
                except OSError:
                    pass
        qid = resp.get("question_id")
        objs = resp.get("objects") or []
        vis = resp.get("text_visible") or []
        log.info(
            "snap sent in %.2fs · objects=%s · visible=%s · qid=%s",
            time.time() - t0, objs[:4], vis[:4], qid or "-",
        )
        # Print machine-readable line too, so a Shortcut / wrapper can surface
        # it in a notification without re-parsing the log format.
        import json as _json
        print(_json.dumps({
            "question_id": qid,
            "objects": objs,
            "text_visible": vis,
            "text_summary": resp.get("text_summary"),
            "scene_context_id": resp.get("scene_context_id"),
        }))
        return 0

    # --- Loop mode -----------------------------------------------------------
    log.info(
        "screenshot bridge → %s (session=%s, interval=%.1fs, q=%r)",
        backend, session_id, interval, question,
    )
    with httpx.Client() as client:
        prime_session(client, backend, session_id)
        last_hash: str | None = None
        n = 0
        try:
            while True:
                n += 1
                shot = tmpdir / f"shot_{n:05d}.jpg"
                try:
                    t0 = time.time()
                    _screencapture_region(region, shot)
                    b64, h = _encode_for_snap(shot)
                    if args.dedup and h == last_hash:
                        log.info("#%04d skipped (unchanged, hash=%s)", n, h)
                    else:
                        resp = post_snap(client, backend, session_id, b64, question)
                        last_hash = h
                        qid = resp.get("question_id")
                        objs = resp.get("objects") or []
                        vis = resp.get("text_visible") or []
                        log.info(
                            "#%04d sent in %.2fs · objects=%s · visible=%s · qid=%s",
                            n,
                            time.time() - t0,
                            objs[:4],
                            vis[:4],
                            qid or "-",
                        )
                except subprocess.CalledProcessError as e:
                    log.error("screencapture failed: %s", e.stderr.decode() if e.stderr else e)
                except httpx.HTTPError as e:
                    log.error("/snap failed: %s", e)
                except Exception as e:  # noqa: BLE001
                    log.exception("snap loop iter failed: %s", e)
                finally:
                    try:
                        shot.unlink(missing_ok=True)
                    except OSError:
                        pass

                # Sleep the remainder of the interval, responsive to Ctrl-C.
                elapsed = time.time() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            log.info("stopping (Ctrl-C)")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Meta Ray-Ban bridge (screenshot mode) — captures a "
        "screen region every N seconds and POSTs it to /snap with "
        "capture_mode=glasses.",
    )
    p.add_argument(
        "--region",
        help="screen region to capture as 'x,y,w,h' (macOS pixel coords).",
    )
    p.add_argument(
        "--fullscreen",
        action="store_true",
        help="capture the whole main display instead of a region.",
    )
    p.add_argument(
        "--pick",
        action="store_true",
        help="open an interactive picker, print the REGION coords, and exit.",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"seconds between screenshots (default: {DEFAULT_INTERVAL_S})",
    )
    p.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        help=f"LiveRecall backend URL (default: {DEFAULT_BACKEND})",
    )
    p.add_argument(
        "--session-id",
        default=DEFAULT_SESSION,
        help=f"session id to stamp on every snap (default: {DEFAULT_SESSION})",
    )
    p.add_argument(
        "--question",
        default=None,
        help="optional question text to fire with every snap (Path A style). "
             "Omit for scene-only updates; questions can still be asked via /ask.",
    )
    p.add_argument(
        "--dedup",
        action="store_true",
        help="skip a snap if the JPEG bytes are byte-identical to the previous "
             "one (cheap protection against spending Vision tokens on a frozen window).",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="take exactly one snapshot, POST it to /snap, print the JSON "
             "response, and exit. Pair with a macOS Shortcut / Hammerspoon "
             "hotkey for on-demand Path A capture from a paused MP4 player.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    sys.exit(run(args))


if __name__ == "__main__":
    main()
