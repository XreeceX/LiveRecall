#!/usr/bin/env python3
"""One-terminal phone demo: Cloudflare Quick Tunnels + dev stack.

Starts trycloudflare tunnels to localhost:8080 (phone static) and :8000 (API),
prints a single HTTPS link that opens ``glasses.html`` with ``?backend=…`` set,
then runs ``scripts/dev_stack.py`` (same as ``make dev-stack``).

Prereqs:
  - ``cloudflared`` installed (Windows: ``winget install Cloudflare.cloudflared``)
  - Optional: set ``CLOUDFLARED`` to the full path to ``cloudflared.exe``

Usage (from repo root):

    python scripts/phone_demo.py
    python scripts/phone_demo.py --no-dashboard

Ctrl+C stops tunnels and the dev stack.

Desktop workflow is unchanged: ``python scripts/dev_stack.py`` + localhost URLs.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent

# Quick Tunnel prints e.g. https://words-here.trycloudflare.com
_TRY_CLOUDFLARE_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)", re.I)

_TUNNEL_PROCS: list[subprocess.Popen[str]] = []
_STACK_PROC: subprocess.Popen[str] | None = None


def _cloudflared_exe() -> str:
    override = os.environ.get("CLOUDFLARED", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        return override  # might be on PATH
    if sys.platform == "win32":
        for cand in (
            Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
            Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        ):
            if cand.is_file():
                return str(cand)
    w = shutil.which("cloudflared")
    if w:
        return w
    raise FileNotFoundError(
        "cloudflared not found. Install: "
        "https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/"
        "install-and-setup/installation/ "
        "(Windows: winget install Cloudflare.cloudflared). "
        "Or set CLOUDFLARED to the full path to cloudflared.exe."
    )


def _start_tunnel(port: int, label: str, timeout_s: float = 120.0) -> str:
    exe = _cloudflared_exe()
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    _TUNNEL_PROCS.append(proc)
    assert proc.stdout is not None

    url_seen: dict[str, str | None] = {"url": None}
    lock = threading.Lock()

    def reader() -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                with lock:
                    sys.stdout.write(f"[{label}] {line}")
                    sys.stdout.flush()
                m = _TRY_CLOUDFLARE_RE.search(line)
                if m and url_seen["url"] is None:
                    url_seen["url"] = m.group(1).rstrip("/")
        except (ValueError, OSError):
            pass

    threading.Thread(target=reader, daemon=True).start()

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with lock:
            u = url_seen["url"]
        if u:
            return u
        if proc.poll() is not None:
            break
        time.sleep(0.05)

    raise RuntimeError(
        f"{label}: no trycloudflare URL within {timeout_s:.0f}s "
        f"(cloudflared exit={proc.poll()}). Is cloudflared installed and online?"
    )


def _cleanup(*_args: object) -> None:
    global _STACK_PROC
    if _STACK_PROC and _STACK_PROC.poll() is None:
        _STACK_PROC.terminate()
        try:
            _STACK_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _STACK_PROC.kill()
    for p in _TUNNEL_PROCS:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cloudflare tunnels + LiveRecall dev stack for phone (one link with backend preset)."
    )
    parser.add_argument("--no-dashboard", action="store_true", help="Skip Next.js (port 3000).")
    parser.add_argument("--no-phone", action="store_true", help="Skip static server (not recommended).")
    parser.add_argument(
        "--skip-sleep",
        action="store_true",
        help="Do not wait after starting backend before worker (passed through to dev_stack).",
    )
    args = parser.parse_args()

    os.chdir(REPO_ROOT)

    def _on_signal(_signum: int, _frame: object | None) -> None:
        _cleanup()
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    print(
        "\nLiveRecall phone demo — starting Cloudflare Quick Tunnels first.\n"
        "(Origin :8080 / :8000 need not be up yet; tunnels retry until dev_stack listens.)\n",
        flush=True,
    )

    phone_base = _start_tunnel(8080, "tunnel-phone")
    api_base = _start_tunnel(8000, "tunnel-backend")

    backend_q = quote(api_base, safe="")
    one_link = f"{phone_base}/glasses.html?backend={backend_q}"

    print(
        "\n"
        + "=" * 72
        + "\n"
        "  OPEN THIS ON YOUR PHONE (backend URL is embedded):\n\n"
        f"    {one_link}\n\n"
        "  Dashboard (laptop):  http://localhost:3000\n"
        "  Same tab works on desktop too — optional.\n"
        + "=" * 72
        + "\n\n"
        "Starting dev stack (Ctrl+C stops tunnels + stack)…\n",
        flush=True,
    )

    stack_cmd = [sys.executable, str(REPO_ROOT / "scripts" / "dev_stack.py")]
    if args.no_dashboard:
        stack_cmd.append("--no-dashboard")
    if args.no_phone:
        stack_cmd.append("--no-phone")
    if args.skip_sleep:
        stack_cmd.append("--skip-sleep")

    global _STACK_PROC
    _STACK_PROC = subprocess.Popen(
        stack_cmd,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
    )
    code = _STACK_PROC.wait()
    _cleanup()
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
