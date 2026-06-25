#!/usr/bin/env python3
"""Start the DemoBuilder backend and frontend together (one command).

    python run.py

  backend  -> http://localhost:8000   (FastAPI + AG-UI, Python-only)
  frontend -> http://localhost:5173   (open this in your browser)

Press Ctrl+C to stop both. Cross-platform (Windows / macOS / Linux).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
BACKEND_PORT = os.environ.get("BACKEND_PORT", "8000")


def venv_python() -> str:
    """Prefer the project venv interpreter; fall back to the current one."""
    for candidate in (
        ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        ROOT / ".venv" / "bin" / "python",          # macOS / Linux
    ):
        if candidate.exists():
            return str(candidate)
    print(
        "! .venv not found. Create it first:\n"
        "    python -m venv .venv\n"
        "    .venv/Scripts/pip install -r backend/requirements.txt   (Windows)\n"
        "    .venv/bin/pip install -r backend/requirements.txt       (macOS/Linux)\n"
    )
    return sys.executable


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # npm spawns a child node process; kill the whole tree.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()


def main() -> int:
    if not (FRONTEND_DIR / "node_modules").exists():
        print("! frontend/node_modules missing — run: npm --prefix frontend install\n")

    python = venv_python()
    npm = "npm.cmd" if os.name == "nt" else "npm"

    backend_cmd = [
        python, "-m", "uvicorn", "app.main:app",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", BACKEND_PORT,
    ]
    frontend_cmd = [npm, "--prefix", str(FRONTEND_DIR), "run", "dev"]

    print("DemoBuilder")
    print(f"  backend  -> http://localhost:{BACKEND_PORT}")
    print("  frontend -> http://localhost:5173   <-- open this in your browser")
    print("  (Ctrl+C to stop)\n")

    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen(backend_cmd, cwd=str(ROOT)))
        procs.append(subprocess.Popen(frontend_cmd, cwd=str(ROOT)))
        while all(p.poll() is None for p in procs):
            time.sleep(0.4)
        exited = next(p for p in procs if p.poll() is not None)
        print(f"\n! a process exited (code {exited.returncode}); shutting down the other…")
        return exited.returncode or 0
    except KeyboardInterrupt:
        print("\nstopping…")
        return 0
    finally:
        for p in procs:
            kill_tree(p)
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
