"""Start the sibling National Grid Tool on demand, so the menu can open it.

The tool is a separate Streamlit app in ``../National-Grid-Tool``. It is only
launched when someone asks for it, reuses an instance that is already up, and
binds to loopback like this server does.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[2] / "National-Grid-Tool"
PORTS = range(8501, 8511)
STARTUP_SECONDS = 45

_lock = threading.Lock()


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/_stcore/health", timeout=0.8
        ) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def _free(port: int) -> bool:
    import socket

    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def ensure_running() -> str:
    """Return the tool's URL, starting it first if nothing is serving it."""
    with _lock:
        for port in PORTS:
            if _healthy(port):
                return f"http://localhost:{port}"
        if not (TOOL_DIR / "HomePage.py").exists():
            raise RuntimeError(f"National Grid Tool not found at {TOOL_DIR}")
        port = next((p for p in PORTS if _free(p)), None)
        if port is None:
            raise RuntimeError("no local port free between 8501 and 8510")

        runtime = TOOL_DIR / ".runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "streamlit.log").open("ab", buffering=0) as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "HomePage.py",
                 "--server.port", str(port), "--server.address", "127.0.0.1",
                 "--server.headless", "true",
                 "--browser.gatherUsageStats", "false"],
                cwd=TOOL_DIR,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            if _healthy(port):
                return f"http://localhost:{port}"
            if process.poll() is not None:
                break
            time.sleep(0.25)
        raise RuntimeError(
            f"National Grid Tool did not start; see {runtime / 'streamlit.log'}"
        )
