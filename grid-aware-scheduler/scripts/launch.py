"""Start or reveal the local product without opening a terminal window."""
from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = Path.home() / "venvs" / "national-grid" / "bin" / "python"
RUNTIME = PROJECT / ".runtime"
PORTS = range(8766, 8786)


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/health", timeout=0.8
        ) as response:
            payload = json.loads(response.read())
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def available(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def reveal(port: int) -> None:
    subprocess.run(["open", f"http://localhost:{port}"], check=True)


def notify(message: str) -> None:
    escaped = message.replace('"', '\\"')
    subprocess.run([
        "osascript", "-e",
        f'display notification "{escaped}" with title "Grid-Aware Scheduler"',
    ], check=False)


def main() -> int:
    for port in PORTS:
        if healthy(port):
            reveal(port)
            return 0

    if not PYTHON.exists():
        notify(f"Python environment not found at {PYTHON}")
        return 1

    port = next((candidate for candidate in PORTS if available(candidate)), None)
    if port is None:
        notify("No local port is available between 8766 and 8785")
        return 1

    RUNTIME.mkdir(parents=True, exist_ok=True)
    log_path = RUNTIME / "server.log"
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [str(PYTHON), "-m", "app.serve", "--port", str(port), "--no-open"],
            cwd=PROJECT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (RUNTIME / "server.pid").write_text(str(process.pid), encoding="utf-8")
    (RUNTIME / "server.port").write_text(str(port), encoding="utf-8")

    for _ in range(100):
        if healthy(port):
            reveal(port)
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.1)
    notify(f"The server did not start. See {log_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
