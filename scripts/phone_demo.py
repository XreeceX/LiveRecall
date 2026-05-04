#!/usr/bin/env python3
"""One-terminal phone demo: HTTPS tunnels + dev stack.

Default tunnels use **auto** mode: if **ngrok** is installed and authenticated,
that runs **first** (usually more reliable under load). Otherwise **Cloudflare
Quick Tunnels** (trycloudflare.com) run with **retries + backoff** (429 is common
when the free tier is busy), then **ngrok** is tried if Cloudflare keeps failing.

Starts tunnels to localhost:8080 (phone static) and :8000 (API), prints a
single HTTPS link that opens ``glasses.html`` with ``?backend=…`` set, then runs
``scripts/dev_stack.py`` (same as ``make dev-stack``).

Prereqs:
  - Cloudflare: ``cloudflared`` installed (Windows:
    ``winget install Cloudflare.cloudflared``). Optional: ``CLOUDFLARED`` path.
  - **ngrok** (recommended): on PATH, WinGet install, or ``NGROK`` = path to exe.
    One-time auth: ``ngrok config add-authtoken …``, or set ``NGROK_AUTHTOKEN`` in
    ``.env`` (loaded by this script). ``auto`` uses ngrok first when ready.

Manual bypass (no tunnel processes in this script): set both
``LIVE_RECALL_PHONE_PUBLIC_URL`` and ``LIVE_RECALL_API_PUBLIC_URL`` to full
HTTPS origins (e.g. two ``ngrok http`` or ``cloudflared tunnel`` terminals you
started yourself).

Usage (from repo root):

    python scripts/phone_demo.py
    python scripts/phone_demo.py --no-dashboard
    python scripts/phone_demo.py --tunnel ngrok
    python scripts/phone_demo.py --tunnel-retries 8 --tunnel-backoff 15

Ctrl+C stops tunnels and the dev stack.

Desktop workflow is unchanged: ``python scripts/dev_stack.py`` + localhost URLs.
"""

from __future__ import annotations

import argparse
import os
import random
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

# ngrok v2/v3 common URL shapes (free tier uses ngrok-free.app)
_NGROK_URL_RE = re.compile(
    r"(https://[a-z0-9-]+\.(?:ngrok-free\.app|ngrok\.io|ngrok\.app)(?::\d+)?)",
    re.I,
)

_TUNNEL_PROCS: list[subprocess.Popen[str]] = []
_STACK_PROC: subprocess.Popen[str] | None = None


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _ngrok_winget_exe() -> str | None:
    """Windows winget installs ngrok under LOCALAPPDATA (often not on Git Bash PATH)."""
    if sys.platform != "win32":
        return None
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        return None
    pkg_root = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not pkg_root.is_dir():
        return None
    for child in sorted(pkg_root.glob("Ngrok.Ngrok_*")):
        cand = child / "ngrok.exe"
        if cand.is_file():
            return str(cand)
    return None


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


