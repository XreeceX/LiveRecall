#!/usr/bin/env python3
"""Run backend, LiveKit worker, Next dashboard, and phone static server together.

From the LiveRecall repo root:

    python scripts/dev_stack.py

Ctrl+C stops every child process. Load ``.env`` from the repo root before
spawning (same as running ``python -m backend.main`` yourself).

One-time setup still applies: ``pip install -r backend/requirements.txt``,
``cd dashboard && npm install``, ``make seed`` (or equivalent).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
# (label, Popen) in start order — used for shutdown and exit diagnostics
PROC_ENTRIES: list[tuple[str, subprocess.Popen[str]]] = []
OUT_LOCK = threading.Lock()


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _stream(prefix: str, stream: TextIO | None) -> None:
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            with OUT_LOCK:
                sys.stdout.write(f"{prefix}{line}")
                sys.stdout.flush()
    except (ValueError, OSError):
        pass


def _add_proc(
    args: list[str],
    *,
    cwd: Path,
    name: str,
    shell: bool = False,
) -> subprocess.Popen[str]:
    p = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        shell=shell,
        env=os.environ.copy(),
    )
    PROC_ENTRIES.append((name, p))
    t = threading.Thread(target=_stream, args=(f"[{name}] ", p.stdout), daemon=True)
    t.start()
    return p


def _shutdown() -> None:
    for _label, p in PROC_ENTRIES:
        if p.poll() is None:
            p.terminate()
    deadline = time.monotonic() + 8.0
    for _label, p in PROC_ENTRIES:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            p.kill()


def _on_signal(_sig: int, _frame: object) -> None:
    _shutdown()
    sys.exit(130)


def main() -> int:
    parser = argparse.ArgumentParser(description="LiveRecall dev stack (one terminal).")
    parser.add_argument("--no-dashboard", action="store_true", help="Skip Next.js (port 3000).")
    parser.add_argument("--no-phone", action="store_true", help="Skip static server (port 8080).")
    parser.add_argument(
        "--skip-sleep",
        action="store_true",
        help="Do not wait after starting backend before worker.",
    )
    args = parser.parse_args()

    os.chdir(REPO_ROOT)
    _load_env()

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    else:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    py = sys.executable

    print(
        "\nLiveRecall dev stack — URLs:\n"
        f"  Glasses page:  http://localhost:8080/glasses.html\n"
        f"  Dashboard:       http://localhost:3000\n"
        f"  API:             http://localhost:8000\n"
        "Phone (HTTPS): from repo root run `make phone-demo` or "
        "`python scripts/phone_demo.py` — tunnels start first, then this stack; "
        "you get one link with backend baked in. Manual: `make tunnel-phone` + "
        "`make tunnel-backend` in two terminals.\n"
        "Ctrl+C stops all services started here.\n",
        flush=True,
    )

    _add_proc([py, "-m", "backend.main"], cwd=REPO_ROOT, name="backend")
    if not args.skip_sleep:
        time.sleep(1.5)

    _add_proc([py, "-m", "backend.worker", "dev"], cwd=REPO_ROOT, name="worker")

    npm = shutil.which("npm")
    if not args.no_dashboard and npm:
        _add_proc([npm, "run", "dev"], cwd=REPO_ROOT / "dashboard", name="dashboard")
    elif not args.no_dashboard:
        print("[dev_stack] npm not found in PATH; skipping dashboard.", flush=True)

    if not args.no_phone:
        _add_proc([py, "-m", "http.server", "8080"], cwd=REPO_ROOT / "phone", name="phone")

    try:
        while True:
            for label, p in PROC_ENTRIES:
                code = p.poll()
                if code is not None:
                    print(
                        f"\n[dev_stack] {label} exited with code {code}. Shutting down.\n",
                        flush=True,
                    )
                    _shutdown()
                    return int(code or 1)
            time.sleep(0.4)
    except KeyboardInterrupt:
        _shutdown()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
