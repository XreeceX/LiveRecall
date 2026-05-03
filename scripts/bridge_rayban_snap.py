"""Meta Ray-Ban → LiveRecall bridge (screenshot-loop mode).

Captures periodic screenshots of a screen region (typically the Meta AI
iPhone-mirror window showing the Ray-Ban POV) and POSTs them to the
backend's ``/snap`` endpoint with ``capture_mode="glasses"``. This is the
same code path Path A already exercises end-to-end — this script just
drives it on a timer.

Why this shape vs the MP4-into-LiveKit bridge:

  - Ray-Ban Meta v2 + the Meta AI app don't expose a public live-video
    endpoint we can subscribe to. Mirroring the paired phone to a desktop
    (QuickTime / Phone Link / AirPlay receiver / third-party cast) and
    grabbing screenshots is the usual way to pipe real POV frames in.
  - ``/snap`` is already the deterministic, judge-proof entry point: it
    runs Vision synchronously, writes ``scene_context``, warms the Local
    Retrieval cache, and optionally fires a question. Dropping
    screenshots in on a timer gives you a continuous-capture feel without
    needing a real WebRTC bridge.

**Capture backend:** all desktop OSes use the ``mss`` package (same coordinate
system as ``--pick``). Install via ``backend/requirements.txt``. Screen-recording
permission still applies (macOS Settings; Windows may prompt depending on version).

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
import ctypes
import hashlib
import logging
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


def _platform_capture(region: str | None, out_path: Path) -> None:
    """Write a JPEG screenshot to ``out_path`` using ``mss`` (Windows, macOS, Linux).

    ``region`` is ``"x,y,w,h"`` in physical screen pixels matching ``--pick``,
    or ``None`` for the primary monitor (`mss.monitors[1]`).
    """
    try:
        import mss
    except ImportError as e:
        raise SystemExit(
            "Screen capture requires `mss`. Install: pip install -r backend/requirements.txt"
        ) from e

    with mss.mss() as sct:
        if region:
            parts = [int(p.strip()) for p in region.split(",")]
            if len(parts) != 4:
                raise ValueError(f"bad region: {region}")
            x, y, w, h = parts
            if w < 1 or h < 1:
                raise ValueError(f"region width/height must be positive: {region}")
            box = {"left": x, "top": y, "width": w, "height": h}
        else:
            box = sct.monitors[1]
        shot = sct.grab(box)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img.save(out_path, format="JPEG", quality=JPEG_QUALITY)


# --- Screen capture ---------------------------------------------------------


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

def _ensure_windows_physical_pixels() -> None:
    """Make Tk root coords match ``mss`` (physical pixels) on hi-DPI Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def pick_region_interactive() -> tuple[int, int, int, int]:
    """Full-screen translucent overlay; user drags a rectangle.

    Returns (x, y, w, h) in physical screen pixels — same space as ``mss.grab``.
    """
    _ensure_windows_physical_pixels()
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
    # MP4 to a frame of interest (any player window), binds a desktop hotkey to
    # this command, and gets exactly one /snap per keypress.
    if args.once:
        log.info(
            "one-shot snap → %s (session=%s, q=%r)", backend, session_id, question
        )
        shot = tmpdir / "shot_once.jpg"
        with httpx.Client() as client:
            prime_session(client, backend, session_id)
            t0 = time.time()
            try:
                _platform_capture(region, shot)
                b64, _ = _encode_for_snap(shot)
                resp = post_snap(client, backend, session_id, b64, question)
            except httpx.HTTPError as e:
                log.error("/snap failed: %s", e)
                return 4
            except Exception as e:
                log.error("screen capture failed: %s", e)
                return 3
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
                    _platform_capture(region, shot)
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
                except OSError as e:
                    log.error("screen capture failed (permission or display?): %s", e)
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
        help="screen region as 'x,y,w,h' in physical pixels (all desktop OSes).",
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
             "response, and exit. Pair with a desktop hotkey (Shortcuts, "
             "AutoHotkey, etc.) for on-demand capture from a paused mirror/player.",
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