def _ngrok_exe() -> str:
    env_path = os.environ.get("NGROK", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        return env_path
    w = shutil.which("ngrok")
    if w:
        return w
    winget = _ngrok_winget_exe()
    if winget:
        return winget
    raise FileNotFoundError(
        "ngrok not found on PATH. Install from https://ngrok.com/download "
        "then run: ngrok config add-authtoken <token>. "
        "Or set NGROK to the full path to the ngrok executable."
    )


def _ngrok_config_file_hint_present() -> bool:
    """Best-effort authtoken detection when `ngrok config check` is unavailable."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            candidates.append(Path(local) / "ngrok" / "ngrok.yml")
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            candidates.append(Path(appdata) / "ngrok" / "ngrok.yml")
        candidates.append(Path.home() / ".ngrok2" / "ngrok.yml")
    else:
        candidates.extend(
            (
                Path.home() / ".config" / "ngrok" / "ngrok.yml",
                Path.home() / ".ngrok" / "ngrok.yml",
                Path.home() / ".ngrok2" / "ngrok.yml",
            )
        )
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"(?im)^\s*authtoken\s*:", text):
            return True
    return False


def _ngrok_ready() -> bool:
    """True if ngrok is on PATH / NGROK and appears authenticated (preflight only)."""
    try:
        exe = _ngrok_exe()
    except FileNotFoundError:
        return False
    try:
        r = subprocess.run(
            [exe, "config", "check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            return True
        combined = (r.stderr or "") + (r.stdout or "")
        if "unknown command" in combined.lower() or "invalid command" in combined.lower():
            return _ngrok_config_file_hint_present()
        return False
    except (subprocess.TimeoutExpired, OSError):
        return _ngrok_config_file_hint_present()


def _ensure_ngrok_authtoken_from_env() -> None:
    """If NGROK_AUTHTOKEN is set in the environment, ensure ngrok's config has it."""
    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if not token:
        return
    try:
        exe = _ngrok_exe()
    except FileNotFoundError:
        print(
            "NGROK_AUTHTOKEN is set but ngrok was not found (PATH / NGROK / WinGet). "
            "Install ngrok or set NGROK to ngrok.exe.\n",
            flush=True,
        )
        return
    if _ngrok_ready():
        return
    r = subprocess.run(
        [exe, "config", "add-authtoken", token],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=45,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        print(
            "Could not apply NGROK_AUTHTOKEN (ngrok config add-authtoken failed).\n"
            + (msg[:800] + "…" if len(msg) > 800 else msg)
            + "\n",
            flush=True,
        )


def _wait_for_tunnel_url(
    proc: subprocess.Popen[str],
    label: str,
    url_patterns: tuple[re.Pattern[str], ...],
    timeout_s: float,
) -> str | None:
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
                if url_seen["url"] is not None:
                    continue
                for pat in url_patterns:
                    m = pat.search(line)
                    if m:
                        with lock:
                            if url_seen["url"] is None:
                                url_seen["url"] = m.group(1).rstrip("/")
                        break
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

    with lock:
        return url_seen["url"]


def _terminate_tunnel_proc(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if proc in _TUNNEL_PROCS:
        _TUNNEL_PROCS.remove(proc)


def _start_cloudflare_tunnel_once(port: int, label: str, timeout_s: float) -> tuple[str | None, subprocess.Popen[str]]:
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
    url = _wait_for_tunnel_url(proc, label, (_TRY_CLOUDFLARE_RE,), timeout_s)
    return url, proc


def _start_cloudflare_tunnel(
    port: int,
    label: str,
    timeout_s: float,
    max_attempts: int,
    backoff_base_s: float,
    backoff_max_s: float,
) -> str:
    last_err = ""
    for attempt in range(max_attempts):
        url, proc = _start_cloudflare_tunnel_once(port, label, timeout_s)
        if url:
            _TUNNEL_PROCS.append(proc)
            return url
        code = proc.poll()
        _terminate_tunnel_proc(proc)
        last_err = f"cloudflared exit={code}"
        if attempt < max_attempts - 1:
            # Exponential backoff + small jitter so retries don't thunder together.
            raw = min(backoff_base_s * (2**attempt), backoff_max_s)
            delay = raw + random.uniform(0, min(4.0, raw * 0.15))
            print(
                f"[{label}] Quick Tunnel did not yield a URL ({last_err}); "
                f"attempt {attempt + 1}/{max_attempts}. Retrying in {delay:.1f}s "
                f"(Cloudflare may be rate-limiting — 429 is common on busy days).\n",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"{label}: no trycloudflare URL after {max_attempts} attempts ({last_err}). "
        "Try again later, use --tunnel ngrok, or set LIVE_RECALL_PHONE_PUBLIC_URL "
        "and LIVE_RECALL_API_PUBLIC_URL if you run tunnels manually."
    )


def _start_ngrok_tunnel(port: int, label: str, timeout_s: float) -> str:
    exe = _ngrok_exe()
    proc = subprocess.Popen(
        [
            exe,
            "http",
            str(port),
            "--log=stdout",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    url = _wait_for_tunnel_url(proc, label, (_NGROK_URL_RE,), timeout_s)
    if not url:
        _terminate_tunnel_proc(proc)
        raise RuntimeError(
            f"{label}: no ngrok HTTPS URL within {timeout_s:.0f}s "
            f"(ngrok exit={proc.poll()}). Is ngrok authenticated?"
        )
    _TUNNEL_PROCS.append(proc)
    return url


def _start_tunnel_for_backend(
    tunnel: str,
    port: int,
    label: str,
    timeout_s: float,
    max_attempts: int,
    backoff_base_s: float,
    backoff_max_s: float,
) -> str:
    if tunnel == "cloudflare":
        return _start_cloudflare_tunnel(port, label, timeout_s, max_attempts, backoff_base_s, backoff_max_s)
    if tunnel == "ngrok":
        return _start_ngrok_tunnel(port, label, timeout_s)

    # auto: prefer ngrok when configured (avoids Cloudflare 429 bursts), else CF → ngrok.
    ngrok_first = _ngrok_ready()
    if ngrok_first:
        try:
            return _start_ngrok_tunnel(port, label, timeout_s)
        except RuntimeError as e:
            print(
                f"\n[{label}] ngrok failed ({e}); trying Cloudflare Quick Tunnel "
                f"(up to {max_attempts} attempts)…\n",
                flush=True,
            )

    try:
        return _start_cloudflare_tunnel(port, label, timeout_s, max_attempts, backoff_base_s, backoff_max_s)
    except RuntimeError as e:
        print(f"\n[{label}] Cloudflare tunnel failed ({e}); trying ngrok fallback…\n", flush=True)
        try:
            return _start_ngrok_tunnel(port, label, timeout_s)
        except FileNotFoundError as ne:
            raise RuntimeError(
                f"{label}: Cloudflare quick tunnel failed after retries and ngrok was not found. "
                "Install ngrok (https://ngrok.com/download), run `ngrok config add-authtoken …`, "
                "or set LIVE_RECALL_PHONE_PUBLIC_URL and LIVE_RECALL_API_PUBLIC_URL if you run tunnels manually."
            ) from ne


def _cleanup(*_args: object) -> None:
    global _STACK_PROC
    if _STACK_PROC and _STACK_PROC.poll() is None:
        _STACK_PROC.terminate()
        try:
            _STACK_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _STACK_PROC.kill()
    for p in list(_TUNNEL_PROCS):
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    _TUNNEL_PROCS.clear()


def main() -> int:
    os.chdir(REPO_ROOT)
    _load_env()

    parser = argparse.ArgumentParser(
        description="HTTPS tunnels + LiveRecall dev stack for phone (one link with backend preset)."
    )
    parser.add_argument("--no-dashboard", action="store_true", help="Skip Next.js (port 3000).")
    parser.add_argument("--no-phone", action="store_true", help="Skip static server (not recommended).")
    parser.add_argument(
        "--skip-sleep",
        action="store_true",
        help="Do not wait after starting backend before worker (passed through to dev_stack).",
    )
    parser.add_argument(
        "--tunnel",
        choices=("auto", "cloudflare", "ngrok"),
        default=os.environ.get("LIVE_RECALL_TUNNEL", "auto"),
        help="Tunnel backend: auto (ngrok first when authenticated, else Cloudflare retries then ngrok), "
        "cloudflare only, or ngrok only. Override env LIVE_RECALL_TUNNEL.",
    )
    parser.add_argument(
        "--tunnel-timeout",
        type=float,
        default=float(os.environ.get("LIVE_RECALL_TUNNEL_TIMEOUT", "120")),
        help="Seconds to wait for each tunnel process to print a URL (default 120).",
    )
    parser.add_argument(
        "--tunnel-retries",
        type=int,
        default=int(os.environ.get("LIVE_RECALL_TUNNEL_RETRIES", "6")),
        help="Cloudflare Quick Tunnel attempts before giving up or switching to ngrok (auto mode).",
    )
    parser.add_argument(
        "--tunnel-backoff",
        type=float,
        default=float(os.environ.get("LIVE_RECALL_TUNNEL_BACKOFF", "8")),
        help="Initial backoff seconds between Cloudflare retries (doubles each attempt, capped).",
    )
    parser.add_argument(
        "--tunnel-backoff-max",
        type=float,
        default=float(os.environ.get("LIVE_RECALL_TUNNEL_BACKOFF_MAX", "90")),
        help="Maximum backoff seconds between Cloudflare retries.",
    )
    args = parser.parse_args()

    global _STACK_PROC

    def _on_signal(_signum: int, _frame: object | None) -> None:
        _cleanup()
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    phone_override = os.environ.get("LIVE_RECALL_PHONE_PUBLIC_URL", "").strip().rstrip("/")
    api_override = os.environ.get("LIVE_RECALL_API_PUBLIC_URL", "").strip().rstrip("/")

    if phone_override and api_override:
        backend_q = quote(api_override, safe="")
        one_link = f"{phone_override}/glasses.html?backend={backend_q}"
        print(
            "\nLiveRecall phone demo — using LIVE_RECALL_PHONE_PUBLIC_URL / "
            "LIVE_RECALL_API_PUBLIC_URL (no tunnel processes started).\n",
            flush=True,
        )
        print(
            "\n"
            + "=" * 72
            + "\n"
            "  OPEN THIS ON YOUR PHONE (backend URL is embedded):\n\n"
            f"    {one_link}\n\n"
            "  Dashboard (laptop):  http://localhost:3000\n"
            + "=" * 72
            + "\n\n"
            "Starting dev stack (Ctrl+C stops stack)…\n",
            flush=True,
        )
        stack_cmd = [sys.executable, str(REPO_ROOT / "scripts" / "dev_stack.py")]
        if args.no_dashboard:
            stack_cmd.append("--no-dashboard")
        if args.no_phone:
            stack_cmd.append("--no-phone")
        if args.skip_sleep:
            stack_cmd.append("--skip-sleep")
        _STACK_PROC = subprocess.Popen(
            stack_cmd,
            cwd=str(REPO_ROOT),
            env=os.environ.copy(),
        )
        code = _STACK_PROC.wait()
        _cleanup()
        return int(code or 0)

    tunnel_mode = args.tunnel.strip().lower()
    if tunnel_mode not in ("auto", "cloudflare", "ngrok"):
        tunnel_mode = "auto"

    _ensure_ngrok_authtoken_from_env()

    if tunnel_mode == "auto":
        tunnel_hint = (
            "ngrok first (authenticated)"
            if _ngrok_ready()
            else "Cloudflare Quick Tunnel first (429/backoff), then ngrok if configured"
        )
    else:
        tunnel_hint = tunnel_mode
    print(
        f"\nLiveRecall phone demo — starting tunnels ({tunnel_mode}: {tunnel_hint}).\n"
        "(Origin :8080 / :8000 need not be up yet; tunnels retry until dev_stack listens.)\n",
        flush=True,
    )

    phone_base = _start_tunnel_for_backend(
        tunnel_mode,
        8080,
        "tunnel-phone",
        args.tunnel_timeout,
        max(1, args.tunnel_retries),
        max(1.0, args.tunnel_backoff),
        max(args.tunnel_backoff, args.tunnel_backoff_max),
    )
    api_base = _start_tunnel_for_backend(
        tunnel_mode,
        8000,
        "tunnel-backend",
        args.tunnel_timeout,
        max(1, args.tunnel_retries),
        max(1.0, args.tunnel_backoff),
        max(args.tunnel_backoff, args.tunnel_backoff_max),
    )

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
