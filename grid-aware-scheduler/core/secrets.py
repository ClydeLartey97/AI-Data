"""Local API keys, read from a gitignored file into the environment.

Several data sources this project needs are free but token-gated —
Renewables.ninja today, PJM and ERCOT when those adapters land. Keeping them in
a file rather than exported by hand means a key survives opening a new shell,
and means the one place a key ever gets written is a path git refuses to track.

Deliberately stdlib. The whole product has three dependencies and a dotenv
parser is twenty lines, so adding a library to read `KEY=VALUE` would be a poor
trade.

**Existing environment variables always win.** `setdefault` is used rather than
assignment so that a key exported in the shell, injected by CI, or supplied by
a secret manager is never silently overridden by a stale local file.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path | None = None) -> list[str]:
    """Load ``KEY=VALUE`` lines into ``os.environ``. Returns the names set.

    Never returns or logs a value — only which keys were found — because the
    obvious way to leak a key is to print it while confirming it loaded.
    """
    env_file = path or DEFAULT_ENV_FILE
    if not env_file.is_file():
        return []

    loaded = []
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        # Strip one layer of matching quotes, so a pasted value that arrived
        # wrapped in them does not become part of the token.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)
            loaded.append(key)
    return loaded


def describe(*names: str) -> dict[str, bool]:
    """Which of ``names`` are set. Presence only — never the value."""
    load_env_file()
    return {name: bool(os.environ.get(name)) for name in names}
