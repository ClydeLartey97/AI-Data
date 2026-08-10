"""Build a one-click macOS launcher on the current user's Desktop."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = Path.home() / "venvs" / "national-grid" / "bin" / "python"
LAUNCH = PROJECT / "scripts" / "launch.py"
OUTPUT = Path.home() / "Desktop" / "Grid-Aware Scheduler.app"


def quoted(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def main() -> None:
    if not PYTHON.exists():
        raise SystemExit(f"Python environment not found at {PYTHON}")
    source = (
        f'do shell script quoted form of "{quoted(PYTHON)}" & " " & '
        f'quoted form of "{quoted(LAUNCH)}"\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".applescript", encoding="utf-8", delete=False
    ) as handle:
        handle.write(source)
        temporary = Path(handle.name)
    try:
        subprocess.run(
            ["osacompile", "-o", str(OUTPUT), str(temporary)], check=True
        )
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Installed {OUTPUT}")


if __name__ == "__main__":
    main()
